"""In-memory preset cache for ``peaky serve`` (invalidated by ``config.yaml`` mtime)."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from peaky_finders.sites_job import Preset, SiteEntry, load_preset_for_coverage

_cache: dict[str, tuple[float, "ServeProjectContext"]] = {}
_guard = threading.Lock()


@dataclass(frozen=True)
class ServeProjectContext:
    preset: Preset
    repeater_sites: dict[str, SiteEntry]


def _config_mtime(config_path: Path) -> float:
    try:
        return config_path.stat().st_mtime
    except OSError:
        return -1.0


def load_serve_project_context(project_dir: Path) -> ServeProjectContext:
    """Load coverage preset + repeater sites once per ``config.yaml`` revision."""
    root = Path(project_dir).expanduser().resolve()
    config = root / "config.yaml"
    mtime = _config_mtime(config)
    key = str(config)
    with _guard:
        hit = _cache.get(key)
        if hit is not None and hit[0] == mtime:
            return hit[1]

    preset = load_preset_for_coverage(config)
    ctx = ServeProjectContext(preset=preset, repeater_sites=dict(preset.repeaters))
    with _guard:
        _cache[key] = (mtime, ctx)
    return ctx


def reset_serve_preset_cache_for_tests() -> None:
    with _guard:
        _cache.clear()
