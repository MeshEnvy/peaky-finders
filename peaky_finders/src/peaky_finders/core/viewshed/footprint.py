"""Read unioned SPLAT ``splat.gpkg`` footprint geometry from GeoPackage."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
from shapely import make_valid
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union


def read_coverage_footprint(path: Path) -> BaseGeometry | None:
    """Return EPSG:4326 footprint union from a coverage GPKG, or None if missing/empty."""
    p = Path(path)
    if not p.is_file():
        return None
    gdf = gpd.read_file(p)
    if gdf.empty:
        return None
    raw = unary_union(list(gdf.geometry))
    if raw.is_empty:
        return None
    g = make_valid(raw) if not raw.is_valid else raw
    return g if not g.is_empty else None
