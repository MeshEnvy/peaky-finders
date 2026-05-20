"""Skadi DEM global max inside WGS-84 polygon (pairwise overlap pins)."""

from __future__ import annotations

from pathlib import Path

from shapely.geometry import box

from peaky_finders.pairwise_dem_peak import global_max_skadi_elevation_in_polygon


def test_global_max_returns_none_when_mirror_has_no_tiles(tmp_path: Path) -> None:
    g = box(-115.02, 39.01, -115.0, 39.02)
    assert global_max_skadi_elevation_in_polygon(g, tmp_path) is None
