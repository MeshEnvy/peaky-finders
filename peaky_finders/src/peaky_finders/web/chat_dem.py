"""Skadi DEM queries for the web chat agent (project AOI / extent)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from shapely.geometry import box
from shapely.geometry.base import BaseGeometry

from peaky_finders.bundle_build import load_composite_aoi_polygon
from peaky_finders.pairwise_dem_peak import global_max_skadi_elevation_in_polygon
from peaky_finders.sites_job import Preset, load_preset, peaky_projects_dir, resolved_preset_bundle_data_dir
from peaky_finders.web.dem_contours import resolved_project_dem_dir
from peaky_finders.web.geocode import GeocodeError, reverse_geocode_label
from peaky_finders.web.projects import project_context

M_PER_FT = 3.280839895

# Process-local cache: full-state AOI scans are expensive; repeat chat turns reuse results.
_dem_highest_by_project: dict[str, dict[str, Any]] = {}


def clear_dem_highest_cache() -> None:
    _dem_highest_by_project.clear()


def _meters_to_feet(m: float) -> float:
    return float(m) * M_PER_FT


def project_dem_search_geometry(
    *,
    project_slug: str,
    preset_path: Path,
    preset: Preset,
) -> tuple[BaseGeometry, str]:
    """Geometry to scan and a short scope label for tool results."""
    if preset.bundle and preset.bundle.aoi:
        try:
            data_dir = resolved_preset_bundle_data_dir(preset_path=preset_path, preset=preset)
            poly = load_composite_aoi_polygon(preset.bundle, data_dir)
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
        "no project AOI or site extent available — configure bundle.aoi or add sites to the preset"
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
                "no Skadi DEM tiles for this project — run a build that populates "
                "projects/<slug>/build/dem or configure the global Skadi mirror"
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
