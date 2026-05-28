"""Corridor-guided trial scoring for mesh-backbone routing."""

from __future__ import annotations

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
from peaky_finders.site_suggestions.corridor import (
    CorridorPath,
    active_corridor,
    effective_corridor_buffer_m,
)
from peaky_finders.site_suggestions.log import (
    SuggestProgressTicker,
    suggest_log,
    suggest_progress,
    suggest_step,
)
from peaky_finders.site_suggestions.mesh_backbone_completion import (
    all_backbone_sites,
    footprints_for_backbone_sites,
    mutual_hop_neighbors,
)
from peaky_finders.site_suggestions.mesh_backbone_geom import GoalPoint
from peaky_finders.site_suggestions.mesh_connectivity import satellite_in_main_component
from peaky_finders.site_suggestions.mesh_goals import bridge_satellite_slug, goal_point_for_key, is_bridge_goal_key
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
    bridge_satellite_slug: str | None = None
    hop_sites: tuple[BackboneSite, ...] = ()
    hop_footprints: Mapping[str, BaseGeometry | None] | None = None


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
    verbose: bool = False,
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
    if verbose:
        suggest_progress(
            verbose,
            f"covered arc: line {length / 1000.0:.1f} km, buffer {buf:.0f} m, {n + 1} sample(s)",
        )
    ticker = SuggestProgressTicker(verbose, label="covered arc", interval_s=0.5)
    for i in range(n + 1):
        s = min(length, i * step)
        pt = line.interpolate(s)
        if on_corridor.contains(pt) or on_corridor.intersects(pt.buffer(min(buf, step))):
            max_s = max(max_s, s)
        ticker.maybe(f"arc sample {i}/{n}, s={max_s / 1000.0:.1f} km")

    boundary_pts = 0
    if verbose:
        suggest_progress(verbose, "covered arc: boundary vertex scan…")
    boundary_ticker = SuggestProgressTicker(
        verbose, label="covered arc boundary", interval_s=0.5, every_n=500
    )
    for geom in _explode_parts(on_corridor):
        if geom.is_empty:
            continue
        if geom.geom_type == "Point":
            max_s = max(max_s, float(line.project(geom)))
            boundary_pts += 1
            boundary_ticker.maybe(f"vertex {boundary_pts}, s={max_s / 1000.0:.1f} km")
            continue
        for x, y in _iter_boundary_coords(geom):
            max_s = max(max_s, float(line.project(Point(x, y))))
            boundary_pts += 1
            boundary_ticker.maybe(f"vertex {boundary_pts}, s={max_s / 1000.0:.1f} km")

    if verbose:
        ticker.done(f"max covered arc {max_s / 1000.0:.1f} km")
    return float(max_s)


def build_corridor_score_context(ctx: SiteSuggestionContext) -> CorridorScoreContext | None:
    corridor = active_corridor(ctx)
    if corridor is None:
        return None
    goal = goal_point_for_key(ctx, corridor.goal_key)
    if goal is None:
        return None

    verbose = ctx.verbose
    line_m = corridor.line_m3857()
    buffer_m = effective_corridor_buffer_m(ctx)
    if verbose:
        suggest_progress(
            verbose,
            f"corridor score context: goal={corridor.goal_key} variant={corridor.variant} "
            f"buffer={buffer_m:.0f} m corridor={corridor.length_m / 1000.0:.1f} km",
        )

    with suggest_step(verbose, "corridor score context composite coverage"):
        coverage = composite_coverage_geometry(ctx, verbose=verbose)
    with suggest_step(verbose, "corridor score context project coverage"):
        coverage_m = _to_m3857(coverage)
    with suggest_step(verbose, "corridor score context baseline covered arc"):
        s_before = max_covered_arc_m(coverage_m, line_m, buffer_m=buffer_m, verbose=verbose)

    if verbose:
        dist_km = _point_dist_m(coverage_m, lon=goal.lon, lat=goal.lat) / 1000.0
        suggest_log(
            verbose,
            f"site suggest:     corridor score context ready: s_before={s_before / 1000.0:.1f} km "
            f"dist_to_goal={dist_km:.1f} km sites={len(analysis_sites(ctx))}",
        )

    bridge_sat: str | None = None
    hop_sites: tuple[BackboneSite, ...] = ()
    hop_fps: Mapping[str, BaseGeometry | None] | None = None
    if is_bridge_goal_key(corridor.goal_key):
        bridge_sat = bridge_satellite_slug(corridor.goal_key)
        hop_sites = tuple(all_backbone_sites(ctx))
        hop_fps = footprints_for_backbone_sites(ctx.plan, ctx.session_footprints)

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
        bridge_satellite_slug=bridge_sat,
        hop_sites=hop_sites,
        hop_footprints=hop_fps,
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

    if score_ctx.bridge_satellite_slug is not None and score_ctx.hop_footprints is not None:
        captured = satellite_in_main_component(
            satellite_slug=score_ctx.bridge_satellite_slug,
            sites=score_ctx.hop_sites,
            footprints=score_ctx.hop_footprints,
            trial_lat=float(lat),
            trial_lon=float(lon),
            trial_footprint=fp,
        )
    else:
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


def _score_corridor_trial_worker(
    args: tuple[CorridorScoreContext, float, float, BaseGeometry],
) -> CorridorTrialScore | None:
    score_ctx, lat, lon, trial_footprint = args
    return score_corridor_trial(
        score_ctx=score_ctx,
        lat=lat,
        lon=lon,
        trial_footprint=trial_footprint,
    )


def score_corridor_trials(
    *,
    score_ctx: CorridorScoreContext,
    trials: Sequence[tuple[float, float, BaseGeometry]],
    jobs: int = 1,
    verbose: bool = False,
) -> list[CorridorTrialScore | None]:
    """Score many trial footprints; parallel when ``jobs > 1``."""
    if not trials:
        return []
    workers = max(1, int(jobs))
    n = len(trials)
    if verbose:
        suggest_progress(verbose, f"corridor trial score: {n} trial(s), workers={workers}")

    if workers <= 1 or n <= 1:
        ticker = SuggestProgressTicker(verbose, label="corridor trial score", interval_s=0.5)
        out: list[CorridorTrialScore | None] = []
        for idx, (lat, lon, fp) in enumerate(trials, start=1):
            out.append(
                score_corridor_trial(
                    score_ctx=score_ctx,
                    lat=lat,
                    lon=lon,
                    trial_footprint=fp,
                )
            )
            ticker.maybe(f"[{idx}/{n}] scored")
        if verbose:
            ticker.done(f"{sum(s is not None for s in out)}/{n} scorable")
        return out

    mx = min(workers, n)
    payload = [(score_ctx, lat, lon, fp) for lat, lon, fp in trials]
    out: list[CorridorTrialScore | None] = [None] * n
    ticker = SuggestProgressTicker(verbose, label="corridor trial score", interval_s=0.5)
    if verbose:
        suggest_progress(verbose, f"corridor trial score: process pool workers={mx}, submitting…")
    with ProcessPoolExecutor(max_workers=mx) as pool:
        futs = {
            pool.submit(_score_corridor_trial_worker, item): idx
            for idx, item in enumerate(payload)
        }
        if verbose:
            suggest_progress(verbose, f"corridor trial score: {len(futs)} job(s) queued, awaiting results…")
        done = 0
        for fut in as_completed(futs):
            out[futs[fut]] = fut.result()
            done += 1
            ticker.maybe(f"[{done}/{n}] scored")
    if verbose:
        ticker.done(f"{sum(s is not None for s in out)}/{n} scorable")
    return out
