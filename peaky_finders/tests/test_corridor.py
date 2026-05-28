"""Tests for eligible-corridor planning and scoring."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
from shapely.geometry import LineString, box

from peaky_finders.site_suggestions.context import BackboneSite, SiteSuggestionContext
from peaky_finders.site_suggestions.corridor import (
    CorridorGrowState,
    plan_corridors_for_goal,
)
from peaky_finders.site_suggestions.corridor_scoring import (
    build_corridor_score_context,
    corridor_sort_key,
    corridor_trial_has_progress,
    max_covered_arc_m,
    score_corridor_trial,
)
from peaky_finders.site_suggestions.depth_grid import build_coverage_depth_grid
from peaky_finders.site_suggestions.mesh_backbone_candidates import generate_corridor_grow_candidates
from peaky_finders.sites_job import (
    BundleSiteSuggestionsConfig,
    MeshBackboneGoalEntry,
    MeshBackboneRouting,
    MeshBackboneStrategyConfig,
    SiteSuggestionStrategy,
)


def _ctx(*, eligible, goals: dict[str, tuple[float, float]], footprint=None) -> SiteSuggestionContext:
    grid = build_coverage_depth_grid(
        aoi_ll=eligible,
        target_ll=eligible,
        footprint_gpkg_paths=[],
        max_raster_dimension=128,
    )
    if footprint is not None:
        grid.add_footprint(footprint)
    mb = MeshBackboneStrategyConfig(
        routing=MeshBackboneRouting.CORRIDOR,
        goals={k: MeshBackboneGoalEntry(loc=v) for k, v in goals.items()},
        corridor_grid_cell_m=200.0,
        corridor_k=2,
        max_candidates_per_round=16,
        coarse_peaks_enabled=False,
    )
    ctx = SiteSuggestionContext(
        preset=type("P", (), {"sites": {"seed": type("E", (), {"lat": 39.0, "lon": -116.0})()}})(),
        plan=type("Plan", (), {"viewshed_workspaces": ()})(),
        grid=grid,
        eligible_ll=eligible,
        aoi_ll=eligible,
        target_ll=eligible,
        suggest_root=Path("/tmp/suggest"),
        cfg=BundleSiteSuggestionsConfig(strategy=SiteSuggestionStrategy.MESH_BACKBONE, mesh_backbone=mb),
        dem_mirror_root=Path("/tmp/dem"),
        eligible_sha="x",
        jobs=1,
        verbose=False,
        corridor_state=CorridorGrowState(),
    )
    if footprint is not None:
        ctx.session_footprints["seed"] = footprint
    return ctx


def test_plan_corridor_through_eligible_strip() -> None:
    eligible = box(-116.5, 38.5, -114.5, 39.5)
    seed_fp = box(-116.4, 38.9, -116.0, 39.1)
    ctx = _ctx(
        eligible=eligible,
        goals={"east": (39.0, -114.8)},
        footprint=seed_fp,
    )
    routes = plan_corridors_for_goal(ctx, goal_key="east", k=1, cell_m=200.0)
    assert len(routes) == 1
    assert routes[0].length_m > 0
    end = routes[0].line.coords[-1]
    assert end[0] > -115.0


def test_plan_corridor_routes_around_hole() -> None:
    outer = box(-116.5, 38.5, -114.5, 39.5)
    hole = box(-115.6, 38.85, -115.2, 39.15)
    eligible = outer.difference(hole)
    seed_fp = box(-116.4, 38.9, -116.0, 39.1)
    ctx = _ctx(
        eligible=eligible,
        goals={"east": (39.0, -114.8)},
        footprint=seed_fp,
    )
    routes = plan_corridors_for_goal(ctx, goal_key="east", k=2, cell_m=150.0)
    assert routes
    for route in routes:
        assert route.line.intersects(eligible)


def test_max_covered_arc_m_increases_with_footprint() -> None:
    line = LineString([(-116.0, 39.0), (-115.0, 39.0)])
    line_m = gpd.GeoDataFrame(geometry=[line], crs="EPSG:4326").to_crs("EPSG:3857").geometry.iloc[0]
    s0 = max_covered_arc_m(None, line_m, buffer_m=1000.0)
    fp_m = gpd.GeoDataFrame(geometry=[box(-116.2, 38.9, -115.8, 39.1)], crs="EPSG:4326").to_crs(
        "EPSG:3857"
    ).geometry.iloc[0]
    s1 = max_covered_arc_m(fp_m, line_m, buffer_m=1000.0)
    assert s1 > s0


def test_max_covered_arc_m_multipart_coverage() -> None:
    line = LineString([(-116.0, 39.0), (-115.0, 39.0)])
    line_m = gpd.GeoDataFrame(geometry=[line], crs="EPSG:4326").to_crs("EPSG:3857").geometry.iloc[0]
    multi = gpd.GeoDataFrame(
        geometry=[box(-116.2, 38.9, -115.8, 39.1), box(-115.4, 38.9, -115.0, 39.1)],
        crs="EPSG:4326",
    ).to_crs("EPSG:3857").geometry.unary_union
    s = max_covered_arc_m(multi, line_m, buffer_m=1000.0)
    assert s > 0.0


def test_corridor_scoring_prefers_forward_progress() -> None:
    eligible = box(-116.5, 38.5, -114.5, 39.5)
    seed_fp = box(-116.4, 38.9, -116.0, 39.1)
    ctx = _ctx(
        eligible=eligible,
        goals={"east": (39.0, -114.8)},
        footprint=seed_fp,
    )
    routes = plan_corridors_for_goal(ctx, goal_key="east", k=1, cell_m=200.0)
    assert routes
    ctx.corridor_state.corridors = routes
    ctx.corridor_state.active_goal_key = "east"
    score_ctx = build_corridor_score_context(ctx)
    assert score_ctx is not None

    forward_fp = box(-116.35, 38.88, -115.85, 39.12)
    side_fp = box(-116.15, 39.05, -115.75, 39.25)
    relay_lat, relay_lon = 39.0, -116.15

    forward = score_corridor_trial(
        score_ctx=score_ctx,
        lat=relay_lat,
        lon=relay_lon,
        trial_footprint=forward_fp,
    )
    sideways = score_corridor_trial(
        score_ctx=score_ctx,
        lat=39.12,
        lon=relay_lon,
        trial_footprint=side_fp,
    )
    assert forward is not None
    assert corridor_trial_has_progress(forward)
    if sideways is not None:
        assert corridor_sort_key(forward) >= corridor_sort_key(sideways)


def test_generate_corridor_candidates_active_goal() -> None:
    eligible = box(-116.5, 38.5, -114.5, 39.5)
    seed_fp = box(-116.4, 38.9, -116.0, 39.1)
    ctx = _ctx(
        eligible=eligible,
        goals={"east": (39.0, -114.8)},
        footprint=seed_fp,
    )
    cands = generate_corridor_grow_candidates(ctx)
    assert cands
    assert all(c.strategy.startswith("corridor:") or c.strategy.startswith("goal:") for c in cands)
