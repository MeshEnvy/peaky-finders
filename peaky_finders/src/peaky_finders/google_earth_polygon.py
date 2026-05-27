"""Prepare Shapely polygons for Google Earth KML rendering."""

from __future__ import annotations

import math

from shapely import count_coordinates, make_valid
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import orient

# Google Earth uses a 16-bit vertex index per polygon (outer + all inner rings). Polygons
# above ~65,535 vertices are silently dropped from rendering. We target a safety margin.
_GE_MAX_VERTICES_PER_POLYGON = 60_000

# GE's polygon tessellator silently fails on polygons with too many holes or too-wide bbox
# (the constrained Delaunay solver gets overwhelmed). When either threshold is exceeded we
# spatial-tile the polygon into smaller, independently-tessellatable pieces.
_GE_MAX_HOLES_PER_POLYGON = 200
_GE_MAX_BBOX_SPAN_DEG = 2.0
_GE_TILE_SIZE_DEG = 1.0

# Avoid O(n^2) grid scans that explode when tile_deg is tiny (e.g. 10°÷1e-4 ⇒ 10⁸ cells).
_MAX_GRID_CELLS = 2048

# Bounding-box bisection fallback (when the grid would be too large or stalled at min tile).
_MAX_BISECT_CHAIN = 96


def _fit_polygon_to_ge_vertex_limit(p: Polygon) -> Polygon:
    """Iteratively Douglas-Peucker simplify until vertex count fits under GE's 16-bit index limit.

    Source land-use polygons can have tens of thousands of nearly-collinear points on long
    section-line boundaries. Tolerance starts at ~1 m (invisible at any practical zoom) and
    doubles until the polygon fits or we hit a sanity cap.
    """
    if count_coordinates(p) <= _GE_MAX_VERTICES_PER_POLYGON:
        return p
    tol = 1e-5
    while count_coordinates(p) > _GE_MAX_VERTICES_PER_POLYGON and tol < 1e-2:
        p = p.simplify(tol, preserve_topology=True)
        tol *= 2
    return p


def _polygon_fits_ge_tessellator(p: Polygon) -> bool:
    n_holes = len(p.interiors)
    minx, miny, maxx, maxy = p.bounds
    span = max(maxx - minx, maxy - miny)
    return n_holes <= _GE_MAX_HOLES_PER_POLYGON and span <= _GE_MAX_BBOX_SPAN_DEG


def _extract_polygons(g: BaseGeometry) -> list[Polygon]:
    if g is None or g.is_empty:
        return []
    if isinstance(g, Polygon):
        return [g]
    if isinstance(g, MultiPolygon):
        return [p for p in g.geoms if isinstance(p, Polygon) and not p.is_empty]
    if isinstance(g, GeometryCollection):
        out: list[Polygon] = []
        for sub in g.geoms:
            out.extend(_extract_polygons(sub))
        return out
    return []


def _bbox_bisect_polygon(p: Polygon) -> list[Polygon]:
    """Split ``p`` along the longer axis into up to two polygons (axis-aligned cut)."""
    minx, miny, maxx, maxy = p.bounds
    dx, dy = maxx - minx, maxy - miny
    if dx <= 0 or dy <= 0:
        return [p]
    span = max(dx, dy)
    eps = max(span * 1e-10, 1e-12)
    # Overlap halves slightly so the cut line is not lost to numeric boundary cases.
    if dx >= dy:
        mid = (minx + maxx) / 2
        if mid <= minx + eps * 1000 or mid >= maxx - eps * 1000:
            return [p]
        b_a = box(minx, miny, mid + eps, maxy)
        b_b = box(mid - eps, miny, maxx, maxy)
    else:
        mid = (miny + maxy) / 2
        if mid <= miny + eps * 1000 or mid >= maxy - eps * 1000:
            return [p]
        b_a = box(minx, miny, maxx, mid + eps)
        b_b = box(minx, mid - eps, maxx, maxy)
    parts: list[Polygon] = []
    parts.extend(_extract_polygons(p.intersection(b_a)))
    parts.extend(_extract_polygons(p.intersection(b_b)))
    parts = [q for q in parts if not q.is_empty]
    if len(parts) < 2:
        return [p]
    return parts


def _last_resort_tessellatable(p: Polygon) -> Polygon:
    """Simplify until hole/bbox budgets fit — last resort when bisection cannot progress."""
    p = make_valid(p)
    if not isinstance(p, Polygon):
        polys = _extract_polygons(p)
        if not polys:
            return Polygon()
        p = max(polys, key=lambda x: x.area)
    if _polygon_fits_ge_tessellator(p):
        return _fit_polygon_to_ge_vertex_limit(p)
    minx, miny, maxx, maxy = p.bounds
    span = max(maxx - minx, maxy - miny)
    tol = max(span * 1e-7, 1e-7)
    for _ in range(28):
        p = make_valid(p.simplify(tol, preserve_topology=True))
        if not isinstance(p, Polygon):
            polys = _extract_polygons(p)
            if not polys:
                tol *= 2
                continue
            return _last_resort_tessellatable(max(polys, key=lambda x: x.area))
        if _polygon_fits_ge_tessellator(p):
            return _fit_polygon_to_ge_vertex_limit(p)
        tol *= 1.6
    return _fit_polygon_to_ge_vertex_limit(p)


