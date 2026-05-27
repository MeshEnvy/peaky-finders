"""Mesh-grow trial scoring across all uncaptured goals."""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Mapping, Sequence

import geopandas as gpd
from pyproj import Transformer
from shapely import make_valid
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from peaky_finders.site_suggestions.context import BackboneSite, SiteSuggestionContext
import peaky_finders.site_suggestions.mesh_backbone_completion as mesh_completion
from peaky_finders.site_suggestions.mesh_backbone_completion import (
    all_backbone_sites,
    captured_goal_keys,
    mutual_hop_neighbors,
)
from peaky_finders.site_suggestions.mesh_backbone_geom import GoalPoint, goals_from_config
from peaky_finders.site_suggestions.mesh_connectivity import (
    main_footprint_slugs,
    mesh_healing_needed,
    uncaptured_healing_goals,
)

_TO_M = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)


@dataclass(frozen=True)
class MeshGrowTrialScore:
    delta_by_goal_m: dict[str, float]
    captured_goal_keys: tuple[str, ...]
    best_goal_key: str | None
    best_delta_m: float
    total_delta_m: float
    footprint_area_m2: float
    hop_neighbors: tuple[str, ...]


@dataclass(frozen=True)
class MeshGrowScoreContext:
    """Cached grid coverage + seed footprints for one scoring pass."""

    uncaptured_goals: dict[str, GoalPoint]
    before_dist_m: dict[str, float]
    coverage: BaseGeometry | None
    coverage_m3857: BaseGeometry | None
    sites: tuple[BackboneSite, ...]
    footprints: Mapping[str, BaseGeometry | None]


def analysis_footprints(ctx: SiteSuggestionContext) -> dict[str, BaseGeometry | None]:
    """Footprints in analysis scope (main mesh only during healing)."""
    fps = mesh_completion.footprints_for_backbone_sites(ctx.plan, ctx.session_footprints)
    main = main_footprint_slugs(ctx)
    if main is None:
        return fps
    return {slug: fps.get(slug) for slug in main}


def analysis_sites(ctx: SiteSuggestionContext) -> tuple[BackboneSite, ...]:
    """Backbone sites in analysis scope (main mesh only during healing)."""
    main = main_footprint_slugs(ctx)
    sites = all_backbone_sites(ctx)
    if main is None:
        return tuple(sites)
    return tuple(s for s in sites if s.slug in main)


def composite_coverage_geometry(ctx: SiteSuggestionContext) -> BaseGeometry | None:
    """Union of analysis-scope footprints plus rasterized composite coverage."""
    footprints = analysis_footprints(ctx)
    parts: list[BaseGeometry] = []
    # During healing, satellite seed footprints must not expand the composite mesh.
    if not mesh_healing_needed(ctx):
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


def uncaptured_goals(ctx: SiteSuggestionContext) -> dict[str, GoalPoint]:
    """Configured goals not yet captured by an existing site footprint."""
    mb = ctx.cfg.mesh_backbone
    goals = goals_from_config(mb)
    if not goals:
        return {}

    sites = all_backbone_sites(ctx)
    footprints = mesh_completion.footprints_for_backbone_sites(ctx.plan, ctx.session_footprints)
    missing = set(goals.keys()) - captured_goal_keys(mb, sites, footprints)
    return {key: goals[key] for key in sorted(missing)}


def grow_goals(ctx: SiteSuggestionContext) -> dict[str, GoalPoint]:
    """Active goals for the current phase (heal bridge goals or preset grow goals)."""
    if mesh_healing_needed(ctx):
        return uncaptured_healing_goals(ctx)
    return uncaptured_goals(ctx)


def active_attractor_goal(ctx: SiteSuggestionContext) -> GoalPoint | None:
    """Uncaptured goal closest to current composite coverage (status/logging only)."""
    goals = grow_goals(ctx)
    if not goals:
        return None

    coverage = composite_coverage_geometry(ctx)
    best_key: str | None = None
    best_dist = float("inf")
    for key, goal in goals.items():
        dist = _min_distance_geometry_to_point_m(coverage, lon=goal.lon, lat=goal.lat)
        if dist < best_dist:
            best_dist = dist
            best_key = key
    return goals[best_key] if best_key is not None else None


def build_mesh_grow_score_context(ctx: SiteSuggestionContext) -> MeshGrowScoreContext | None:
    """Precompute coverage geometry and seed footprints once per scoring batch."""
    goals = grow_goals(ctx)
    if not goals:
        return None
    coverage = composite_coverage_geometry(ctx)
    coverage_m3857 = _to_m3857(coverage)
    before_dist_m = {
        key: _point_distance_m3857(coverage_m3857, lon=goal.lon, lat=goal.lat)
        for key, goal in goals.items()
    }
    return MeshGrowScoreContext(
        uncaptured_goals=goals,
        before_dist_m=before_dist_m,
        coverage=coverage,
        coverage_m3857=coverage_m3857,
        sites=analysis_sites(ctx),
        footprints=analysis_footprints(ctx),
    )


def _valid_geom(geom: BaseGeometry) -> BaseGeometry:
    return geom if geom.is_valid else make_valid(geom)


def _to_m3857(geom: BaseGeometry | None) -> BaseGeometry | None:
    if geom is None or geom.is_empty:
        return None
    g = _valid_geom(geom)
    return gpd.GeoDataFrame(geometry=[g], crs="EPSG:4326").to_crs("EPSG:3857").geometry.iloc[0]


