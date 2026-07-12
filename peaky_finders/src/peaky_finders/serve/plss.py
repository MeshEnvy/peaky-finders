"""On-demand PLSS (SECDIVID) lookup for ``peaky serve`` coordinate prefetch."""

from __future__ import annotations

from pathlib import Path

from peaky_finders.core.plss.fetch import (
    apply_plss_on_preset,
    loc_plss_resolved,
    loc_stamp,
    plss_for_point,
    read_plss_loc_cache,
    write_plss_loc_cache,
)
from peaky_finders.core.preset import resolved_preset_build_dir


class ServePlssError(Exception):
    """PLSS lookup failed for serve."""


def _cache_base(project_dir: Path) -> Path:
    return resolved_preset_build_dir(project_dir / "config.yaml")


def ensure_plss_for_coords(
    project_dir: Path,
    lat: float,
    lon: float,
    *,
    verbose: bool = False,
) -> dict[str, str]:
    """Resolve PLSS for coordinates, using ``build/plss/by_loc.json`` when possible."""
    if not (-90.0 <= lat <= 90.0):
        raise ServePlssError(f"lat out of bounds: {lat}")
    if not (-180.0 <= lon <= 180.0):
        raise ServePlssError(f"lon out of bounds: {lon}")

    cache_base = _cache_base(project_dir)
    stamp = loc_stamp(lat, lon)
    loc_cache = read_plss_loc_cache(cache_base)
    cached = loc_cache.get(stamp)

    if cached is not None and loc_plss_resolved(cached):
        if verbose:
            print(f"serve plss: cache hit {stamp}", flush=True)
        return {"plss": cached.get("plss", "")}

    if verbose:
        print(f"serve plss: CadNSDI lookup {stamp}", flush=True)
    try:
        plss = plss_for_point(lon, lat)
    except Exception as e:
        raise ServePlssError(f"CadNSDI lookup failed: {e}") from e

    loc_cache[stamp] = {"plss": plss or ""}
    write_plss_loc_cache(cache_base, loc_cache)
    if verbose:
        print(f"serve plss: done {stamp}", flush=True)
    return {"plss": plss or ""}


def apply_plss_from_loc_cache(
    preset_path: Path,
    site_slug: str,
    lat: float,
    lon: float,
) -> str | None:
    """Write cached PLSS onto a preset site/goal when the loc cache has a resolved entry."""
    cache_base = resolved_preset_build_dir(preset_path)
    loc_cache = read_plss_loc_cache(cache_base)
    cached = loc_cache.get(loc_stamp(lat, lon))
    if cached is None or not loc_plss_resolved(cached):
        return None

    plss = cached.get("plss") or None
    apply_plss_on_preset(preset_path, site_slug, plss)
    return plss
