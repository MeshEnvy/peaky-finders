"""Tests for :mod:`peaky_finders.google_earth_polygon` (GE tessellation limits, tiling)."""

from __future__ import annotations

from shapely.geometry import MultiPolygon, Polygon, box

from peaky_finders.google_earth_polygon import (
    _GE_MAX_BBOX_SPAN_DEG,
    _GE_MAX_HOLES_PER_POLYGON,
    _polygon_fits_ge_tessellator,
    orient_for_kml,
)


def _nested_square_holes(nx: int, ny: int, *, hole_size: float = 0.08, margin: float = 0.05) -> Polygon:
    """Single outer square with nx*ny rectangular holes (for tessellator stress)."""
    outer = box(0, 0, 10, 10)
    holes: list[tuple[float, float, float, float]] = []
    step_x = (10 - 2 * margin) / nx
    step_y = (10 - 2 * margin) / ny
    for i in range(nx):
        for j in range(ny):
            x0 = margin + i * step_x + step_x * 0.15
            y0 = margin + j * step_y + step_y * 0.15
            holes.append((x0, y0, x0 + hole_size, y0 + hole_size))
    shell = list(outer.exterior.coords)
    interiors = [tuple(box(*b).exterior.coords) for b in holes]
    return Polygon(shell, interiors)


def test_polygon_fits_tessellator_thresholds() -> None:
    small = box(0, 0, 1, 1)
    assert _polygon_fits_ge_tessellator(small)
    wide = box(0, 0, _GE_MAX_BBOX_SPAN_DEG + 0.1, 1)
    assert not _polygon_fits_ge_tessellator(wide)


def test_orient_for_kml_splits_many_holes_for_google_earth() -> None:
    """GE silently drops fill when hole count is too high; output must subdivide."""
    poly = _nested_square_holes(20, 13)
    assert len(poly.interiors) == 260
    assert not _polygon_fits_ge_tessellator(poly)

    out = orient_for_kml(poly)
    assert isinstance(out, MultiPolygon)
    pieces = list(out.geoms)
    assert len(pieces) >= 2
    for p in pieces:
        assert isinstance(p, Polygon)
        assert _polygon_fits_ge_tessellator(p)
        assert len(p.interiors) <= _GE_MAX_HOLES_PER_POLYGON
        span = max(p.bounds[2] - p.bounds[0], p.bounds[3] - p.bounds[1])
        assert span <= _GE_MAX_BBOX_SPAN_DEG