def _point_distance_m3857(
    geom_m3857: BaseGeometry | None,
    *,
    lon: float,
    lat: float,
) -> float:
    if geom_m3857 is None or geom_m3857.is_empty:
        return float("inf")
    x, y = _TO_M.transform(float(lon), float(lat))
    return float(Point(x, y).distance(geom_m3857))


def _min_distance_geometry_to_point_m(
    geom: BaseGeometry | None,
    *,
    lon: float,
    lat: float,
) -> float:
    return _point_distance_m3857(_to_m3857(geom), lon=lon, lat=lat)


def _trial_union_m3857(
    *,
    coverage_m3857: BaseGeometry | None,
    trial_fp: BaseGeometry,
) -> BaseGeometry | None:
    parts: list[BaseGeometry] = []
    if coverage_m3857 is not None and not coverage_m3857.is_empty:
        parts.append(coverage_m3857)
    if trial_fp is not None and not trial_fp.is_empty:
        trial_m = _to_m3857(trial_fp)
        if trial_m is not None and not trial_m.is_empty:
            parts.append(trial_m)
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    union = unary_union(parts)
    if union is None or union.is_empty:
        return None
    return union if union.is_valid else make_valid(union)


def _best_goal_key(
    *,
    delta_by_goal_m: Mapping[str, float],
    captured_goal_keys: Sequence[str],
) -> str | None:
    if captured_goal_keys:
        return max(
            captured_goal_keys,
            key=lambda key: (float(delta_by_goal_m.get(key, 0.0)), key),
        )
    positive = [(key, float(delta)) for key, delta in delta_by_goal_m.items() if delta > 0]
    if not positive:
        return None
    return max(positive, key=lambda item: (item[1], item[0]))[0]


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

    fp = _valid_geom(trial_footprint)
    trial_union_m3857 = _trial_union_m3857(
        coverage_m3857=score_ctx.coverage_m3857,
        trial_fp=trial_footprint,
    )
    delta_by_goal_m: dict[str, float] = {}
    captured: list[str] = []
    for key, goal in score_ctx.uncaptured_goals.items():
        after_m = _point_distance_m3857(trial_union_m3857, lon=goal.lon, lat=goal.lat)
        delta_by_goal_m[key] = float(score_ctx.before_dist_m[key] - after_m)
        pt = Point(float(goal.lon), float(goal.lat))
        if fp.covers(pt):
            captured.append(key)

    captured_keys = tuple(sorted(captured))
    best_delta_m = max(delta_by_goal_m.values(), default=0.0)
    total_delta_m = sum(max(0.0, delta) for delta in delta_by_goal_m.values())
    best_goal_key = _best_goal_key(
        delta_by_goal_m=delta_by_goal_m,
        captured_goal_keys=captured_keys,
    )

    trial_m3857 = _to_m3857(fp)
    footprint_area_m2 = float(trial_m3857.area) if trial_m3857 is not None else 0.0
    return MeshGrowTrialScore(
        delta_by_goal_m=delta_by_goal_m,
        captured_goal_keys=captured_keys,
        best_goal_key=best_goal_key,
        best_delta_m=float(best_delta_m),
        total_delta_m=float(total_delta_m),
        footprint_area_m2=footprint_area_m2,
        hop_neighbors=tuple(hops),
    )


def _score_mesh_grow_trial_worker(
    args: tuple[MeshGrowScoreContext, float, float, BaseGeometry],
) -> MeshGrowTrialScore | None:
    score_ctx, lat, lon, trial_footprint = args
    return score_mesh_grow_trial(
        score_ctx=score_ctx,
        lat=lat,
        lon=lon,
        trial_footprint=trial_footprint,
    )


def score_mesh_grow_trials(
    *,
    score_ctx: MeshGrowScoreContext,
    trials: Sequence[tuple[float, float, BaseGeometry]],
    jobs: int = 1,
) -> list[MeshGrowTrialScore | None]:
    """Score many trial footprints; parallel when ``jobs > 1``."""
    if not trials:
        return []
    workers = max(1, int(jobs))
    if workers <= 1 or len(trials) <= 1:
        return [
            score_mesh_grow_trial(
                score_ctx=score_ctx,
                lat=lat,
                lon=lon,
                trial_footprint=fp,
            )
            for lat, lon, fp in trials
        ]

    mx = min(workers, len(trials))
    payload = [(score_ctx, lat, lon, fp) for lat, lon, fp in trials]
    out: list[MeshGrowTrialScore | None] = [None] * len(trials)
    with ProcessPoolExecutor(max_workers=mx) as pool:
        futs = {
            pool.submit(_score_mesh_grow_trial_worker, item): idx
            for idx, item in enumerate(payload)
        }
        for fut in as_completed(futs):
            out[futs[fut]] = fut.result()
    return out


def mesh_grow_sort_key(score: MeshGrowTrialScore) -> tuple:
    """Higher is better: captures, best single-goal Δdist, total Δdist, footprint area."""
    return (
        len(score.captured_goal_keys),
        score.best_delta_m,
        score.total_delta_m,
        score.footprint_area_m2,
    )


def mesh_grow_trial_has_progress(score: MeshGrowTrialScore) -> bool:
    """True when the trial captures a goal or improves distance toward any uncaptured goal."""
    return bool(score.captured_goal_keys) or score.best_delta_m > 0


def summarize_mesh_grow_outcomes(outcomes: Sequence[str]) -> str:
    counts = Counter(outcomes)
    parts = [f"{key}={counts[key]}" for key in sorted(counts)]
    return ", ".join(parts)
