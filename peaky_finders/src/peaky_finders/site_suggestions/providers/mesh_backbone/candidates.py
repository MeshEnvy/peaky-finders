"""Mesh-grow candidate generation (frontier extension toward goals)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

import geopandas as gpd
from pyproj import Transformer
from shapely.geometry import LineString, Point
from shapely.geometry.base import BaseGeometry

from peaky_finders.site_suggestions.candidates import SiteCandidate, _dedupe_candidates
from peaky_finders.site_suggestions.context import SiteSuggestionContext
from peaky_finders.site_suggestions.corridor import (
    active_corridor,
    effective_corridor_buffer_m,
    ensure_active_corridor,
)
from peaky_finders.site_suggestions.corridor_scoring import max_covered_arc_m
from peaky_finders.site_suggestions.log import (
    SuggestProgressTicker,
    suggest_log,
    suggest_progress,
    suggest_step,
)
from peaky_finders.site_suggestions.providers.mesh_backbone.geom import GoalPoint
from peaky_finders.site_suggestions.mesh_connectivity import main_footprint_slugs
from peaky_finders.site_suggestions.providers.mesh_backbone.goals import goal_point_for_key
from peaky_finders.site_suggestions.providers.mesh_backbone.scoring import (
    analysis_sites,
    composite_coverage_geometry,
    grow_goals,
)
from peaky_finders.sites_job import MeshBackboneStrategyConfig

_TO_M = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
_FROM_M = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)


def _point_in_coverage(
    ctx: SiteSuggestionContext,
    *,
    lon: float,
    lat: float,
    coverage: BaseGeometry | None,
) -> bool:
    if coverage is not None and not coverage.is_empty:
        return coverage.covers(Point(float(lon), float(lat)))
    return ctx.grid.depth_at_point(float(lon), float(lat)) >= 1


def _cold_start_samples(
    ctx: SiteSuggestionContext,
    *,
    goal: GoalPoint,
    cfg: MeshBackboneStrategyConfig,
    cap: int,
    coverage: BaseGeometry | None = None,
) -> list[SiteCandidate]:
    """Sample along seed→goal line on cells already in composite coverage."""
    sites = list(analysis_sites(ctx))
    if not sites:
        return []

    if coverage is not None and not coverage.is_empty:
        cov_m = gpd.GeoDataFrame(geometry=[coverage], crs="EPSG:4326").to_crs("EPSG:3857").geometry.iloc[0]
        best = min(
            sites,
            key=lambda s: float(
                Point(*_TO_M.transform(float(s.lon), float(s.lat))).distance(cov_m)
            ),
        )
    else:
        best = min(
            sites,
            key=lambda s: ctx.grid.min_distance_coverage_to_point_m(
                float(s.lon), float(s.lat), min_depth=1
            ),
        )
    gx, gy = _TO_M.transform(float(goal.lon), float(goal.lat))
    sx, sy = _TO_M.transform(float(best.lon), float(best.lat))
    leg = LineString([(sx, sy), (gx, gy)])
    if leg.is_empty or leg.length <= 0:
        return []

    spacing = max(50.0, float(cfg.frontier_sample_spacing_m))
    n = max(1, int(leg.length / spacing))
    label = f"goal:{goal.key}"
    out: list[SiteCandidate] = []
    for i in range(1, n + 1):
        dist = min(float(leg.length), i * spacing)
        pt = leg.interpolate(dist)
        lon, lat = _FROM_M.transform(pt.x, pt.y)
        if not ctx.eligible_ll.intersects(Point(float(lon), float(lat))):
            continue
        if not _point_in_coverage(ctx, lon=float(lon), lat=float(lat), coverage=coverage):
            continue
        out.append(
            SiteCandidate(
                lat=float(lat),
                lon=float(lon),
                elev_m=None,
                strategy=label,
            )
        )
    return out[:cap]


def _frontier_candidates_for_goal(
    ctx: SiteSuggestionContext,
    *,
    goal: GoalPoint,
    cfg: MeshBackboneStrategyConfig,
    cap: int,
) -> list[SiteCandidate]:
    if cap <= 0:
        return []

    verbose = ctx.verbose
    use_composite = main_footprint_slugs(ctx) is not None
    if verbose:
        src = "composite footprints" if use_composite else "depth grid"
        suggest_progress(
            verbose,
            f"frontier toward {goal.key}: cap={cap} spacing={cfg.frontier_sample_spacing_m:.0f} m ({src})",
        )

    coverage = None
    if use_composite:
        with suggest_step(verbose, f"frontier toward {goal.key} composite coverage"):
            coverage = composite_coverage_geometry(ctx, verbose=verbose)

    with suggest_step(verbose, f"frontier toward {goal.key} grid scan"):
        samples = ctx.grid.frontier_sample_points_toward_goal(
            goal_lon=float(goal.lon),
            goal_lat=float(goal.lat),
            eligible_ll=ctx.eligible_ll,
            min_depth=1,
            spacing_m=float(cfg.frontier_sample_spacing_m),
            max_points=cap,
            coverage_geometry_wgs84=coverage,
            verbose=verbose,
        )
    label = f"goal:{goal.key}"
    cands = [SiteCandidate(lat=lat, lon=lon, elev_m=None, strategy=label) for lat, lon in samples]
    if not cands:
        if verbose:
            suggest_progress(verbose, f"frontier toward {goal.key}: cold start along seed→goal…")
        with suggest_step(verbose, f"frontier toward {goal.key} cold start"):
            cands = _cold_start_samples(ctx, goal=goal, cfg=cfg, cap=cap, coverage=coverage)
    if verbose:
        suggest_log(verbose, f"site suggest:     frontier toward {goal.key}: {len(cands)} candidate(s)")
    return cands


def _corridor_line_samples(
    ctx: SiteSuggestionContext,
    *,
    goal: GoalPoint,
    cap: int,
) -> list[SiteCandidate]:
    """Sample on active corridor line within coverage + lookahead toward goal."""
    corridor = active_corridor(ctx)
    if corridor is None or cap <= 0:
        return []

    mb = ctx.cfg.mesh_backbone
    line_m = corridor.line_m3857()
    if line_m is None or line_m.is_empty:
        return []
    if line_m.geom_type == "Point":
        return []
    if line_m.geom_type == "MultiLineString":
        line_m = max(line_m.geoms, key=lambda g: float(g.length))
    if float(line_m.length) <= 0.0:
        return []
    buffer_m = effective_corridor_buffer_m(ctx)
    verbose = ctx.verbose
    label = f"corridor:{goal.key}"
    out: list[SiteCandidate] = []

    with suggest_step(verbose, f"corridor line samples composite coverage (buffer={buffer_m:.0f} m)"):
        coverage = composite_coverage_geometry(ctx, verbose=verbose)
    with suggest_step(verbose, "corridor line samples project coverage"):
        cov_m = None
        if coverage is not None and not coverage.is_empty:
            cov_m = gpd.GeoDataFrame(geometry=[coverage], crs="EPSG:4326").to_crs("EPSG:3857").geometry.iloc[0]
    with suggest_step(verbose, "corridor line samples covered arc"):
        s_covered = max_covered_arc_m(cov_m, line_m, buffer_m=buffer_m, verbose=verbose)
    s_end = min(float(line_m.length), s_covered + float(mb.corridor_lookahead_m))
    spacing = max(50.0, float(mb.frontier_sample_spacing_m))
    if verbose:
        suggest_progress(
            verbose,
            f"corridor line samples: walk s={s_covered / 1000.0:.1f}–{s_end / 1000.0:.1f} km "
            f"spacing={spacing:.0f} m cap={cap}",
        )

    s = max(0.0, s_covered - spacing)
    ticker = SuggestProgressTicker(verbose, label="corridor line walk", interval_s=0.5)
    while s <= s_end and len(out) < cap:
        pt_m = line_m.interpolate(s)
        lon, lat = _FROM_M.transform(float(pt_m.x), float(pt_m.y))
        pt = Point(float(lon), float(lat))
        if not ctx.eligible_ll.intersects(pt):
            s += spacing
            continue
        if not _point_in_coverage(ctx, lon=float(lon), lat=float(lat), coverage=coverage):
            s += spacing
            continue
        out.append(SiteCandidate(lat=float(lat), lon=float(lon), elev_m=None, strategy=label))
        ticker.maybe(f"s={s / 1000.0:.1f} km, {len(out)} kept")
        s += spacing

    if verbose:
        suggest_log(verbose, f"site suggest:     corridor line samples: {len(out)} candidate(s)")
    return out


def _corridor_frontier_samples(
    ctx: SiteSuggestionContext,
    *,
    goal: GoalPoint,
    cap: int,
) -> list[SiteCandidate]:
    """Frontier samples restricted to corridor buffer toward active goal."""
    if cap <= 0:
        return []
    corridor = active_corridor(ctx)
    if corridor is None:
        return []

    verbose = ctx.verbose
    buffer_m = effective_corridor_buffer_m(ctx)
    base_cap = cap * 2
    with suggest_step(verbose, f"corridor buffer frontier base samples (cap={base_cap})"):
        base = _frontier_candidates_for_goal(
            ctx, goal=goal, cfg=ctx.cfg.mesh_backbone, cap=base_cap
        )
    if verbose:
        suggest_log(verbose, f"site suggest:     corridor buffer frontier: {len(base)} base sample(s)")

    line_m = corridor.line_m3857()
    if line_m is None or line_m.is_empty or line_m.geom_type == "Point" or float(line_m.length) <= 0.0:
        if verbose:
            suggest_log(verbose, "site suggest:     corridor buffer frontier: invalid corridor line")
        return []
    if line_m.geom_type == "MultiLineString":
        line_m = max(line_m.geoms, key=lambda g: float(g.length))
    corridor_buf = line_m.buffer(max(100.0, buffer_m))

    if verbose:
        suggest_progress(
            verbose,
            f"corridor buffer frontier: filter {len(base)} base through buffer "
            f"({buffer_m:.0f} m, cap={cap})…",
        )
    out: list[SiteCandidate] = []
    ticker = SuggestProgressTicker(verbose, label="corridor buffer filter", interval_s=0.5)
    for idx, cand in enumerate(base, start=1):
        x, y = _TO_M.transform(float(cand.lon), float(cand.lat))
        if corridor_buf.contains(Point(x, y)) or corridor_buf.intersects(Point(x, y).buffer(1.0)):
            out.append(cand)
        ticker.maybe(f"[{idx}/{len(base)}] checked, {len(out)} in buffer")
        if len(out) >= cap:
            break
    if verbose:
        suggest_log(
            verbose,
            f"site suggest:     corridor buffer frontier: {len(out)} in-buffer sample(s)",
        )
    return out


def generate_corridor_grow_candidates(ctx: SiteSuggestionContext) -> list[SiteCandidate]:
    """Candidates for corridor routing toward the active committed goal."""
    mb = ctx.cfg.mesh_backbone
    cap = max(1, int(mb.max_candidates_per_round))

    with suggest_step(ctx.verbose, "corridor ensure active route"):
        corridor = ensure_active_corridor(ctx)
    if corridor is None:
        suggest_log(ctx.verbose, "site suggest:     corridor candidates: no active corridor")
        return []

    goal = goal_point_for_key(ctx, corridor.goal_key)
    if goal is None:
        suggest_log(ctx.verbose, f"site suggest:     corridor candidates: unknown goal {corridor.goal_key}")
        return []

    half = max(1, cap // 2)
    cands: list[SiteCandidate] = []

    with suggest_step(ctx.verbose, f"corridor sample candidates toward {corridor.goal_key} (cap={cap})"):
        suggest_progress(
            ctx.verbose,
            f"line samples along variant {corridor.variant + 1} (cap={half})…",
        )
        cands.extend(_corridor_line_samples(ctx, goal=goal, cap=half))
        line_n = len(cands)

        suggest_progress(
            ctx.verbose,
            f"frontier in buffer (cap={cap - line_n}, buffer={effective_corridor_buffer_m(ctx):.0f} m)…",
        )
        cands.extend(_corridor_frontier_samples(ctx, goal=goal, cap=cap - len(cands)))
        frontier_n = len(cands) - line_n

        if len(cands) < cap:
            suggest_progress(
                ctx.verbose,
                f"general frontier toward {goal.key} (cap={cap - len(cands)})…",
            )
            cands.extend(
                _frontier_candidates_for_goal(
                    ctx,
                    goal=goal,
                    cfg=mb,
                    cap=cap - len(cands),
                )
            )

        cands = _dedupe_candidates(cands)[:cap]
        suggest_log(
            ctx.verbose,
            f"site suggest:     corridor candidates: {len(cands)} "
            f"(line={line_n}, buffer={frontier_n}, goal={corridor.goal_key})",
        )

    from peaky_finders.site_suggestions.refine_peaks import attach_frontier_peak_candidates

    with suggest_step(ctx.verbose, "corridor attach coarse peaks"):
        return attach_frontier_peak_candidates(
            ctx=ctx,
            candidates=cands,
            eligible_ll=ctx.eligible_ll,
            verbose=ctx.verbose,
        )


def generate_mesh_grow_candidates(ctx: SiteSuggestionContext) -> list[SiteCandidate]:
    """Frontier samples on composite coverage, split across all active goals."""
    mb = ctx.cfg.mesh_backbone
    goals = grow_goals(ctx)
    if not goals:
        return []

    cap = max(1, int(mb.max_candidates_per_round))
    per_goal = max(1, cap // len(goals))
    cands: list[SiteCandidate] = []
    goal_items = list(goals.values())
    workers = max(1, int(ctx.jobs))
    if workers > 1 and len(goal_items) > 1:
        mx = min(workers, len(goal_items))
        if ctx.verbose:
            suggest_progress(
                ctx.verbose,
                f"mesh-grow candidates: {len(goal_items)} goal(s), workers={mx}",
            )
        with ThreadPoolExecutor(max_workers=mx) as pool:
            futs = {
                pool.submit(_frontier_candidates_for_goal, ctx, goal=goal, cfg=mb, cap=per_goal): goal.key
                for goal in goal_items
            }
            for fut in as_completed(futs):
                cands.extend(fut.result())
    else:
        for goal in goal_items:
            cands.extend(_frontier_candidates_for_goal(ctx, goal=goal, cfg=mb, cap=per_goal))

    cands = _dedupe_candidates(cands)[:cap]
    from peaky_finders.site_suggestions.refine_peaks import attach_frontier_peak_candidates

    return attach_frontier_peak_candidates(
        ctx=ctx,
        candidates=cands,
        eligible_ll=ctx.eligible_ll,
        verbose=ctx.verbose,
    )


generate_mesh_backbone_candidates = generate_mesh_grow_candidates
