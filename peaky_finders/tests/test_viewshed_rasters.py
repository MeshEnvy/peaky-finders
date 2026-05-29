"""Viewshed splat.png raster metadata for web map."""

from __future__ import annotations

from peaky_finders.web.viewshed_rasters import latlonbox_image_coordinates


def test_latlonbox_image_coordinates_axis_aligned() -> None:
    box = {"north": 40.0, "south": 39.0, "east": -115.0, "west": -116.0, "rotation": 0.0}
    coords = latlonbox_image_coordinates(box)
    assert len(coords) == 4
    # top-left, top-right, bottom-right, bottom-left [lon, lat]
    assert coords[0] == [-116.0, 40.0]
    assert coords[1] == [-115.0, 40.0]
    assert coords[2] == [-115.0, 39.0]
    assert coords[3] == [-116.0, 39.0]
