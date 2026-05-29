"""GDAL/OGR helpers for quiet, predictable vector writes."""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

import geopandas as gpd
import pandas as pd

try:
    from osgeo import gdal
except ImportError:  # pragma: no cover
    gdal = None  # type: ignore[assignment]


@contextmanager
def gdal_quiet(*, skip_organize_polygons: bool = True) -> Iterator[None]:
    """Suppress GDAL warning spam during bulk ``to_file`` (type coercion, organizePolygons, etc.)."""
    pushed = False
    old_org: str | None = None
    if gdal is not None:
        gdal.PushErrorHandler("CPLQuietErrorHandler")
        pushed = True
    if skip_organize_polygons:
        old_org = os.environ.get("OGR_ORGANIZE_POLYGONS")
        os.environ["OGR_ORGANIZE_POLYGONS"] = "SKIP"
    try:
        yield
    finally:
        if skip_organize_polygons:
            if old_org is None:
                os.environ.pop("OGR_ORGANIZE_POLYGONS", None)
            else:
                os.environ["OGR_ORGANIZE_POLYGONS"] = old_org
        if pushed:
            gdal.PopErrorHandler()


def coerce_gdf_columns_for_ogr_write(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Stringify attributes so OGR does not warn on GUIDs / codes in numeric schema slots."""
    if gdf.empty:
        return gdf
    g = gdf.copy()
    for col in g.columns:
        if col == "geometry":
            continue
        g[col] = g[col].map(lambda v: "" if pd.isna(v) else str(v))
    return g
