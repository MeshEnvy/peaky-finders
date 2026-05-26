"""Mesh-grow candidate and scoring tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from shapely.geometry import box

from peaky_finders.site_suggestions.context import BackboneSite, SiteSuggestionContext
from peaky_finders.site_suggestions.depth_grid import build_coverage_depth_grid
from peaky_finders.site_suggestions.mesh_backbone_candidates import generate_mesh_grow_candidates
from peaky_finders.site_suggestions.mesh_grow import (
    active_attractor_goal,
    build_mesh_grow_score_context,
    score_mesh_grow_trial,
)
from peaky_finders.site_suggestions.strategies.mesh_backbone import MeshBackboneStrategy
from peaky_finders.sites_job import (
    BundleSiteSuggestionsConfig,
    MeshBackboneGoalEntry,
    MeshBackboneStrategyConfig,
    SiteSuggestionStrategy,
)


def test_active_attractor_picks_closest_uncaptured_goal() -> None:
    eligible = box(-116.5, 38.5, -114.5, 39.5)
    grid = build_coverage_depth_grid(
        aoi_ll=eligible,
        target_ll=eligible,
        footprint_gpkg_paths=[],
        max_raster_dimension=128,
    )
    grid.add_footprint(box(-115.9, 38.9, -115.4, 39.1))
    cfg = MeshBackboneStrategyConfig(
        goals={
            "near": MeshBackboneGoalEntry(loc=(39.0, -115.5)),
            "far": MeshBackboneGoalEntry(loc=(39.0, -114.5)),
        }
    )
    ctx = SiteSuggestionContext(
        preset=type("P", (), {"sites": {"seed": type("E", (), {"lat": 39.0, "lon": -115.8})()}})(),
        plan=type("Plan", (), {"viewshed_workspaces": ()})(),
        grid=grid,
        eligible_ll=eligible,
        aoi_ll=eligible,
        target_ll=eligible,
        suggest_root=Path("/tmp/suggest"),
        cfg=BundleSiteSuggestionsConfig(strategy=SiteSuggestionStrategy.MESH_BACKBONE, mesh_backbone=cfg),
        dem_mirror_root=Path("/tmp/dem"),
        eligible_sha="x",
        jobs=1,
        verbose=False,
    )
    attractor = active_attractor_goal(ctx)
    assert attractor is not None
    assert attractor.key == "near"


def test_generate_mesh_grow_candidates_on_frontier() -> None:
    eligible = box(-116.5, 38.5, -114.5, 39.5)
    grid = build_coverage_depth_grid(
        aoi_ll=eligible,
        target_ll=eligible,
        footprint_gpkg_paths=[],
        max_raster_dimension=128,
    )
    grid.add_footprint(box(-115.9, 38.9, -115.4, 39.1))
    cfg = MeshBackboneStrategyConfig(
        goals={"g1": MeshBackboneGoalEntry(loc=(39.0, -115.2))},
        max_candidates_per_round=8,
    )
    ctx = SiteSuggestionContext(
        preset=type("P", (), {"sites": {"seed": type("E", (), {"lat": 39.0, "lon": -115.8})()}})(),
        plan=type("Plan", (), {"viewshed_workspaces": ()})(),
        grid=grid,
        eligible_ll=eligible,
        aoi_ll=eligible,
        target_ll=eligible,
        suggest_root=Path("/tmp/suggest"),
        cfg=BundleSiteSuggestionsConfig(strategy=SiteSuggestionStrategy.MESH_BACKBONE, mesh_backbone=cfg),
        dem_mirror_root=Path("/tmp/dem"),
        eligible_sha="x",
        jobs=1,
        verbose=False,
    )
    cands = generate_mesh_grow_candidates(ctx)
    assert cands
    assert all(c.strategy == "goal:g1" for c in cands)


def test_score_mesh_grow_trial_zero_delta_for_committed_footprint() -> None:
    eligible = box(-116.5, 38.5, -114.5, 39.5)
    seed_fp = box(-115.9, 38.9, -115.4, 39.1)
    grid = build_coverage_depth_grid(
        aoi_ll=eligible,
        target_ll=eligible,
        footprint_gpkg_paths=[],
        max_raster_dimension=128,
    )
    grid.add_footprint(seed_fp)
    cfg = MeshBackboneStrategyConfig(goals={"g1": MeshBackboneGoalEntry(loc=(39.0, -114.5))})
    relay_fp = box(-115.85, 38.9, -115.15, 39.1)
    ctx = SiteSuggestionContext(
        preset=type("P", (), {"sites": {"seed": type("E", (), {"lat": 39.0, "lon": -115.8})()}})(),
        plan=type("Plan", (), {"viewshed_workspaces": ()})(),
        grid=grid,
        eligible_ll=eligible,
        aoi_ll=eligible,
        target_ll=eligible,
        suggest_root=Path("/tmp/suggest"),
        cfg=BundleSiteSuggestionsConfig(strategy=SiteSuggestionStrategy.MESH_BACKBONE, mesh_backbone=cfg),
        dem_mirror_root=Path("/tmp/dem"),
        eligible_sha="x",
        jobs=1,
        verbose=False,
        session_sites=[BackboneSite(slug="_session_0001", lat=39.0, lon=-115.65)],
        session_footprints={"_session_0001": relay_fp},
    )
    footprints = {
        "seed": seed_fp,
        "_session_0001": relay_fp,
    }
    with patch(
        "peaky_finders.site_suggestions.mesh_grow.footprints_for_backbone_sites",
        return_value=footprints,
    ):
        score_ctx = build_mesh_grow_score_context(ctx)
        assert score_ctx is not None
        score = score_mesh_grow_trial(
            score_ctx=score_ctx,
            lat=39.0,
            lon=-115.65,
            trial_footprint=relay_fp,
        )
    assert score is not None
    assert score.delta_min_dist_m <= 0
    assert score.captures_goal is False


def test_score_mesh_grow_trial_requires_mutual_hop() -> None:
    eligible = box(-116.5, 38.5, -114.5, 39.5)
    grid = build_coverage_depth_grid(
        aoi_ll=eligible,
        target_ll=eligible,
        footprint_gpkg_paths=[],
        max_raster_dimension=128,
    )
    grid.add_footprint(box(-115.9, 38.9, -115.4, 39.1))
    cfg = MeshBackboneStrategyConfig(goals={"g1": MeshBackboneGoalEntry(loc=(39.0, -115.2))})
    ctx = SiteSuggestionContext(
        preset=type("P", (), {"sites": {"seed": type("E", (), {"lat": 39.0, "lon": -115.8})()}})(),
        plan=type("Plan", (), {"viewshed_workspaces": ()})(),
        grid=grid,
        eligible_ll=eligible,
        aoi_ll=eligible,
        target_ll=eligible,
        suggest_root=Path("/tmp/suggest"),
        cfg=BundleSiteSuggestionsConfig(strategy=SiteSuggestionStrategy.MESH_BACKBONE, mesh_backbone=cfg),
        dem_mirror_root=Path("/tmp/dem"),
        eligible_sha="x",
        jobs=1,
        verbose=False,
    )
    attractor = active_attractor_goal(ctx)
    assert attractor is not None
    score_ctx = build_mesh_grow_score_context(ctx)
    assert score_ctx is not None
    orphan_fp = box(-115.35, 38.95, -115.25, 39.05)
    assert (
        score_mesh_grow_trial(
            score_ctx=score_ctx,
            lat=39.0,
            lon=-115.3,
            trial_footprint=orphan_fp,
        )
        is None
    )


def test_mesh_backbone_max_nodes_stops_solver() -> None:
    ctx = SiteSuggestionContext(
        preset=type("P", (), {"sites": {}})(),
        plan=type("Plan", (), {"viewshed_workspaces": ()})(),
        grid=type("G", (), {"depth_at_point": lambda *a, **k: 0})(),
        eligible_ll=box(-116.5, 38.5, -114.5, 39.5),
        aoi_ll=box(-116.5, 38.5, -114.5, 39.5),
        target_ll=box(-116.5, 38.5, -114.5, 39.5),
        suggest_root=Path("/tmp/suggest"),
        cfg=BundleSiteSuggestionsConfig(
            strategy=SiteSuggestionStrategy.MESH_BACKBONE,
            mesh_backbone=MeshBackboneStrategyConfig(max_nodes=2),
        ),
        dem_mirror_root=Path("/tmp/dem"),
        eligible_sha="x",
        jobs=1,
        verbose=False,
        session_sites=[
            BackboneSite(slug="_session_0001", lat=39.0, lon=-115.7),
            BackboneSite(slug="_session_0002", lat=39.0, lon=-115.6),
        ],
    )
    provider = MeshBackboneStrategy()
    assert provider.planning_complete(ctx) is True
