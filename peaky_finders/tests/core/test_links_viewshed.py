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


def test_load_viewshed_footprint_ensure_false_rejects_stale_gpkg(tmp_path: Path) -> None:
    import os
    import time

    wd = tmp_path / "ws"
    wd.mkdir()
    gpkg = wd / SPLAT_GPKG_NAME
    png = wd / "splat.png"
    gpkg.write_bytes(b"old")
    png.write_bytes(b"new")
    now = time.time()
    os.utime(gpkg, (now - 10, now - 10))
    os.utime(png, (now, now))
    with patch("peaky_finders.core.links.viewshed.read_coverage_footprint") as read_fp:
        assert load_viewshed_footprint(wd, preset=object(), ensure=False) is None  # type: ignore[arg-type]
        read_fp.assert_not_called()


class _StubGdf:
    def __init__(self, geoms):
        self.geometry = list(geoms)
        self.empty = not geoms


def test_read_coverage_footprint_wkb_sidecar_roundtrip(tmp_path: Path) -> None:
    import os
    import time

    from peaky_finders.core.viewshed.footprint import FOOTPRINT_WKB_NAME, read_coverage_footprint

    gpkg = tmp_path / SPLAT_GPKG_NAME
    gpkg.write_bytes(b"fake")
    geom = box(-120.0, 39.0, -119.0, 40.0)

    with patch("peaky_finders.core.viewshed.footprint.pyogrio") as og:
        og.read_dataframe.return_value = _StubGdf([geom])
        first = read_coverage_footprint(gpkg)
        assert first is not None and first.equals(geom)
        og.read_dataframe.assert_called_once()

    sidecar = tmp_path / FOOTPRINT_WKB_NAME
    assert sidecar.is_file() and sidecar.stat().st_size > 0

    # Fresh sidecar short-circuits the GPKG open entirely.
    with patch("peaky_finders.core.viewshed.footprint.pyogrio") as og:
        second = read_coverage_footprint(gpkg)
        assert second is not None and second.equals(geom)
        og.read_dataframe.assert_not_called()

    # Newer GPKG invalidates the sidecar.
    now = time.time()
    os.utime(sidecar, (now - 10, now - 10))
    os.utime(gpkg, (now, now))
    other = box(-118.0, 38.0, -117.0, 39.0)
    with patch("peaky_finders.core.viewshed.footprint.pyogrio") as og:
        og.read_dataframe.return_value = _StubGdf([other])
        third = read_coverage_footprint(gpkg)
        assert third is not None and third.equals(other)
        og.read_dataframe.assert_called_once()


def test_read_coverage_footprint_wkb_sidecar_empty_marker(tmp_path: Path) -> None:
    from peaky_finders.core.viewshed.footprint import FOOTPRINT_WKB_NAME, read_coverage_footprint

    gpkg = tmp_path / SPLAT_GPKG_NAME
    gpkg.write_bytes(b"fake")

    with patch("peaky_finders.core.viewshed.footprint.pyogrio") as og:
        og.read_dataframe.return_value = _StubGdf([])
        assert read_coverage_footprint(gpkg) is None

    assert (tmp_path / FOOTPRINT_WKB_NAME).is_file()

    with patch("peaky_finders.core.viewshed.footprint.pyogrio") as og:
        assert read_coverage_footprint(gpkg) is None
        og.read_dataframe.assert_not_called()
