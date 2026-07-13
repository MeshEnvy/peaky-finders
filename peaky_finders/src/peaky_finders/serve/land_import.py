"""Discover GDBs under project ``data/`` and list layers for land import preview."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import geopandas as gpd
import pyogrio
from pyproj import Transformer

_PREVIEW_SIMPLIFY_TOLERANCE_DEG = 0.0005
_PREVIEW_MAX_FEATURES = 500


@dataclass(frozen=True)
class GdbLayerInfo:
    name: str
    geometry: str
    count: int
    bbox: tuple[float, float, float, float]  # minx, miny, maxx, maxy WGS84


def list_data_gdbs(project_dir: Path) -> list[str]:
    """Return top-level ``data/*.gdb`` folder names relative to *project_dir*, sorted."""
    root = Path(project_dir).expanduser().resolve()
    data_dir = root / "data"
    if not data_dir.is_dir():
        return []
    out: list[str] = []
    for path in sorted(data_dir.iterdir()):
        if not path.is_dir() or not path.name.lower().endswith(".gdb"):
            continue
        out.append((Path("data") / path.name).as_posix())
    return out


def resolve_land_gdb_path(project_dir: Path, rel_path: str) -> Path:
    """Resolve and validate a GDB path under ``project_dir/data/``."""
    root = Path(project_dir).expanduser().resolve()
    data_root = (root / "data").resolve()
    normalized = str(rel_path or "").strip().replace("\\", "/")
    if not normalized:
        raise ValueError("path is required")
    if normalized.startswith("/") or ".." in Path(normalized).parts:
        raise ValueError("path must be relative to the project directory")
    if not normalized.lower().endswith(".gdb"):
        raise ValueError("path must end with .gdb")
    resolved = (root / normalized).resolve()
    if not resolved.is_dir():
        raise ValueError(f"GDB not found: {normalized}")
    try:
        resolved.relative_to(data_root)
    except ValueError as e:
        raise ValueError("path must be under data/") from e
    return resolved


def _bounds_wgs84_from_layer_info(info: dict[str, Any]) -> tuple[float, float, float, float]:
    """Layer extent from ``read_info`` metadata — no full geometry read."""
    bounds = info.get("total_bounds")
    if not bounds or len(bounds) != 4:
        return (0.0, 0.0, 0.0, 0.0)
    minx, miny, maxx, maxy = (float(v) for v in bounds)
    crs = info.get("crs")
    if crs is None or str(crs).upper() in {"EPSG:4326", "OGC:CRS84"}:
        return (minx, miny, maxx, maxy)
    try:
        transformer = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
        lons: list[float] = []
        lats: list[float] = []
        for x, y in ((minx, miny), (minx, maxy), (maxx, miny), (maxx, maxy)):
            lon, lat = transformer.transform(x, y)
            lons.append(float(lon))
            lats.append(float(lat))
        return (min(lons), min(lats), max(lons), max(lats))
    except Exception:
        return (0.0, 0.0, 0.0, 0.0)


def list_gdb_layers(gdb_path: Path) -> list[GdbLayerInfo]:
    """List layers in a FileGDB with geometry type, feature count, and WGS84 bbox."""
    path = Path(gdb_path).expanduser().resolve()
    layers = pyogrio.list_layers(path)
    out: list[GdbLayerInfo] = []
    for row in layers:
        name = str(row[0])
        geom_type = str(row[1]) if len(row) > 1 else "Unknown"
        try:
            info = pyogrio.read_info(path, layer=name)
            count = int(info.get("features") or 0)
            bbox = _bounds_wgs84_from_layer_info(info)
        except Exception:
            count = 0
            bbox = (0.0, 0.0, 0.0, 0.0)
        out.append(
            GdbLayerInfo(
                name=name,
                geometry=geom_type,
                count=count,
                bbox=bbox,
            )
        )
    return out


def _layer_bbox_wgs84(gdb_path: Path, layer: str) -> tuple[float, float, float, float]:
    try:
        info = pyogrio.read_info(gdb_path, layer=layer)
    except Exception:
        return (0.0, 0.0, 0.0, 0.0)
    return _bounds_wgs84_from_layer_info(info)


def serialize_layer_info(info: GdbLayerInfo) -> dict[str, Any]:
    minx, miny, maxx, maxy = info.bbox
    return {
        "name": info.name,
        "geometry": info.geometry,
        "count": info.count,
        "bbox": [minx, miny, maxx, maxy],
    }


def gdf_to_feature_collection_geojson(
    gdf: gpd.GeoDataFrame,
    *,
    simplify_tolerance_deg: float = 0.0,
) -> dict[str, Any]:
    """Serialize geometry-only GeoJSON (GDB attrs may include non-JSON types)."""
    if gdf.empty:
        return {"type": "FeatureCollection", "features": []}
    work = gdf[["geometry"]].copy()
    if simplify_tolerance_deg > 0:
        work["geometry"] = work.geometry.simplify(simplify_tolerance_deg, preserve_topology=True)
    return json.loads(work.to_json())


def layer_preview_geojson(
    gdb_path: Path,
    layer: str,
    *,
    max_features: int = _PREVIEW_MAX_FEATURES,
    simplify_tolerance_deg: float = _PREVIEW_SIMPLIFY_TOLERANCE_DEG,
) -> dict[str, Any]:
    """Lightweight FeatureCollection for import modal preview."""
    path = Path(gdb_path).expanduser().resolve()
    gdf = pyogrio.read_dataframe(path, layer=layer, max_features=max_features, read_geometry=True)
    if gdf.empty:
        return {"type": "FeatureCollection", "features": []}
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    else:
        gdf = gdf.to_crs("EPSG:4326")
    if simplify_tolerance_deg > 0:
        gdf = gdf.copy()
        gdf["geometry"] = gdf.geometry.simplify(simplify_tolerance_deg, preserve_topology=True)
    return gdf_to_feature_collection_geojson(gdf)
