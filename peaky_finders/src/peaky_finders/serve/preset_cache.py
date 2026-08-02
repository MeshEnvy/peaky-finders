"""In-memory preset cache for ``peaky serve`` (invalidated by ``config.yaml`` mtime)."""

from __future__ import annotations

import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from peaky_finders.core.preset import Preset, SiteEntry, load_preset_for_coverage, normalize_site_tags

_cache: dict[str, tuple[float, "ServeProjectContext"]] = {}
_guard = threading.Lock()


@dataclass(frozen=True)
class ServeProjectContext:
    preset: Preset
    sites: dict[str, SiteEntry]


def _config_mtime(config_path: Path) -> float:
    try:
        return config_path.stat().st_mtime
    except OSError:
        return -1.0


def load_serve_project_context(project_dir: Path) -> ServeProjectContext:
    """Load coverage preset + sites once per ``config.yaml`` revision."""
    root = Path(project_dir).expanduser().resolve()
    config = root / "config.yaml"
    mtime = _config_mtime(config)
    key = str(config)
    with _guard:
        hit = _cache.get(key)
        if hit is not None and hit[0] == mtime:
            return hit[1]

    preset = load_preset_for_coverage(config)
    ctx = ServeProjectContext(preset=preset, sites=dict(preset.sites))
    with _guard:
        _cache[key] = (mtime, ctx)
    return ctx


def patch_serve_project_site_tags(
    project_dir: Path,
    updates: Mapping[str, Sequence[str]],
) -> None:
    """Patch cached site tags after a minimal YAML write (skip full preset reparse)."""
    if not updates:
        return
    root = Path(project_dir).expanduser().resolve()
    config = root / "config.yaml"
    mtime = _config_mtime(config)
    key = str(config)
    with _guard:
        hit = _cache.get(key)
        if hit is None:
            return
        ctx = hit[1]
        for slug, tags in updates.items():
            entry = ctx.sites.get(slug)
            if entry is not None:
                entry.tags = normalize_site_tags(tags)
        _cache[key] = (mtime, ctx)


def patch_serve_project_site(project_dir: Path, site_row: Mapping[str, object]) -> None:
    """Patch one cached site after a minimal YAML write (skip full preset reparse)."""
    slug = str(site_row.get("slug", "")).strip()
    if not slug:
        return
    root = Path(project_dir).expanduser().resolve()
    config = root / "config.yaml"
    mtime = _config_mtime(config)
    key = str(config)
    with _guard:
        hit = _cache.get(key)
        if hit is None:
            return
        ctx = hit[1]
        entry = ctx.sites.get(slug)
        if entry is None:
            return
        if "name" in site_row:
            entry.name = str(site_row["name"])
        if "lat" in site_row and "lon" in site_row:
            entry.loc = (float(site_row["lat"]), float(site_row["lon"]))
        if "tags" in site_row:
            entry.tags = normalize_site_tags(site_row["tags"])  # type: ignore[arg-type]
        if "height_m" in site_row:
            hm = site_row["height_m"]
            entry.height_m = float(hm) if hm is not None else None
        if "plss" in site_row:
            plss = site_row["plss"]
            entry.plss = str(plss).strip() if plss and str(plss).strip() else None
        _cache[key] = (mtime, ctx)


def reset_serve_preset_cache_for_tests() -> None:
    with _guard:
        _cache.clear()
