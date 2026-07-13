"""Discover GDBs under project ``data/`` and list layers for land import preview."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import geopandas as gpd
import pyogrio
from pyproj import Transformer

from peaky_finders.core.preset import LandLayerEntry, slugify_files_segment

_PREVIEW_SIMPLIFY_TOLERANCE_DEG = 0.0005
_PREVIEW_MAX_FEATURES = 500
_PREVIEW_CACHE_VERSION = "v2"
_FIELD_VALUES_MAX = 100


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


def _column_sort_key(name: str) -> tuple[int, str]:
    u = name.upper()
    if u in ("OBJECTID", "FID", "FID_"):
        return (-2, name)
    if u == "NAME":
        return (0, name)
    if u == "ABBR":
        return (1, name)
    if "NAME" in u or u == "LABEL":
        return (2, name)
    if any(x in u for x in ("STATUS", "TYPE", "CLASS", "CATEGORY", "AGENCY")):
        return (3, name)
    return (10, name)


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


def _field_names_from_info(info: dict[str, Any]) -> list[str]:
    fields_raw = info.get("fields")
    if fields_raw is None:
        return []
    return [str(name) for name in list(fields_raw)]


def list_layer_fields(gdb_path: Path, layer: str) -> dict[str, Any]:
    """Return non-geometry field names and row count for a GDB layer."""
    path = Path(gdb_path).expanduser().resolve()
    layer_name = str(layer).strip()
    info = pyogrio.read_info(path, layer=layer_name)
    field_names = _field_names_from_info(info)
    dtypes_info = info.get("dtypes")
    dtype_map: dict[str, str] = {}
    if isinstance(dtypes_info, dict):
        dtype_map = {str(k): str(v) for k, v in dtypes_info.items()}
    fields: list[dict[str, str]] = []
    for name in sorted(field_names, key=_column_sort_key):
        field_name = str(name)
        dtype = dtype_map.get(field_name, "unknown")
        fields.append({"name": field_name, "dtype": dtype})
    return {
        "layer": layer_name,
        "rowCount": int(info.get("features") or 0),
        "fields": fields,
    }


def list_field_values(
    gdb_path: Path,
    layer: str,
    field: str,
    *,
    max_values: int = _FIELD_VALUES_MAX,
) -> dict[str, Any]:
    """Distinct string values (+ counts) for one attribute column."""
    path = Path(gdb_path).expanduser().resolve()
    layer_name = str(layer).strip()
    field_name = str(field).strip()
    if not field_name:
        raise ValueError("field is required")
    info = pyogrio.read_info(path, layer=layer_name)
    available = set(_field_names_from_info(info))
    if field_name not in available:
        raise ValueError(f"unknown field {field_name!r} on layer {layer_name!r}")

    df = pyogrio.read_dataframe(path, layer=layer_name, columns=[field_name], read_geometry=False)
    if df.empty:
        return {"field": field_name, "values": [], "truncated": False}

    series = df[field_name]
    counts = series.astype(str).str.strip().replace({"nan": "", "None": ""})
    counts = counts[counts != ""]
    value_counts = counts.value_counts()
    rows: list[dict[str, Any]] = []
    truncated = len(value_counts) > max_values
    for value, count in value_counts.head(max_values).items():
        rows.append({"value": str(value), "count": int(count)})
    return {"field": field_name, "values": rows, "truncated": truncated}


def ogr_sql_literal(value: str) -> str:
    s = str(value).replace("'", "''")
    return f"'{s}'"


def ogr_where_for_land_layer(entry: LandLayerEntry) -> str | None:
    """OGR WHERE: optional (include OR …) AND NOT exclude AND …."""
    parts: list[str] = []
    for filt in entry.include:
        ors = [f"{filt.field} = {ogr_sql_literal(val)}" for val in filt.values]
        parts.append("(" + " OR ".join(ors) + ")")
    for filt in entry.exclude:
        for val in filt.values:
            parts.append(f"NOT ({filt.field} = {ogr_sql_literal(val)})")
    if not parts:
        return None
    return " AND ".join(parts)


def _json_safe_value(value: Any) -> str | int | float | bool | None:
    if value is None:
        return None
    try:
        import pandas as pd

        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, (str,)):
        s = value.strip()
        return s or None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


def gdf_to_feature_collection_geojson(
    gdf: gpd.GeoDataFrame,
    *,
    simplify_tolerance_deg: float = 0.0,
    label_field: str | None = None,
    style_field: str | None = None,
    properties: list[str] | None = None,
) -> dict[str, Any]:
    """Serialize GeoJSON with JSON-safe properties for map styling."""
    if gdf.empty:
        return {"type": "FeatureCollection", "features": []}

    work = gdf.copy()
    if simplify_tolerance_deg > 0:
        work["geometry"] = work.geometry.simplify(simplify_tolerance_deg, preserve_topology=True)

    prop_cols: list[str] = []
    if properties:
        for col in properties:
            if col in work.columns and col not in prop_cols:
                prop_cols.append(col)
    if label_field and label_field in work.columns and label_field not in prop_cols:
        prop_cols.append(label_field)
    if style_field and style_field in work.columns and style_field not in prop_cols:
        prop_cols.append(style_field)

    features: list[dict[str, Any]] = []
    for _, row in work.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        props: dict[str, Any] = {}
        if label_field and label_field in work.columns:
            label_val = _json_safe_value(row[label_field])
            if label_val is not None:
                props["label"] = label_val
        if style_field and style_field in work.columns:
            style_val = _json_safe_value(row[style_field])
            if style_val is not None:
                props["style_key"] = str(style_val)
        else:
            props["style_key"] = "__default__"
        for col in prop_cols:
            if col in (label_field, style_field):
                continue
            safe = _json_safe_value(row[col])
            if safe is not None:
                props[col] = safe
        features.append(
            {
                "type": "Feature",
                "geometry": json.loads(gpd.GeoSeries([geom], crs=work.crs).to_json())["features"][0][
                    "geometry"
                ],
                "properties": props,
            }
        )
    return {"type": "FeatureCollection", "features": features}


def read_land_layer_gdf(
    gdb_path: Path,
    entry: LandLayerEntry,
    *,
    max_features: int | None = None,
) -> gpd.GeoDataFrame:
    path = Path(gdb_path).expanduser().resolve()
    where = ogr_where_for_land_layer(entry)
    kwargs: dict[str, Any] = {"layer": entry.name, "read_geometry": True}
    if where:
        kwargs["where"] = where
    if max_features is not None:
        kwargs["max_features"] = max_features
    gdf = pyogrio.read_dataframe(path, **kwargs)
    if gdf.empty:
        return gdf
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
    else:
        gdf = gdf.to_crs("EPSG:4326")
    return gdf


def layer_entry_geojson(
    gdb_path: Path,
    entry: LandLayerEntry,
    *,
    max_features: int | None = None,
    simplify_tolerance_deg: float = 0.0,
) -> dict[str, Any]:
    gdf = read_land_layer_gdf(gdb_path, entry, max_features=max_features)
    if gdf.empty:
        return {"type": "FeatureCollection", "features": []}
    if simplify_tolerance_deg > 0:
        gdf = gdf.copy()
        gdf["geometry"] = gdf.geometry.simplify(simplify_tolerance_deg, preserve_topology=True)
    return gdf_to_feature_collection_geojson(
        gdf,
        label_field=entry.label_field,
        style_field=entry.style_field,
    )


def layer_preview_geojson(
    gdb_path: Path,
    layer: str,
    *,
    entry: LandLayerEntry | None = None,
    max_features: int = _PREVIEW_MAX_FEATURES,
    simplify_tolerance_deg: float = _PREVIEW_SIMPLIFY_TOLERANCE_DEG,
) -> dict[str, Any]:
    """Lightweight FeatureCollection for import modal preview."""
    if entry is not None:
        return layer_entry_geojson(
            gdb_path,
            entry,
            max_features=max_features,
            simplify_tolerance_deg=simplify_tolerance_deg,
        )
    path = Path(gdb_path).expanduser().resolve()
    layer_name = str(layer).strip()
    gdf = pyogrio.read_dataframe(path, layer=layer_name, max_features=max_features, read_geometry=True)
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


def land_layer_entry_digest(entry: LandLayerEntry) -> str:
    payload = json.dumps(entry.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def serialize_layer_info(info: GdbLayerInfo) -> dict[str, Any]:
    minx, miny, maxx, maxy = info.bbox
    return {
        "name": info.name,
        "geometry": info.geometry,
        "count": info.count,
        "bbox": [minx, miny, maxx, maxy],
    }


def resolved_land_preview_cache_dir(project_dir: Path) -> Path:
    return Path(project_dir).expanduser().resolve() / ".peaky" / "cache" / "land" / "preview"


def _preview_cache_digest(gdb_path: Path, entry: LandLayerEntry | None, layer: str) -> str:
    stat = gdb_path.stat()
    spec = land_layer_entry_digest(entry) if entry is not None else ""
    payload = (
        f"{_PREVIEW_CACHE_VERSION}|{gdb_path}|{layer}|{spec}|{stat.st_mtime_ns}|{stat.st_size}"
        f"|{_PREVIEW_MAX_FEATURES}|{_PREVIEW_SIMPLIFY_TOLERANCE_DEG}"
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _preview_cache_path(cache_root: Path, digest: str, layer_key: str) -> Path:
    safe_layer = slugify_files_segment(layer_key) or "layer"
    return cache_root / digest / f"{safe_layer}.geojson"


def ensure_layer_preview_geojson(
    project_dir: Path,
    gdb_path: Path,
    layer: str,
    *,
    entry: LandLayerEntry | None = None,
) -> dict[str, Any]:
    """Build or reuse cached preview GeoJSON for import/edit modals."""
    path = Path(gdb_path).expanduser().resolve()
    layer_name = str(layer).strip()
    if not layer_name:
        raise ValueError("layer is required")
    layer_key = entry.layer_key() if entry is not None else slugify_files_segment(layer_name) or "layer"
    cache_root = resolved_land_preview_cache_dir(project_dir)
    digest = _preview_cache_digest(path, entry, layer_name)
    out_path = _preview_cache_path(cache_root, digest, layer_key)
    if out_path.is_file():
        try:
            raw = json.loads(out_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            raw = None
        if isinstance(raw, dict) and raw.get("type") == "FeatureCollection":
            return raw
    geojson = layer_preview_geojson(path, layer_name, entry=entry)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(geojson) + "\n", encoding="utf-8")
    return geojson
