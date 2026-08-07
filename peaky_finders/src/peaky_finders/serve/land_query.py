"""Point-in-polygon queries against preset land layers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from shapely.geometry import Point
from shapely.strtree import STRtree

from peaky_finders.core.preset import LandLayerEntry, Preset, load_preset
from peaky_finders.serve.land_import import read_land_layer_gdf, resolve_land_source_path


@dataclass(frozen=True)
class LandPointHit:
    """One land polygon containing the query point."""

    properties: dict[str, Any]
    area: float


@dataclass
class LandLayerSpatialIndex:
    """Spatial index for repeated point queries against one land layer."""

    tree: STRtree
    geoms: list
    hits: list[LandPointHit]


def _json_safe_value(value: Any) -> Any:
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
        return int(value)
    if isinstance(value, float):
        return float(value)
    if isinstance(value, str):
        s = value.strip()
        return s or None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


def _row_properties(row: Any, columns: list[str]) -> dict[str, Any]:
    props: dict[str, Any] = {}
    for col in columns:
        if col == "geometry":
            continue
        safe = _json_safe_value(row[col])
        if safe is not None:
            props[col] = safe
    return props


def resolve_land_layer_entry(
    preset: Preset,
    source_id: str,
    layer_name: str | None = None,
) -> LandLayerEntry:
    source = preset.land.sources.get(source_id)
    if source is None:
        raise ValueError(f"unknown land source: {source_id}")
    if layer_name is None:
        if len(source.layers) != 1:
            raise ValueError(
                f"land source {source_id!r} has {len(source.layers)} layers; pass layer_name"
            )
        return source.layers[0]
    for layer in source.layers:
        if layer.name == layer_name:
            return layer
    names = [layer.name for layer in source.layers]
    raise ValueError(f"layer {layer_name!r} not found on {source_id!r}; have {names}")


def load_land_layer_index(
    preset_path: Path,
    source_id: str,
    layer_name: str | None = None,
    *,
    entry: LandLayerEntry | None = None,
) -> LandLayerSpatialIndex:
    """Build a spatial index for one preset land layer."""
    preset_path = Path(preset_path).expanduser().resolve()
    preset = load_preset(preset_path)
    project_dir = preset_path.parent
    source = preset.land.sources.get(source_id)
    if source is None:
        raise ValueError(f"unknown land source: {source_id}")

    layer_entry = entry or resolve_land_layer_entry(preset, source_id, layer_name)
    data_path = resolve_land_source_path(project_dir, source.path)
    gdf = read_land_layer_gdf(data_path, layer_entry)
    if gdf.empty:
        return LandLayerSpatialIndex(tree=STRtree([]), geoms=[], hits=[])

    columns = [str(c) for c in gdf.columns]
    geoms: list = []
    hits: list[LandPointHit] = []
    for _, row in gdf.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        if not geom.is_valid:
            geom = geom.buffer(0)
        geoms.append(geom)
        hits.append(
            LandPointHit(
                properties=_row_properties(row, columns),
                area=float(geom.area),
            )
        )

    return LandLayerSpatialIndex(tree=STRtree(geoms), geoms=geoms, hits=hits)


def query_land_layer_index(
    index: LandLayerSpatialIndex,
    lat: float,
    lon: float,
    *,
    smallest_only: bool = False,
) -> list[LandPointHit]:
    """Return land features containing ``(lat, lon)``."""
    if not index.geoms:
        return []

    pt = Point(float(lon), float(lat))
    matched: list[LandPointHit] = []
    for idx in index.tree.query(pt):
        if index.geoms[idx].contains(pt):
            matched.append(index.hits[idx])

    if not matched:
        return []
    if smallest_only and len(matched) > 1:
        return [min(matched, key=lambda hit: hit.area)]
    return matched


def point_hits(
    preset_path: Path,
    lat: float,
    lon: float,
    source_id: str,
    layer_name: str | None = None,
    *,
    entry: LandLayerEntry | None = None,
    smallest_only: bool = False,
) -> list[dict[str, Any]]:
    """Point query; returns attribute dicts for each matching polygon."""
    index = load_land_layer_index(
        preset_path,
        source_id,
        layer_name,
        entry=entry,
    )
    return [hit.properties for hit in query_land_layer_index(index, lat, lon, smallest_only=smallest_only)]
