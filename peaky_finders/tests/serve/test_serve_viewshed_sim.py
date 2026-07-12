"""Serve viewshed simulation override query params."""

from __future__ import annotations

import pytest

from peaky_finders.serve.viewshed import viewshed_png_api_path
from peaky_finders.serve.viewshed_sim import (
    ViewshedSimOverrides,
    parse_viewshed_sim_overrides,
    viewshed_sim_query_string,
)


def test_viewshed_sim_query_string_empty_when_no_overrides() -> None:
    assert viewshed_sim_query_string(None) == ""
    assert viewshed_sim_query_string(ViewshedSimOverrides()) == ""


def test_viewshed_sim_query_string_round_trip() -> None:
    ov = ViewshedSimOverrides(radius_km=45.0, raster_dimension=1024)
    assert viewshed_sim_query_string(ov) == "radius_km=45&raster_dimension=1024"
    parsed = parse_viewshed_sim_overrides(viewshed_sim_query_string(ov))
    assert parsed.radius_km == 45.0
    assert parsed.raster_dimension == 1024


def test_parse_viewshed_sim_overrides_rejects_out_of_range() -> None:
    with pytest.raises(ValueError, match="radius_km"):
        parse_viewshed_sim_overrides("radius_km=150")
    with pytest.raises(ValueError, match="raster_dimension"):
        parse_viewshed_sim_overrides("raster_dimension=64")


def test_viewshed_png_api_path_includes_sim_query() -> None:
    ov = ViewshedSimOverrides(radius_km=60, raster_dimension=500)
    url = viewshed_png_api_path("nevada", "hub", sim_overrides=ov)
    assert url.endswith("?radius_km=60&raster_dimension=500")
