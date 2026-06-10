"""Mesh-grow candidate and scoring tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from shapely.geometry import box

from peaky_finders.site_suggestions.context import BackboneSite, SiteSuggestionContext
from peaky_finders.site_suggestions.depth_grid import build_coverage_depth_grid
from peaky_finders.site_suggestions.providers.mesh_backbone.candidates import generate_mesh_grow_candidates
from peaky_finders.site_suggestions.providers.mesh_backbone.scoring import (
    active_attractor_goal,
    build_mesh_grow_score_context,
    grow_goals,
    mesh_grow_sort_key,
    score_mesh_grow_trial,
    uncaptured_goals,
)
from peaky_finders.site_suggestions.providers.mesh_backbone import MeshBackboneStrategy
from peaky_finders.sites_job import (
    GoalEntry,
    SuggestConfig,
    MeshBackboneStrategyConfig,
    SiteSuggestionStrategy,
)


@pytest.fixture(autouse=True)
def _stub_rf_goal_pairs():
    with patch(
        "peaky_finders.site_suggestions.providers.mesh_backbone.completion._rf_viable_goal_site_pairs",
        return_value=set(),
    ):
        yield


def _mesh_ctx(
    *,
    goals: dict[str, tuple[float, float]],
    grid,
    eligible,
    session_sites: list[BackboneSite] | None = None,
    session_footprints: dict[str, object] | None = None,
) -> SiteSuggestionContext:
    goal_entries = {key: GoalEntry(name=key, loc=loc) for key, loc in goals.items()}
    preset = type(
        "P",
        (),
        {
            "sites": {"seed": type("E", (), {"lat": 39.0, "lon": -115.8})()},
            "goals": goal_entries,
        },
    )()
    return SiteSuggestionContext(
        preset=preset,
        plan=type("Plan", (), {"viewshed_workspaces": ()})(),
        grid=grid,
        eligible_ll=eligible,
        aoi_ll=eligible,
        target_ll=eligible,
        suggest_root=Path("/tmp/suggest"),
        cfg=SuggestConfig(
            strategy=SiteSuggestionStrategy.MESH_BACKBONE,
            mesh_backbone=MeshBackboneStrategyConfig(),
        ),
        dem_mirror_root=Path("/tmp/dem"),
        eligible_sha="x",
        jobs=1,
        verbose=False,
        session_sites=session_sites or [],
        session_footprints=session_footprints or {},
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
    ctx = _mesh_ctx(
        goals={"near": (39.0, -115.5), "far": (39.0, -114.5)},
        grid=grid,
        eligible=eligible,
    )
    attractor = active_attractor_goal(ctx)
    assert attractor is not None
    assert attractor.key == "near"
    assert set(uncaptured_goals(ctx)) == {"near", "far"}


def test_generate_mesh_grow_candidates_on_frontier() -> None:
    eligible = box(-116.5, 38.5, -114.5, 39.5)
    grid = build_coverage_depth_grid(
        aoi_ll=eligible,
        target_ll=eligible,
        footprint_gpkg_paths=[],
        max_raster_dimension=128,
    )
    grid.add_footprint(box(-115.9, 38.9, -115.4, 39.1))
    ctx = SiteSuggestionContext(
        preset=type(
            "P",
            (),
            {
                "sites": {"seed": type("E", (), {"lat": 39.0, "lon": -115.8})()},
                "goals": {"g1": GoalEntry(name="G1", loc=(39.0, -115.2))},
            },
        )(),
        plan=type("Plan", (), {"viewshed_workspaces": ()})(),
        grid=grid,
        eligible_ll=eligible,
        aoi_ll=eligible,
        target_ll=eligible,
        suggest_root=Path("/tmp/suggest"),
        cfg=SuggestConfig(
            strategy=SiteSuggestionStrategy.MESH_BACKBONE,
            mesh_backbone=MeshBackboneStrategyConfig(
                max_candidates_per_round=8,
                coarse_peaks_enabled=False,
            ),
        ),
        dem_mirror_root=Path("/tmp/dem"),
        eligible_sha="x",
        jobs=1,
        verbose=False,
    )
    cands = generate_mesh_grow_candidates(ctx)
    assert cands
    assert all(c.strategy == "goal:g1" for c in cands)


def test_generate_mesh_grow_candidates_multi_goal() -> None:
    eligible = box(-116.5, 38.5, -114.5, 39.5)
    grid = build_coverage_depth_grid(
        aoi_ll=eligible,
        target_ll=eligible,
        footprint_gpkg_paths=[],
        max_raster_dimension=128,
    )
    grid.add_footprint(box(-115.9, 38.9, -115.4, 39.1))
    ctx = _mesh_ctx(
        goals={"north": (39.5, -115.5), "south": (38.5, -115.5)},
        grid=grid,
        eligible=eligible,
    )
    ctx.cfg.mesh_backbone.max_candidates_per_round = 16
    ctx.cfg.mesh_backbone.coarse_peaks_enabled = False
    cands = generate_mesh_grow_candidates(ctx)
    assert cands
    strategies = {c.strategy for c in cands}
    assert "goal:north" in strategies
    assert "goal:south" in strategies


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
    relay_fp = box(-115.85, 38.9, -115.15, 39.1)
    ctx = _mesh_ctx(
        goals={"g1": (39.0, -114.5)},
        grid=grid,
        eligible=eligible,
        session_sites=[BackboneSite(slug="_session_0001", lat=39.0, lon=-115.65)],
        session_footprints={"_session_0001": relay_fp},
    )
    footprints = {
        "seed": seed_fp,
        "_session_0001": relay_fp,
    }
    with patch(
        "peaky_finders.site_suggestions.providers.mesh_backbone.completion.footprints_for_backbone_sites",
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
    assert score.best_delta_m <= 0
    assert score.captured_goal_keys == ()


def test_score_mesh_grow_trial_prefers_best_goal_delta() -> None:
    eligible = box(-116.5, 38.5, -114.5, 39.5)
    seed_fp = box(-115.9, 38.9, -115.4, 39.1)
    grid = build_coverage_depth_grid(
        aoi_ll=eligible,
        target_ll=eligible,
        footprint_gpkg_paths=[],
        max_raster_dimension=128,
    )
    grid.add_footprint(seed_fp)
    ctx = _mesh_ctx(
        goals={"near": (39.0, -115.55), "far": (39.0, -114.5)},
        grid=grid,
        eligible=eligible,
    )
    near_fp = box(-115.82, 38.95, -115.48, 39.05)
    far_fp = box(-115.82, 38.9, -114.95, 39.1)
    footprints = {"seed": seed_fp}
    with patch(
        "peaky_finders.site_suggestions.providers.mesh_backbone.completion.footprints_for_backbone_sites",
        return_value=footprints,
    ):
        score_ctx = build_mesh_grow_score_context(ctx)
        assert score_ctx is not None
        near_score = score_mesh_grow_trial(
            score_ctx=score_ctx,
            lat=39.0,
            lon=-115.65,
            trial_footprint=near_fp,
        )
        far_score = score_mesh_grow_trial(
            score_ctx=score_ctx,
            lat=39.0,
            lon=-115.4,
            trial_footprint=far_fp,
        )
    assert near_score is not None
    assert far_score is not None
    assert mesh_grow_sort_key(far_score) > mesh_grow_sort_key(near_score)
    assert near_score.best_goal_key == "near"


def test_score_mesh_grow_trial_requires_mutual_hop() -> None:
    eligible = box(-116.5, 38.5, -114.5, 39.5)
    grid = build_coverage_depth_grid(
        aoi_ll=eligible,
        target_ll=eligible,
        footprint_gpkg_paths=[],
        max_raster_dimension=128,
    )
    grid.add_footprint(box(-115.9, 38.9, -115.4, 39.1))
    ctx = _mesh_ctx(
        goals={"g1": (39.0, -115.2)},
        grid=grid,
        eligible=eligible,
    )
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


def test_score_mesh_grow_trial_heal_ignores_satellite_hop() -> None:
    """During healing, trials must hop to main mesh only (not satellite islands)."""
    eligible = box(-120.0, 35.0, -114.0, 42.0)
    main_fp = box(-119.1, 41.4, -118.9, 41.6)
    sat_fp = box(-115.4, 36.1, -115.2, 36.3)
    footprints = {"main": main_fp, "sat": sat_fp}
    grid = build_coverage_depth_grid(
        aoi_ll=eligible,
        target_ll=eligible,
        footprint_gpkg_paths=[],
        max_raster_dimension=128,
    )
    grid.add_footprint(main_fp)
    ctx = SiteSuggestionContext(
        preset=type(
            "P",
            (),
            {
                "sites": {
                    "main": type("E", (), {"lat": 41.5, "lon": -119.0})(),
                    "sat": type("E", (), {"lat": 36.2, "lon": -115.3})(),
                },
                "goals": {"g0": GoalEntry(name="G0", loc=(39.0, -115.5))},
            },
        )(),
        plan=type("Plan", (), {"viewshed_workspaces": ()})(),
        grid=grid,
        eligible_ll=eligible,
        aoi_ll=eligible,
        target_ll=eligible,
        suggest_root=Path("/tmp/suggest"),
        cfg=SuggestConfig(
            strategy=SiteSuggestionStrategy.MESH_BACKBONE,
            mesh_backbone=MeshBackboneStrategyConfig(),
        ),
        dem_mirror_root=Path("/tmp/dem"),
        eligible_sha="x",
        jobs=1,
        verbose=False,
    )
    with patch(
        "peaky_finders.site_suggestions.providers.mesh_backbone.completion.footprints_for_backbone_sites",
        return_value=footprints,
    ):
        assert set(grow_goals(ctx)) == {"bridge:sat"}
        score_ctx = build_mesh_grow_score_context(ctx)
        assert score_ctx is not None
        assert {s.slug for s in score_ctx.sites} == {"main"}
        sat_only_fp = box(-115.35, 36.15, -115.25, 36.25)
        assert (
            score_mesh_grow_trial(
                score_ctx=score_ctx,
                lat=36.2,
                lon=-115.3,
                trial_footprint=sat_only_fp,
            )
            is None
        )


def test_healing_candidates_sample_from_main_mesh_not_satellite_grid() -> None:
    """Healing frontier must ignore satellite seed coverage on the depth grid."""
    eligible = box(-120.0, 35.0, -114.0, 42.0)
    main_fp = box(-119.1, 41.4, -118.9, 41.6)
    sat_fp = box(-115.4, 36.1, -115.2, 36.3)
    footprints = {"main": main_fp, "sat": sat_fp}
    grid = build_coverage_depth_grid(
        aoi_ll=eligible,
        target_ll=eligible,
        footprint_gpkg_paths=[],
        max_raster_dimension=128,
    )
    grid.add_footprint(main_fp)
    grid.add_footprint(sat_fp)
    ctx = SiteSuggestionContext(
        preset=type(
            "P",
            (),
            {
                "sites": {
                    "main": type("E", (), {"lat": 41.5, "lon": -119.0})(),
                    "sat": type("E", (), {"lat": 36.2, "lon": -115.3})(),
                },
                "goals": {"g0": GoalEntry(name="G0", loc=(39.0, -115.5))},
            },
        )(),
        plan=type("Plan", (), {"viewshed_workspaces": ()})(),
        grid=grid,
        eligible_ll=eligible,
        aoi_ll=eligible,
        target_ll=eligible,
        suggest_root=Path("/tmp/suggest"),
        cfg=SuggestConfig(
            strategy=SiteSuggestionStrategy.MESH_BACKBONE,
            mesh_backbone=MeshBackboneStrategyConfig(
                max_candidates_per_round=16,
            ),
        ),
        dem_mirror_root=Path("/tmp/dem"),
        eligible_sha="x",
        jobs=1,
        verbose=False,
    )
    with patch(
        "peaky_finders.site_suggestions.providers.mesh_backbone.completion.footprints_for_backbone_sites",
        return_value=footprints,
    ):
        cands = generate_mesh_grow_candidates(ctx)
    assert cands
    assert all(c.strategy == "goal:bridge:sat" for c in cands)
    assert all(c.lat > 40.0 for c in cands), [c.lat for c in cands]


def test_mesh_backbone_max_nodes_counts_preset_sites() -> None:
    ctx = SiteSuggestionContext(
        preset=type("P", (), {"sites": {}})(),
        plan=type("Plan", (), {"viewshed_workspaces": ()})(),
        grid=type("G", (), {"depth_at_point": lambda *a, **k: 0})(),
        eligible_ll=box(-116.5, 38.5, -114.5, 39.5),
        aoi_ll=box(-116.5, 38.5, -114.5, 39.5),
        target_ll=box(-116.5, 38.5, -114.5, 39.5),
        suggest_root=Path("/tmp/suggest"),
        cfg=SuggestConfig(
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
