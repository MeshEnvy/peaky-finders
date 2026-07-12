"""Footprint polygon export from synthetic SPLAT-style PPM."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
from PIL import Image

from peaky_finders.core.preset import DEFAULT_VIEWSHED_POLYGON_STYLE
from peaky_finders.core.viewshed.polygonize import (
    SPLAT_GPKG_NAME,
    SPLAT_KML_NAME,
    VIEWSHED_COVERAGE_KML_STYLE_ID,
    coverage_mask_from_rgba,
    coverage_mask_from_splat_ppm_rgb,
    polygonize_mask,
    write_coverage_polygons,
)


def test_coverage_mask_ppm_non_white_matches_png_rule() -> None:
    rgb = np.zeros((4, 4, 3), dtype=np.uint8)
    rgb[1, 1] = [0, 0, 1]
    rgb[2, 2] = [255, 255, 255]
    m = coverage_mask_from_splat_ppm_rgb(rgb)
    assert m[1, 1] == 1
    assert m[0, 0] == 1  # not white → same as splat.png (opaque)
    assert m[2, 2] == 0  # SPLAT no-signal white


def test_coverage_mask_alpha_and_white() -> None:
    rgba = np.zeros((4, 4, 4), dtype=np.uint8)
    rgba[:] = [255, 255, 255, 0]
    rgba[1:3, 1:3] = [200, 0, 0, 255]
    mask = coverage_mask_from_rgba(rgba)
    assert mask[1, 1] == 1
    assert mask[0, 0] == 0


def test_polygonize_mask_small_block() -> None:
    m = np.zeros((8, 8), dtype=np.uint8)
    m[2:6, 2:6] = 1
    g = polygonize_mask(m)
    assert g is not None
    assert g.area > 0


def test_write_coverage_polygons_writes_gpkg(tmp_path: Path) -> None:
    h, w = 32, 32
    rgb = np.full((h, w, 3), 255, dtype=np.uint8)
    rgb[8:24, 8:24] = [200, 10, 10]
    ppm_path = tmp_path / "output.ppm"
    Image.fromarray(rgb, "RGB").save(ppm_path, format="PPM")

    bbox = {
        "north": 40.1,
        "south": 39.9,
        "east": -105.0,
        "west": -105.2,
        "rotation": 0.0,
    }
    out_gpkg = tmp_path / SPLAT_GPKG_NAME
    out_kml = tmp_path / SPLAT_KML_NAME
    ok = write_coverage_polygons(
        ppm_path=ppm_path,
        bbox=bbox,
        out_gpkg=out_gpkg,
        out_kml=out_kml,
        polygon_style=DEFAULT_VIEWSHED_POLYGON_STYLE,
    )
    assert ok is True
    assert out_gpkg.is_file()

    gdf = gpd.read_file(out_gpkg, layer="coverage")
    assert len(gdf) == 1
    assert gdf.crs is not None
    geom = gdf.geometry.iloc[0]
    bounds = geom.bounds
    assert bounds[0] >= bbox["west"] - 0.05
    assert bounds[2] <= bbox["east"] + 0.05
    assert bounds[1] >= bbox["south"] - 0.05
    assert bounds[3] <= bbox["north"] + 0.05

    if out_kml.is_file():
        gdf_k = gpd.read_file(out_kml)
        assert len(gdf_k) == 1
        kml_text = out_kml.read_text(encoding="utf-8")
        assert VIEWSHED_COVERAGE_KML_STYLE_ID in kml_text
        assert DEFAULT_VIEWSHED_POLYGON_STYLE.fill in kml_text
        assert "<outline>0</outline>" in kml_text
        assert "<gx:drawOrder>0</gx:drawOrder>" in kml_text
        assert "<altitudeMode>clampToGround</altitudeMode>" in kml_text
