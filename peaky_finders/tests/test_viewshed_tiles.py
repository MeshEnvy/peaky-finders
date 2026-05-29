"""Viewshed XYZ tile rendering tests."""

from __future__ import annotations

import math

import numpy as np

from peaky_finders.web.viewshed_tiles import (
    axis_aligned_bounds,
    render_viewshed_tile,
    zoom_range_for_raster,
)


def _tile_xyz_for_lat_lon(lat: float, lon: float, z: int) -> tuple[int, int, int]:
    n = 2**z
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n)
    return z, x, y


def test_axis_aligned_bounds() -> None:
    box = {"north": 40.0, "south": 39.0, "east": -115.0, "west": -116.0, "rotation": 0.0}
    assert axis_aligned_bounds(box) == [-116.0, 39.0, -115.0, 40.0]


def test_render_viewshed_tile_samples_coverage() -> None:
    rgba = np.zeros((10, 10, 4), dtype=np.uint8)
    rgba[5, 5, :] = [255, 0, 0, 255]
    bounds = {"north": 40.0, "south": 39.0, "east": -115.0, "west": -116.0, "rotation": 0.0}
    z, x, y = _tile_xyz_for_lat_lon(39.5, -115.5, 10)
    tile = render_viewshed_tile(rgba=rgba, bounds=bounds, z=z, x=x, y=y)
    px = np.asarray(tile)
    assert tile.size == (256, 256)
    assert int(px[:, :, 3].sum()) > 0


def test_zoom_range_scales_with_resolution() -> None:
    box = {"north": 40.0, "south": 39.0, "east": -115.0, "west": -116.0, "rotation": 0.0}
    _, low = zoom_range_for_raster(width=500, height=500, bounds=box)
    _, high = zoom_range_for_raster(width=8000, height=8000, bounds=box)
    assert high > low
