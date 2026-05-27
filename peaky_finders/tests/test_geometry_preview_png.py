"""geometry_preview_png: rasterio-backed flat-map PNG sidecars."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image
from shapely.geometry import Point, box

from peaky_finders.geometry_preview_png import (
    _aabbggrr_to_rgba_u8,
    write_wgs84_geometry_preview_png,
    write_wgs84_geodataframe_preview_png,
)


def test_aabbggrr_to_rgba_u8() -> None:
    assert _aabbggrr_to_rgba_u8("ff0000ff") == (255, 0, 0, 255)
    assert _aabbggrr_to_rgba_u8("66888888") == (136, 136, 136, 102)


def test_write_polygon_preview_png(tmp_path: Path) -> None:
    png = tmp_path / "preview.png"
    write_wgs84_geometry_preview_png(box(-115.2, 35.9, -115.0, 36.1), png)
    assert png.is_file()
    arr = np.asarray(Image.open(png).convert("RGBA"))
    assert arr.shape[2] == 4
    assert arr[:, :, 3].all()
    # Filled interior should differ from white background.
    assert not np.all(arr[:, :, :3] == 255)


def test_write_point_preview_png(tmp_path: Path) -> None:
    png = tmp_path / "point.png"
    write_wgs84_geometry_preview_png(Point(-115.1, 36.0), png)
    assert png.is_file()
    arr = np.asarray(Image.open(png).convert("RGBA"))
    assert np.any(arr[:, :, :3] != 255)


def test_empty_geometry_is_noop(tmp_path: Path) -> None:
    png = tmp_path / "empty.png"
    write_wgs84_geometry_preview_png(None, png)  # type: ignore[arg-type]
    assert not png.exists()


def test_geodataframe_preview_respects_fill_toggle(tmp_path: Path) -> None:
    import geopandas as gpd

    gdf = gpd.GeoDataFrame(geometry=[box(-115.2, 35.9, -115.0, 36.1)], crs="EPSG:4326")
    filled = tmp_path / "filled.png"
    outline = tmp_path / "outline.png"
    write_wgs84_geodataframe_preview_png(gdf, filled, fill_polygons=True)
    write_wgs84_geodataframe_preview_png(gdf, outline, fill_polygons=False, line_width=2.0)
    filled_arr = np.asarray(Image.open(filled).convert("RGB"))
    outline_arr = np.asarray(Image.open(outline).convert("RGB"))
    assert filled_arr.mean() < outline_arr.mean()
