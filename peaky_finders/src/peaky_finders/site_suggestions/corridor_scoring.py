"""Corridor-guided trial scoring for mesh-backbone routing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

import geopandas as gpd
from pyproj import Transformer
from shapely import make_valid
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from peaky_finders.site_suggestions.context import BackboneSite, SiteSuggestionContext
from peaky_finders.site_suggestions.corridor import (
    CorridorPath,
    active_corridor,
    effective_corridor_buffer_m,
)
from peaky_finders.site_suggestions.mesh_backbone_completion import mutual_hop_neighbors
from peaky_finders.site_suggestions.mesh_backbone_geom import GoalPoint
from peaky_finders.site_suggestions.mesh_goals import goal_point_for_key
from peaky_finders.site_suggestions.mesh_grow import (
    analysis_footprints,
    analysis_sites,
    composite_coverage_geometry,
)

_TO_M = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)


@dataclass(frozen=True)
class CorridorTrialScore:
    delta_s_m: float
    s_before_m: float
    s_after_m: float
    captured: bool
    delta_dist_to_goal_m: float
    hop_neighbors: tuple[str, ...]
    footprint_area_m2: float


@dataclass(frozen=True)
class CorridorScoreContext:
    goal: GoalPoint
    corridor: CorridorPath
    corridor_line_m: BaseGeometry
    corridor_buffer_m: float
    s_before_m: float
    before_dist_to_goal_m: float
    sites: tuple[BackboneSite, ...]
    footprints: Mapping[str, BaseGeometry | None]
    coverage_m: BaseGeometry | None


def _to_m3857(geom: BaseGeometry | None) -> BaseGeometry | None:
    if geom is None or geom.is_empty:
        return None
    g = geom if geom.is_valid else make_valid(geom)
    return gpd.GeoDataFrame(geometry=[g], crs="EPSG:4326").to_crs("EPSG:3857").geometry.iloc[0]


def _point_dist_m(geom_m: BaseGeometry | None, *, lon: float, lat: float) -> float:
    if geom_m is None or geom_m.is_empty:
        return float("inf")
    x, y = _TO_M.transform(float(lon), float(lat))
    return float(Point(x, y).distance(geom_m))


def _explode_parts(geom: BaseGeometry):
    if geom.is_empty:
        return
    gt = geom.geom_type
    if gt == "GeometryCollection":
        for g in geom.geoms:
            yield from _explode_parts(g)
    elif gt.startswith("Multi"):
        for g in geom.geoms:
            yield g
    else:
        yield geom


def _iter_boundary_coords(geom: BaseGeometry):
    if geom.is_empty:
        return
    gt = geom.geom_type
    if gt == "Point":
        yield (float(geom.x), float(geom.y))
        return
    if gt in ("LineString", "LinearRing"):
        yield from ((float(x), float(y)) for x, y in geom.coords)
        return
    if gt == "Polygon":
        yield from ((float(x), float(y)) for x, y in geom.exterior.coords)
        for ring in geom.interiors:
            yield from ((float(x), float(y)) for x, y in ring.coords)
        return
    if hasattr(geom, "geoms"):
        for part in geom.geoms:
            yield from _iter_boundary_coords(part)
        return
    boundary = geom.boundary
    if boundary is not None and not boundary.is_empty and boundary is not geom:
        yield from _iter_boundary_coords(boundary)


def max_covered_arc_m(
    coverage_m: BaseGeometry | None,
    corridor_line_m: BaseGeometry,
    *,
    buffer_m: float,
    step_m: float = 250.0,
) -> float:
    """Maximum arc length along ``corridor_line_m`` covered by ``coverage_m`` within buffer."""
    if coverage_m is None or coverage_m.is_empty:
        return 0.0
    line = corridor_line_m if corridor_line_m.is_valid else make_valid(corridor_line_m)
    if line.is_empty:
        return 0.0

    buf = max(25.0, float(buffer_m))
    corridor_buf = line.buffer(buf)
    cov = coverage_m if coverage_m.is_valid else make_valid(coverage_m)
    on_corridor = cov.intersection(corridor_buf)
    if on_corridor.is_empty:
        return 0.0

    max_s = 0.0
    length = float(line.length)
    if length <= 0:
        return 0.0

    step = max(25.0, float(step_m))
    n = max(1, int(length / step))
    for i in range(n + 1):
        s = min(length, i * step)
        pt = line.interpolate(s)
        if on_corridor.contains(pt) or on_corridor.intersects(pt.buffer(min(buf, step))):
            max_s = max(max_s, s)

    for geom in _explode_parts(on_corridor):
        if geom.is_empty:
            continue
        if geom.geom_type == "Point":
            max_s = max(max_s, float(line.project(geom)))
            continue
        for x, y in _iter_boundary_coords(geom):
            max_s = max(max_s, float(line.project(Point(x, y))))

    return float(max_s)


def build_corridor_score_context(ctx: SiteSuggestionContext) -> CorridorScoreContext | None:
    corridor = active_corridor(ctx)
    if corridor is None:
        return None
    goal = goal_point_for_key(ctx, corridor.goal_key)
    if goal is None:
        return None

    line_m = corridor.line_m3857()
    buffer_m = effective_corridor_buffer_m(ctx)
    coverage = composite_coverage_geometry(ctx)
    coverage_m = _to_m3857(coverage)
    s_before = max_covered_arc_m(coverage_m, line_m, buffer_m=buffer_m)

    return CorridorScoreContext(
        goal=goal,
        corridor=corridor,
        corridor_line_m=line_m,
        corridor_buffer_m=buffer_m,
        s_before_m=s_before,
        before_dist_to_goal_m=_point_dist_m(coverage_m, lon=goal.lon, lat=goal.lat),
        sites=analysis_sites(ctx),
        footprints=analysis_footprints(ctx),
        coverage_m=coverage_m,
    )


def score_corridor_trial(
    *,
    score_ctx: CorridorScoreContext,
    lat: float,
    lon: float,
    trial_footprint: BaseGeometry,
) -> CorridorTrialScore | None:
    hops = mutual_hop_neighbors(
        new_lat=float(lat),
        new_lon=float(lon),
        new_footprint=trial_footprint,
        sites=score_ctx.sites,
        footprints=score_ctx.footprints,
    )
    if not hops:
        return None

    fp = trial_footprint if trial_footprint.is_valid else make_valid(trial_footprint)
    fp_m = _to_m3857(fp)
    if fp_m is None or fp_m.is_empty:
        return None

    parts: list[BaseGeometry] = []
    if score_ctx.coverage_m is not None and not score_ctx.coverage_m.is_empty:
        parts.append(score_ctx.coverage_m)
    parts.append(fp_m)
    union_m = parts[0] if len(parts) == 1 else unary_union(parts)
    if union_m is None or union_m.is_empty:
        union_m = fp_m

    s_after = max_covered_arc_m(
        union_m,
        score_ctx.corridor_line_m,
        buffer_m=score_ctx.corridor_buffer_m,
    )
    delta_s = float(s_after - score_ctx.s_before_m)

    pt = Point(float(score_ctx.goal.lon), float(score_ctx.goal.lat))
    captured = fp.covers(pt)
    after_dist = _point_dist_m(union_m, lon=score_ctx.goal.lon, lat=score_ctx.goal.lat)
    delta_dist = float(score_ctx.before_dist_to_goal_m - after_dist)

    return CorridorTrialScore(
        delta_s_m=delta_s,
        s_before_m=score_ctx.s_before_m,
        s_after_m=s_after,
        captured=bool(captured),
        delta_dist_to_goal_m=delta_dist,
        hop_neighbors=tuple(hops),
        footprint_area_m2=float(fp_m.area),
    )


def corridor_trial_has_progress(score: CorridorTrialScore) -> bool:
    return score.captured or score.delta_s_m > 0.0


def corridor_sort_key(score: CorridorTrialScore) -> tuple:
    return (
        1 if score.captured else 0,
        score.delta_s_m,
        score.delta_dist_to_goal_m,
        score.footprint_area_m2,
    )


def score_corridor_trials(
    *,
    score_ctx: CorridorScoreContext,
    trials: Sequence[tuple[float, float, BaseGeometry]],
) -> list[CorridorTrialScore | None]:
    return [
        score_corridor_trial(score_ctx=score_ctx, lat=lat, lon=lon, trial_footprint=fp)
        for lat, lon, fp in trials
    ]
