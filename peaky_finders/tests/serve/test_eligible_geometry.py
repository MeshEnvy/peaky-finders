"""Eligible land geometry (include − exclude)."""

from __future__ import annotations

import geopandas as gpd
from shapely.geometry import Polygon

from peaky_finders.serve.eligible_land import build_eligible_geometry


def test_build_eligible_geometry_include_only() -> None:
    include = gpd.GeoDataFrame(geometry=[Polygon([(0, 0), (2, 0), (2, 2), (0, 2)])], crs="EPSG:4326")
    exclude = gpd.GeoDataFrame(geometry=[Polygon()], crs="EPSG:4326")
    geom = build_eligible_geometry(include, exclude)
    assert not geom.is_empty
    assert geom.area > 0


def test_build_eligible_geometry_subtracts_exclude() -> None:
    include = gpd.GeoDataFrame(geometry=[Polygon([(0, 0), (4, 0), (4, 4), (0, 4)])], crs="EPSG:4326")
    exclude = gpd.GeoDataFrame(geometry=[Polygon([(1, 1), (3, 1), (3, 3), (1, 3)])], crs="EPSG:4326")
    geom = build_eligible_geometry(include, exclude)
    assert not geom.is_empty
    assert geom.contains(Polygon([(0.2, 0.2), (0.8, 0.2), (0.8, 0.8), (0.2, 0.8)]))
    assert not geom.contains(Polygon([(1.5, 1.5), (2.5, 1.5), (2.5, 2.5), (1.5, 2.5)]))


def test_build_eligible_geometry_empty_include() -> None:
    include = gpd.GeoDataFrame(geometry=[Polygon()], crs="EPSG:4326")
    exclude = gpd.GeoDataFrame(geometry=[Polygon()], crs="EPSG:4326")
    geom = build_eligible_geometry(include, exclude)
    assert geom.is_empty
