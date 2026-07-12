"""GroundOverlay raster sizing for Earth texture limits."""

from __future__ import annotations

from peaky_finders.core.viewshed.raster import overlay_dimensions_capped


def test_overlay_dimensions_uncapped_inside_limit() -> None:
    assert overlay_dimensions_capped(1024, 768, 8192) == (1024, 768)


def test_overlay_dimensions_landscape_scaled_to_max_edge() -> None:
    assert overlay_dimensions_capped(14400, 10800, 8192) == (8192, 6144)


def test_overlay_dimensions_portrait_scaled_to_max_edge() -> None:
    assert overlay_dimensions_capped(10800, 14400, 8192) == (6144, 8192)
