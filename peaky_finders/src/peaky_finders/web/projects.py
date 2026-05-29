"""Discover Peaky projects from ``projects/*/config.yaml``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from shapely.geometry import box
from shapely.geometry.base import BaseGeometry

from peaky_finders.bundle_build import load_composite_aoi_polygon
from peaky_finders.sites_job import SiteType, load_preset, peaky_projects_dir, resolved_preset_bundle_data_dir

_GEOCODE_AOI_CACHE: dict[str, tuple[float, list[float] | None, BaseGeometry | None]] = {}


def list_projects() -> list[dict[str, Any]]:
    root = peaky_projects_dir()
    out: list[dict[str, Any]] = []
    if not root.is_dir():
        return out
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        cfg = entry / "config.yaml"
        if not cfg.is_file():
            yml = entry / "config.yml"
            cfg = yml if yml.is_file() else cfg
        if not cfg.is_file():
            continue
        try:
            preset = load_preset(cfg)
        except Exception:
            continue
        strategy = "land-grab"
        goal_count = sum(1 for e in preset.sites.values() if e.type == SiteType.GOAL)
        if preset.bundle and preset.bundle.site_suggestions:
            ss = preset.bundle.site_suggestions
            strategy = ss.strategy.value
            if strategy == "mesh-grow-ai":
                goal_count += len(ss.mesh_grow_ai.goals)
            elif strategy == "mesh-backbone":
                goal_count += len(ss.mesh_backbone.goals)
        out.append(
            {
                "slug": entry.name,
                "path": str(cfg.resolve()),
                "strategy": strategy,
                "goals": goal_count,
                "site_count": len(preset.sites),
            }
        )
    return out


def project_context(slug: str) -> dict[str, Any]:
    root = peaky_projects_dir()
    cfg = root / slug / "config.yaml"
    if not cfg.is_file():
        raise FileNotFoundError(f"project not found: {slug!r}")
    preset = load_preset(cfg)
    sites = [
        {
            "slug": s,
            "name": e.name.strip() or s,
            "lat": e.lat,
            "lon": e.lon,
            "type": e.type.value,
        }
        for s, e in sorted(preset.sites.items())
    ]
    goals: list[dict[str, Any]] = [
        {
            "key": slug,
            "label": e.name.strip() or slug,
            "lat": e.lat,
            "lon": e.lon,
        }
        for slug, e in sorted(preset.sites.items())
        if e.type == SiteType.GOAL
    ]
    if preset.bundle and preset.bundle.site_suggestions:
        ss = preset.bundle.site_suggestions
        if ss.strategy.value == "mesh-grow-ai":
            gcfg = ss.mesh_grow_ai.goals
        elif ss.strategy.value == "mesh-backbone":
            gcfg = ss.mesh_backbone.goals
        else:
            gcfg = {}
        for key, ent in gcfg.items():
            goals.append({"key": key, "label": key, "lat": ent.lat, "lon": ent.lon})
    bbox = None
    if sites:
        lats = [s["lat"] for s in sites]
        lons = [s["lon"] for s in sites]
        pad = 0.25
        bbox = [min(lons) - pad, min(lats) - pad, max(lons) + pad, max(lats) + pad]
    return {"slug": slug, "sites": sites, "goals": goals, "bbox": bbox}


def _viewbox_from_bounds(west: float, south: float, east: float, north: float, *, pad: float) -> list[float]:
    return [west - pad, south - pad, east + pad, north + pad]


def project_geocode_aoi(slug: str) -> tuple[list[float] | None, BaseGeometry | None]:
    """WGS-84 viewbox and AOI polygon for geocode bias (bundle AOI, else site extent)."""
    root = peaky_projects_dir()
    cfg = root / slug / "config.yaml"
    if not cfg.is_file():
        return None, None

    mtime = cfg.stat().st_mtime_ns
    cached = _GEOCODE_AOI_CACHE.get(slug)
    if cached and cached[0] == mtime:
        return cached[1], cached[2]

    viewbox: list[float] | None = None
    aoi: BaseGeometry | None = None

    try:
        preset = load_preset(cfg)
    except Exception:
        _GEOCODE_AOI_CACHE[slug] = (mtime, None, None)
        return None, None

    if preset.bundle and preset.bundle.aoi:
        try:
            data_dir = resolved_preset_bundle_data_dir(preset_path=cfg, preset=preset)
            poly = load_composite_aoi_polygon(preset.bundle, data_dir)
            if not poly.is_empty:
                aoi = poly
                west, south, east, north = poly.bounds
                viewbox = _viewbox_from_bounds(west, south, east, north, pad=0.05)
        except Exception:
            pass

    if aoi is None:
        ctx = project_context(slug)
        raw_bbox = ctx.get("bbox")
        if isinstance(raw_bbox, list) and len(raw_bbox) == 4:
            west, south, east, north = (float(raw_bbox[i]) for i in range(4))
            if east > west and north > south:
                viewbox = [west, south, east, north]
                aoi = box(west, south, east, north)

    _GEOCODE_AOI_CACHE[slug] = (mtime, viewbox, aoi)
    return viewbox, aoi


def clear_project_geocode_aoi_cache() -> None:
    _GEOCODE_AOI_CACHE.clear()
