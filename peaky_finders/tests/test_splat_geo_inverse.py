"""Rotation-aware inverse geo mapping matches forward sampling."""

from __future__ import annotations

import math

from peaky_finders.coverage_png import (
    fraction_to_lat_lon,
    pixel_to_lat_lon,
)
from peaky_finders.coverage_png import _lat_lon_to_fraction


def _roundtrip(lat: float, lon: float, *, rotation_deg: float) -> None:
    north, south, east, west = 40.0, 39.0, -117.0, -118.0
    uf, vf = _lat_lon_to_fraction(
        lat,
        lon,
        north=north,
        south=south,
        east=east,
        west=west,
        rotation_deg=rotation_deg,
    )
    assert uf is not None and vf is not None
    lat2, lon2 = fraction_to_lat_lon(
        uf,
        vf,
        north=north,
        south=south,
        east=east,
        west=west,
        rotation_deg=rotation_deg,
    )
    assert math.isclose(lat2, lat, rel_tol=0, abs_tol=1e-9)
    assert math.isclose(lon2, lon, rel_tol=0, abs_tol=1e-9)


def test_lat_lon_fraction_roundtrip_rotation_zero() -> None:
    _roundtrip(39.25, -117.35, rotation_deg=0.0)


def test_lat_lon_fraction_roundtrip_rotation_nonzero() -> None:
    _roundtrip(39.41, -117.62, rotation_deg=33.0)


def test_raster_corners_map_uv_fractions_rotation_zero() -> None:
    north, south, east, west = 41.0, 40.0, -71.0, -72.0
    w, h = 101, 51
    lat_nw, lon_nw = pixel_to_lat_lon(
        0.0,
        0.0,
        width=w,
        height=h,
        north=north,
        south=south,
        east=east,
        west=west,
        rotation_deg=0.0,
    )
    uf, vf = _lat_lon_to_fraction(
        lat_nw,
        lon_nw,
        north=north,
        south=south,
        east=east,
        west=west,
        rotation_deg=0.0,
    )
    assert uf is not None and vf is not None
    assert math.isclose(uf, 0.0, abs_tol=1e-9)
    assert math.isclose(vf, 0.0, abs_tol=1e-9)

    lat_se, lon_se = pixel_to_lat_lon(
        float(w),
        float(h),
        width=w,
        height=h,
        north=north,
        south=south,
        east=east,
        west=west,
        rotation_deg=0.0,
    )
    uf2, vf2 = _lat_lon_to_fraction(
        lat_se,
        lon_se,
        north=north,
        south=south,
        east=east,
        west=west,
        rotation_deg=0.0,
    )
    assert uf2 is not None and vf2 is not None
    assert math.isclose(uf2, 1.0, abs_tol=1e-9)
    assert math.isclose(vf2, 1.0, abs_tol=1e-9)
