"""gdal_io attribute coercion."""

from __future__ import annotations

import geopandas as gpd
from shapely.geometry import box

from peaky_finders.gdal_io import coerce_gdf_columns_for_ogr_write


def test_coerce_gdf_columns_stringifies_attributes() -> None:
    gdf = gpd.GeoDataFrame(
        {"ORGCODE": ["NWR", 1], "GIS_ACRES": ["{GUID}", 12.5]},
        geometry=[box(0, 0, 1, 1), box(1, 1, 2, 2)],
        crs="EPSG:4326",
    )
    out = coerce_gdf_columns_for_ogr_write(gdf)
    assert out["ORGCODE"].tolist() == ["NWR", "1"]
    assert out["GIS_ACRES"].tolist() == ["{GUID}", "12.5"]
    assert len(out) == 2
