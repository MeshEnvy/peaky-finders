"""Skadi DEM queries for the web chat agent (project AOI / extent)."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from pyproj import Transformer
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry

from peaky_finders.bundle_build import load_composite_aoi_polygon
from peaky_finders.pairwise_dem_peak import global_max_skadi_elevation_in_polygon
from peaky_finders.sites_job import Preset, load_preset, maps_by_type, peaky_projects_dir, resolved_preset_bundle_data_dir
from peaky_finders.web.dem_contours import resolved_project_dem_dir
from peaky_finders.web.geocode import GeocodeError, reverse_geocode_label
from peaky_finders.web.projects import project_context

M_PER_FT = 3.280839895
PEAK_LABEL_WORDS = frozenset({"peak", "mountain", "mount", "summit", "hill", "pico", "butte"})
DEFAULT_PEAK_SNAP_RADIUS_M = 2000.0
MIN_PEAK_SNAP_SHIFT_M = 25.0

# Process-local cache: full-state AOI scans are expensive; repeat chat turns reuse results.
_dem_highest_by_project: dict[str, dict[str, Any]] = {}


def clear_dem_highest_cache() -> None:
    _dem_highest_by_project.clear()


def _meters_to_feet(m: float) -> float:
    return float(m) * M_PER_FT


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_378_137.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    return 2 * r * math.asin(min(1.0, a**0.5))


def _search_box_around_point(lon: float, lat: float, *, radius_m: float) -> box:
    to_m = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    to_ll = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
    x, y = to_m.transform(lon, lat)
    r = max(200.0, float(radius_m))
    corners = [to_ll.transform(x + dx, y + dy) for dx, dy in ((-r, -r), (-r, r), (r, -r), (r, r))]
    lons = [c[0] for c in corners]
    lats = [c[1] for c in corners]
    return box(min(lons), min(lats), max(lons), max(lats))


def looks_like_peak_label(label: str) -> bool:
    tokens = {t.strip(".,;:").lower() for t in str(label or "").replace("-", " ").split()}
    return bool(tokens & PEAK_LABEL_WORDS)


def label_with_elevation_ft(label: str, elev_ft: float | None) -> str:
    base = str(label or "").strip()
    if elev_ft is None or not base:
        return base
    suffix = f"({int(elev_ft)} ft)"
    if suffix in base:
        return base
    return f"{base} {suffix}"


def snap_peak_to_local_dem(
    *,
    project_slug: str,
    lat: float,
    lon: float,
    radius_m: float = DEFAULT_PEAK_SNAP_RADIUS_M,
    max_shift_m: float = DEFAULT_PEAK_SNAP_RADIUS_M,
) -> dict[str, Any] | None:
    """Snap a geocoded peak seed to the highest Skadi cell within ``radius_m``."""
    preset_path = peaky_projects_dir() / project_slug / "config.yaml"
    if not preset_path.is_file():
        return None
    dem_dir = resolved_project_dem_dir(preset_path)
    if dem_dir is None:
        return None

    search = _search_box_around_point(lon, lat, radius_m=radius_m)
    peak = global_max_skadi_elevation_in_polygon(search, dem_dir)
    if peak is None:
        return None

    slon, slat, elev_m = peak
    shift_m = _haversine_m(lat, lon, slat, slon)
    if shift_m > float(max_shift_m):
        return None

    out: dict[str, Any] = {
        "lat": slat,
        "lon": slon,
        "elev_m": round(elev_m, 1),
        "elev_ft": round(_meters_to_feet(elev_m), 0),
        "shift_m": round(shift_m, 1),
        "source": f"Skadi SRTM highest cell within {int(radius_m)} m of geocode seed",
    }
    if shift_m >= MIN_PEAK_SNAP_SHIFT_M:
        out["dem_snapped"] = True
    return out


def project_dem_search_geometry(
    *,
    project_slug: str,
    preset_path: Path,
    preset: Preset,
) -> tuple[BaseGeometry, str]:
    """Geometry to scan and a short scope label for tool results."""
    if maps_by_type(preset.maps, "aoi"):
        try:
            data_dir = resolved_preset_bundle_data_dir(preset_path=preset_path, preset=preset)
            poly = load_composite_aoi_polygon(preset, data_dir)
            if not poly.is_empty:
                return poly, "project bundle AOI"
        except Exception:
            pass

    ctx = project_context(project_slug)
    raw_bbox = ctx.get("bbox")
    if isinstance(raw_bbox, list) and len(raw_bbox) == 4:
        west, south, east, north = (float(raw_bbox[i]) for i in range(4))
        if east > west and north > south:
            return box(west, south, east, north), "project site map extent"

    raise ValueError(
        "no project AOI or site extent available — configure maps with type aoi or add sites to the preset"
    )


def query_project_dem_highest(*, project_slug: str, use_cache: bool = True) -> dict[str, Any]:
    """Highest valid Skadi SRTM cell in the project search area."""
    if use_cache and project_slug in _dem_highest_by_project:
        cached = dict(_dem_highest_by_project[project_slug])
        cached["cached"] = True
        return cached

    preset_path = peaky_projects_dir() / project_slug / "config.yaml"
    if not preset_path.is_file():
        return {"error": f"project preset not found: {project_slug!r}"}

    preset = load_preset(preset_path)
    dem_dir = resolved_project_dem_dir(preset_path)
    if dem_dir is None:
        return {
            "error": (
                "no Skadi DEM tiles for this project — populate "
                "projects/<slug>/build/dem (e.g. via DEM prefetch) or configure the global Skadi mirror"
            ),
        }

    try:
        geom, scope = project_dem_search_geometry(
            project_slug=project_slug,
            preset_path=preset_path,
            preset=preset,
        )
    except ValueError as exc:
        return {"error": str(exc)}

    peak = global_max_skadi_elevation_in_polygon(geom, dem_dir)
    if peak is None:
        return {"error": "no valid elevation samples in project search area", "scope": scope}

    lon, lat, elev_m = peak
    elev_ft = _meters_to_feet(elev_m)

    place_label: str | None = None
    try:
        place_label = reverse_geocode_label(lat, lon)
    except GeocodeError:
        place_label = None

    out: dict[str, Any] = {
        "scope": scope,
        "project": project_slug,
        "lat": lat,
        "lon": lon,
        "elev_m": round(elev_m, 1),
        "elev_ft": round(elev_ft, 0),
        "source": "Skadi SRTM (1 arc-second) highest cell in search polygon",
        "note": (
            "DEM highest point in the project area — not a curated peak list. "
            "Official state high points may differ slightly from SRTM cell centers."
        ),
    }
    if place_label:
        out["place_label"] = place_label
    if use_cache and "error" not in out:
        _dem_highest_by_project[project_slug] = dict(out)
    return out
