"""On-demand PLSS / MLRS lookup for ``peaky serve`` coordinate prefetch."""

from __future__ import annotations

from pathlib import Path

from peaky_finders.plss_mlrs_fetch import (
    _apply_plss_mlrs_on_preset,
    loc_plss_resolved,
    loc_stamp,
    plss_mlrs_for_point,
    read_plss_mlrs_loc_cache,
    write_plss_mlrs_loc_cache,
)
from peaky_finders.sites_job import resolved_preset_build_dir


class ServePlssMlrsError(Exception):
    """PLSS/MLRS lookup failed for serve."""


def _cache_base(project_dir: Path) -> Path:
    return resolved_preset_build_dir(project_dir / "config.yaml")


def ensure_plss_mlrs_for_coords(
    project_dir: Path,
    lat: float,
    lon: float,
    *,
    verbose: bool = False,
) -> dict[str, str]:
    """Resolve PLSS/MLRS for coordinates, using ``build/plss_mlrs/by_loc.json`` when possible."""
    if not (-90.0 <= lat <= 90.0):
        raise ServePlssMlrsError(f"lat out of bounds: {lat}")
    if not (-180.0 <= lon <= 180.0):
        raise ServePlssMlrsError(f"lon out of bounds: {lon}")

    cache_base = _cache_base(project_dir)
    stamp = loc_stamp(lat, lon)
    loc_cache = read_plss_mlrs_loc_cache(cache_base)
    cached = loc_cache.get(stamp)

    if cached is not None and loc_plss_resolved(cached):
        if verbose:
            print(f"serve plss/mlrs: cache hit {stamp}", flush=True)
        return {"plss": cached.get("plss", ""), "mlrs": cached.get("mlrs", "")}

    if verbose:
        print(f"serve plss/mlrs: CadNSDI lookup {stamp}", flush=True)
    try:
        plss, mlrs = plss_mlrs_for_point(lon, lat)
    except Exception as e:
        raise ServePlssMlrsError(f"CadNSDI lookup failed: {e}") from e

    loc_cache[stamp] = {"plss": plss or "", "mlrs": mlrs or ""}
    write_plss_mlrs_loc_cache(cache_base, loc_cache)
    if verbose:
        print(f"serve plss/mlrs: done {stamp}", flush=True)
    return {"plss": plss or "", "mlrs": mlrs or ""}


def apply_plss_mlrs_from_loc_cache(
    preset_path: Path,
    site_slug: str,
    lat: float,
    lon: float,
) -> tuple[str | None, str | None]:
    """Write cached PLSS/MLRS onto a preset site when the loc cache has a resolved entry."""
    cache_base = resolved_preset_build_dir(preset_path)
    loc_cache = read_plss_mlrs_loc_cache(cache_base)
    cached = loc_cache.get(loc_stamp(lat, lon))
    if cached is None or not loc_plss_resolved(cached):
        return None, None

    plss = cached.get("plss") or None
    mlrs = cached.get("mlrs") or None
    _apply_plss_mlrs_on_preset(preset_path, site_slug, plss, mlrs)
    return plss, mlrs