def _bisect_then_resplit(p: Polygon, depth: int) -> list[Polygon]:
    """When the grid is too expensive or stuck, split along a great bbox divider and recurse."""
    if _polygon_fits_ge_tessellator(p):
        return [p]
    if depth >= _MAX_BISECT_CHAIN:
        return [_last_resort_tessellatable(p)]
    halves = _bbox_bisect_polygon(p)
    if len(halves) < 2:
        return [_last_resort_tessellatable(p)]
    out: list[Polygon] = []
    for h in halves:
        out.extend(_split_polygon_for_ge_tessellator(h, _GE_TILE_SIZE_DEG, depth + 1))
    return out


def _split_polygon_for_ge_tessellator(
    p: Polygon,
    tile_deg: float = _GE_TILE_SIZE_DEG,
    bisect_depth: int = 0,
) -> list[Polygon]:
    """Recursively spatial-tile ``p`` until every piece fits the GE tessellator budget.

    Holes are not uniformly distributed (urban inholdings cluster around Reno, Las Vegas,
    Carson City, etc.), so a single 1° cell of an apparently sparse 5°×6° polygon can still
    hold 500+ holes. We halve the tile size and re-split any piece still over budget.

    When the grid would visit an enormous number of tiny cells, we bound runtime by bounding-
    box bisection instead (same coordinates), which also fixes the old bug of returning an
    over-budget polygon once ``tile_deg`` dropped below 1e-3°.
    """
    if _polygon_fits_ge_tessellator(p):
        return [p]
    minx, miny, maxx, maxy = p.bounds
    w, h = maxx - minx, maxy - miny
    nx = max(1, math.ceil(w / tile_deg))
    ny = max(1, math.ceil(h / tile_deg))
    if nx * ny > _MAX_GRID_CELLS:
        return _bisect_then_resplit(p, bisect_depth)

    x0 = math.floor(minx / tile_deg) * tile_deg
    y0 = math.floor(miny / tile_deg) * tile_deg
    pieces: list[Polygon] = []
    x = x0
    while x < maxx:
        y = y0
        while y < maxy:
            cell = box(x, y, x + tile_deg, y + tile_deg)
            chunk = p.intersection(cell)
            if not chunk.is_empty:
                subs: list[Polygon] = []
                if isinstance(chunk, Polygon):
                    subs = [chunk]
                elif isinstance(chunk, MultiPolygon):
                    subs = list(chunk.geoms)
                else:
                    subs = _extract_polygons(chunk)
                for sub in subs:
                    if _polygon_fits_ge_tessellator(sub):
                        pieces.append(sub)
                    else:
                        next_tile = tile_deg / 2
                        sw = sub.bounds[2] - sub.bounds[0]
                        sh = sub.bounds[3] - sub.bounds[1]
                        next_cells = math.ceil(sw / next_tile) * math.ceil(sh / next_tile)
                        if tile_deg <= 1e-3 or next_cells > _MAX_GRID_CELLS:
                            pieces.extend(_bisect_then_resplit(sub, bisect_depth))
                        else:
                            pieces.extend(
                                _split_polygon_for_ge_tessellator(sub, next_tile, bisect_depth)
                            )
            y += tile_deg
        x += tile_deg
    if not pieces:
        return _bisect_then_resplit(p, bisect_depth)
    return pieces


def _prepare_polygon_for_kml(p: Polygon) -> list[Polygon]:
    """Tile, simplify, and re-orient one Polygon so each emitted piece is GE-renderable."""
    return [
        orient(_fit_polygon_to_ge_vertex_limit(piece), sign=1.0)
        for piece in _split_polygon_for_ge_tessellator(p)
    ]


def orient_for_kml(geom: BaseGeometry) -> BaseGeometry:
    """Prepare polygon geometry for KML output to render correctly in Google Earth.

    Three failure modes this prevents:
      1. Backface culling of large polygons whose outer ring is CW (KML 2.2 spec wants CCW
         outers + CW holes; source GDBs often store the opposite).
      2. Silent drop of polygons exceeding GE's ~65,535-vertex-per-polygon index limit.
      3. Silent fill failure (hit-test still works) on polygons with thousands of holes or
         multi-degree bbox span — GE's tessellator gives up; spatial-tile to fix.

    Non-polygonal geometries pass through unchanged.
    """
    if geom is None or geom.is_empty:
        return geom
    g = make_valid(geom)
    if isinstance(g, Polygon):
        pieces = _prepare_polygon_for_kml(g)
        return pieces[0] if len(pieces) == 1 else MultiPolygon(pieces)
    if isinstance(g, MultiPolygon):
        all_pieces: list[Polygon] = []
        for p in g.geoms:
            all_pieces.extend(_prepare_polygon_for_kml(p))
        return MultiPolygon(all_pieces)
    return g
