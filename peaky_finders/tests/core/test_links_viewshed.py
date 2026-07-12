"""Core tests for viewshed-based mutual link evaluation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from shapely.geometry import box

from peaky_finders.core.links.viewshed import (
    footprint_covers_point,
    load_viewshed_footprint,
    mutual_viewshed_link,
)
from peaky_finders.core.viewshed.polygonize import SPLAT_GPKG_NAME, SPLAT_OUTPUT_PPM_BASENAME


def test_mutual_viewshed_link_requires_both_directions() -> None:
    fp_a = box(-120.0, 39.0, -119.0, 40.0)
    fp_b = box(-119.5, 39.4, -119.3, 39.6)
    lat_a, lon_a = 39.5, -119.5
    lat_b, lon_b = 39.5, -119.4
    assert mutual_viewshed_link(
        fp_a,
        fp_b,
        lat_a=lat_a,
        lon_a=lon_a,
        lat_b=lat_b,
        lon_b=lon_b,
    )
    assert not mutual_viewshed_link(
        fp_a,
        None,
        lat_a=lat_a,
        lon_a=lon_a,
        lat_b=lat_b,
        lon_b=lon_b,
    )


def test_footprint_covers_point() -> None:
    fp = box(-119.6, 39.4, -119.4, 39.6)
    assert footprint_covers_point(fp, lat=39.5, lon=-119.5)
    assert not footprint_covers_point(fp, lat=40.5, lon=-119.5)
    assert not footprint_covers_point(None, lat=39.5, lon=-119.5)


def test_mutual_viewshed_link_one_way_only_is_false() -> None:
    fp_a = box(-120.0, 39.0, -119.0, 40.0)
    fp_b = box(-119.9, 39.9, -119.8, 40.0)
    lat_a, lon_a = 39.5, -119.5
    lat_b, lon_b = 39.95, -119.85
    assert footprint_covers_point(fp_a, lat=lat_b, lon=lon_b)
    assert not footprint_covers_point(fp_b, lat=lat_a, lon=lon_a)
    assert not mutual_viewshed_link(
        fp_a,
        fp_b,
        lat_a=lat_a,
        lon_a=lon_a,
        lat_b=lat_b,
        lon_b=lon_b,
    )


def test_load_viewshed_footprint_ensure_false_skips_vectorize(tmp_path: Path) -> None:
    wd = tmp_path / "ws"
    wd.mkdir()
    (wd / SPLAT_OUTPUT_PPM_BASENAME).write_bytes(b"P6\n1 1\n255\n\x00\x00\x00")
    with patch(
        "peaky_finders.core.links.viewshed._ensure_footprint_gpkg"
    ) as ensure_gpkg:
        assert load_viewshed_footprint(wd, preset=object(), ensure=False) is None  # type: ignore[arg-type]
        ensure_gpkg.assert_not_called()
    assert not (wd / SPLAT_GPKG_NAME).is_file()
