"""Mesh-grow attractor selection and trial scoring."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Mapping, Sequence

import geopandas as gpd
from pyproj import Transformer
from shapely import make_valid
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from peaky_finders.site_suggestions.context import BackboneSite, SiteSuggestionContext
from peaky_finders.site_suggestions.mesh_backbone_completion import (
    all_backbone_sites,
    captured_goal_keys,
    footprints_for_backbone_sites,
    mutual_hop_neighbors,
    site_location_key,
)
from peaky_finders.site_suggestions.mesh_backbone_geom import GoalPoint, goals_from_config

_TO_M = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)


@dataclass(frozen=True)
class MeshGrowTrialScore:
    delta_min_dist_m: float
    captures_goal: bool
    footprint_area_m2: float
    hop_neighbors: tuple[str, ...]


@dataclass(frozen=True)
class MeshGrowScoreContext:
    """Cached grid coverage + seed footprints for one scoring pass."""

    attractor: GoalPoint
    coverage: BaseGeometry | None
    before_dist_m: float
    sites: tuple[BackboneSite, ...]
    footprints: Mapping[str, BaseGeometry | None]


def composite_coverage_geometry(ctx: SiteSuggestionContext) -> BaseGeometry | None:
    """Union of committed footprint polygons plus rasterized composite coverage."""
    footprints = footprints_for_backbone_sites(ctx.plan, ctx.session_footprints)
    parts: list[BaseGeometry] = []
    grid_cov = ctx.grid.coverage_geometry_wgs84(min_depth=1)
    if grid_cov is not None and not grid_cov.is_empty:
        parts.append(grid_cov if grid_cov.is_valid else make_valid(grid_cov))
    for fp in footprints.values():
        if fp is None or fp.is_empty:
            continue
        parts.append(fp if fp.is_valid else make_valid(fp))
    if not parts:
        return None
    union = unary_union(parts)
    if union is None or union.is_empty:
        return None
    return union if union.is_valid else make_valid(union)


def _min_distance_geometry_to_point_m(
    geom: BaseGeometry | None,
    *,
    lon: float,
    lat: float,
) -> float:
    if geom is None or geom.is_empty:
        return float("inf")
    g = geom if geom.is_valid else make_valid(geom)
    gm = gpd.GeoDataFrame(geometry=[g], crs="EPSG:4326").to_crs("EPSG:3857").geometry.iloc[0]
    x, y = _TO_M.transform(float(lon), float(lat))
    return float(Point(x, y).distance(gm))


def active_attractor_goal(ctx: SiteSuggestionContext) -> GoalPoint | None:
    """Uncaptured goal closest to current composite coverage."""
    mb = ctx.cfg.mesh_backbone
    goals = goals_from_config(mb)
    if not goals:
        return None

    sites = all_backbone_sites(ctx)
    footprints = footprints_for_backbone_sites(ctx.plan, ctx.session_footprints)
    uncaptured = set(goals.keys()) - captured_goal_keys(mb, sites, footprints)
    if not uncaptured:
        return None

    coverage = composite_coverage_geometry(ctx)
    best_key: str | None = None
    best_dist = float("inf")
    for key in uncaptured:
        goal = goals[key]
        dist = _min_distance_geometry_to_point_m(coverage, lon=goal.lon, lat=goal.lat)
        if dist < best_dist:
            best_dist = dist
            best_key = key
    return goals[best_key] if best_key is not None else None


def build_mesh_grow_score_context(ctx: SiteSuggestionContext) -> MeshGrowScoreContext | None:
    """Precompute coverage geometry and seed footprints once per scoring batch."""
    attractor = active_attractor_goal(ctx)
    if attractor is None:
        return None
    coverage = composite_coverage_geometry(ctx)
    return MeshGrowScoreContext(
        attractor=attractor,
        coverage=coverage,
        before_dist_m=_min_distance_geometry_to_point_m(coverage, lon=attractor.lon, lat=attractor.lat),
        sites=tuple(all_backbone_sites(ctx)),
        footprints=footprints_for_backbone_sites(ctx.plan, ctx.session_footprints),
    )


def _min_distance_union_to_point_m(
    coverage: BaseGeometry | None,
    trial_fp: BaseGeometry,
    *,
    lon: float,
    lat: float,
) -> float:
    parts: list[BaseGeometry] = []
    if coverage is not None and not coverage.is_empty:
        parts.append(coverage if coverage.is_valid else make_valid(coverage))
    if trial_fp is not None and not trial_fp.is_empty:
        parts.append(trial_fp if trial_fp.is_valid else make_valid(trial_fp))
    if not parts:
        return float("inf")
    union = unary_union(parts)
    if union is None or union.is_empty:
        return float("inf")
    return _min_distance_geometry_to_point_m(union, lon=lon, lat=lat)


def score_mesh_grow_trial(
    *,
    score_ctx: MeshGrowScoreContext,
    lat: float,
    lon: float,
    trial_footprint: BaseGeometry,
) -> MeshGrowTrialScore | None:
    """Score a trial footprint; ``None`` when no confirmed mutual hop to an existing site."""
    hops = mutual_hop_neighbors(
        new_lat=float(lat),
        new_lon=float(lon),
        new_footprint=trial_footprint,
        sites=score_ctx.sites,
        footprints=score_ctx.footprints,
    )
    if not hops:
        return None

    after_m = _min_distance_union_to_point_m(
        score_ctx.coverage,
        trial_footprint,
        lon=score_ctx.attractor.lon,
        lat=score_ctx.attractor.lat,
    )
    delta = score_ctx.before_dist_m - after_m

    pt = Point(float(score_ctx.attractor.lon), float(score_ctx.attractor.lat))
    fp = trial_footprint if trial_footprint.is_valid else make_valid(trial_footprint)
    captures = bool(fp.covers(pt))

    gm = gpd.GeoDataFrame(geometry=[fp], crs="EPSG:4326").to_crs("EPSG:3857").geometry.iloc[0]
    return MeshGrowTrialScore(
        delta_min_dist_m=float(delta),
        captures_goal=captures,
        footprint_area_m2=float(gm.area),
        hop_neighbors=tuple(hops),
    )


def mesh_grow_sort_key(score: MeshGrowTrialScore) -> tuple:
    """Higher is better: goal progress, capture, then footprint area."""
    return (
        score.delta_min_dist_m,
        1 if score.captures_goal else 0,
        score.footprint_area_m2,
    )


def summarize_mesh_grow_outcomes(outcomes: Sequence[str]) -> str:
    counts = Counter(outcomes)
    parts = [f"{key}={counts[key]}" for key in sorted(counts)]
    return ", ".join(parts)
