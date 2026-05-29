"""Spatial queries over preset ``maps`` for web chat tools."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import geopandas as gpd
from shapely.geometry import Point

from peaky_finders.bundle_clips import eligible_gpkg_path, reference_gpkg_path
from peaky_finders.sites_job import Preset, load_preset, maps_by_type, peaky_projects_dir, resolved_preset_clips_dir


def _preset_path(slug: str) -> Path:
    cfg = peaky_projects_dir() / slug / "config.yaml"
    if not cfg.is_file():
        raise FileNotFoundError(f"project not found: {slug!r}")
    return cfg


def list_general_overlays_catalog(project_slug: str) -> list[dict[str, Any]]:
    preset_path = _preset_path(project_slug)
    preset = load_preset(preset_path)
    clips_root = resolved_preset_clips_dir(preset_path)
    out: list[dict[str, Any]] = []
    for m in maps_by_type(preset.maps, "general_overlay"):
        gpkg = reference_gpkg_path(clips_root, m.id)
        fields: list[str] = []
        if gpkg.is_file():
            try:
                df = gpd.read_file(gpkg, layer="reference", rows=1)
                fields = [str(c) for c in df.columns if c != "geometry"][:20]
            except Exception:
                pass
        out.append(
            {
                "id": m.id,
                "name": m.name,
                "description": m.description,
                "built": gpkg.is_file(),
                "attribute_fields": fields,
            }
        )
    return out


def _overlay_gdf(project_slug: str, map_id: str) -> gpd.GeoDataFrame:
    preset = load_preset(_preset_path(project_slug))
    entry = next((m for m in preset.maps if m.id == map_id), None)
    if entry is None or entry.type != "general_overlay":
        raise KeyError(f"unknown general_overlay map id: {map_id!r}")
    gpkg = reference_gpkg_path(resolved_preset_clips_dir(_preset_path(project_slug)), map_id)
    if not gpkg.is_file():
        raise FileNotFoundError(f"overlay not built: {map_id!r}")
    return gpd.read_file(gpkg, layer="reference")


def query_overlay_at_point(project_slug: str, *, map_id: str, lat: float, lon: float) -> dict[str, Any]:
    gdf = _overlay_gdf(project_slug, map_id)
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    pt = gpd.GeoDataFrame(geometry=[Point(lon, lat)], crs="EPSG:4326").to_crs(gdf.crs)
    hits = gdf[gdf.intersects(pt.geometry.iloc[0])]
    if hits.empty:
        return {"hit": False}
    row = hits.iloc[0]
    props = {k: _json_safe(v) for k, v in row.items() if k != "geometry"}
    return {"hit": True, "attributes": props}


def list_overlay_values(
    project_slug: str,
    *,
    map_id: str,
    attribute: str,
    limit: int = 50,
) -> dict[str, Any]:
    gdf = _overlay_gdf(project_slug, map_id)
    if attribute not in gdf.columns:
        return {
            "error": f"attribute not found: {attribute!r}",
            "fields": [str(c) for c in gdf.columns if c != "geometry"],
        }
    vals = sorted({str(v) for v in gdf[attribute].dropna().unique()})
    return {"attribute": attribute, "values": vals[: max(1, limit)], "total": len(vals)}


def sites_in_overlay(
    project_slug: str,
    *,
    map_id: str,
    attribute: str | None = None,
    value: str | None = None,
) -> dict[str, Any]:
    preset = load_preset(_preset_path(project_slug))
    gdf = _overlay_gdf(project_slug, map_id)
    if attribute and value is not None:
        gdf = gdf[gdf[attribute].astype(str) == str(value)]
    if gdf.empty:
        return {"sites": []}
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    union = gdf.unary_union
    sites_out: list[dict[str, Any]] = []
    for slug, site in preset.sites.items():
        pt = Point(float(site.lon), float(site.lat))
        if union.contains(pt) or union.intersects(pt):
            sites_out.append({"slug": slug, "name": site.name, "lat": site.lat, "lon": site.lon})
    return {"sites": sites_out}


def point_in_eligible(project_slug: str, *, lat: float, lon: float) -> dict[str, Any]:
    preset_path = _preset_path(project_slug)
    gpkg = eligible_gpkg_path(resolved_preset_clips_dir(preset_path))
    if not gpkg.is_file():
        return {"error": "eligible land not built — run maps rebuild", "eligible": False}
    gdf = gpd.read_file(gpkg, layer="eligible")
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    pt_gdf = gpd.GeoDataFrame(geometry=[Point(lon, lat)], crs="EPSG:4326").to_crs(gdf.crs)
    hit = bool(gdf.intersects(pt_gdf.geometry.iloc[0]).any())
    return {"eligible": hit}


def _json_safe(v: Any) -> Any:
    if v is None or isinstance(v, str | int | float | bool):
        return v
    return str(v)


def format_general_overlays_for_prompt(project_slug: str | None) -> str:
    if not project_slug:
        return ""
    try:
        preset = load_preset(_preset_path(project_slug))
    except Exception:
        return ""
    lines = ["General overlay maps (use overlay tools; never invent admin names):"]
    for m in maps_by_type(preset.maps, "general_overlay"):
        lines.append(f"- id={m.id!r} name={m.name!r}: {m.description or '(no description)'}")
    if len(lines) == 1:
        return "No general_overlay maps configured."
    return "\n".join(lines)
