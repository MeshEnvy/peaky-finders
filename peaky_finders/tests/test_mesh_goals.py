"""Unified preset + bridge goals for mesh-backbone."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from shapely.geometry import box

from peaky_finders.site_suggestions.context import BackboneSite, SiteSuggestionContext
from peaky_finders.site_suggestions.corridor import CorridorGrowState, plan_corridors_for_goal
from peaky_finders.site_suggestions.corridor_scoring import build_corridor_score_context
from peaky_finders.site_suggestions.depth_grid import build_coverage_depth_grid
from peaky_finders.site_suggestions.mesh_backbone_candidates import generate_corridor_grow_candidates
from peaky_finders.site_suggestions.mesh_goals import (
    goal_point_for_key,
    is_bridge_goal_key,
    is_goal_captured,
    ordered_uncaptured_goal_keys,
)
from peaky_finders.site_suggestions.mesh_connectivity import main_footprint_slugs
from peaky_finders.site_suggestions.mesh_grow import analysis_sites
from peaky_finders.sites_job import (
    BundleSiteSuggestionsConfig,
    MeshBackboneGoalEntry,
    MeshBackboneRouting,
    MeshBackboneStrategyConfig,
    SiteEntry,
    SiteSuggestionStrategy,
    SiteType,
)


def _disconnected_ctx() -> SiteSuggestionContext:
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
                    "main": SiteEntry(type=SiteType.INSTALLED, name="Main", loc=(41.5, -119.0)),
                    "sat": SiteEntry(type=SiteType.INSTALLED, name="Sat", loc=(36.2, -115.3)),
                }
            },
        )(),
        plan=type("Plan", (), {"viewshed_workspaces": ()})(),
        grid=grid,
        eligible_ll=eligible,
        aoi_ll=eligible,
        target_ll=eligible,
        suggest_root=Path("/tmp/suggest"),
        cfg=BundleSiteSuggestionsConfig(
            strategy=SiteSuggestionStrategy.MESH_BACKBONE,
            mesh_backbone=MeshBackboneStrategyConfig(
                routing=MeshBackboneRouting.CORRIDOR,
                goals={"g0": MeshBackboneGoalEntry(loc=(39.0, -115.5))},
                corridor_grid_cell_m=500.0,
                max_candidates_per_round=8,
            ),
        ),
        dem_mirror_root=Path("/tmp/dem"),
        eligible_sha="x",
        jobs=1,
        verbose=False,
        corridor_state=CorridorGrowState(),
    )
    ctx.session_footprints = footprints
    return ctx


def test_bridge_goal_key_detection() -> None:
    assert is_bridge_goal_key("bridge:sat")
    assert not is_bridge_goal_key("elko")


def test_ordered_uncaptured_prefers_bridge_before_preset() -> None:
    ctx = _disconnected_ctx()
    with patch(
        "peaky_finders.site_suggestions.mesh_backbone_completion.footprints_for_backbone_sites",
        return_value=ctx.session_footprints,
    ):
        pending = ordered_uncaptured_goal_keys(ctx)
        assert pending == ["bridge:sat"]
        assert "g0" not in pending


def test_goal_point_for_bridge_key() -> None:
    ctx = _disconnected_ctx()
    with patch(
        "peaky_finders.site_suggestions.mesh_backbone_completion.footprints_for_backbone_sites",
        return_value=ctx.session_footprints,
    ):
        goal = goal_point_for_key(ctx, "bridge:sat")
        assert goal is not None
        assert goal.lat == 36.2
        assert goal.lon == -115.3


def test_corridor_bridge_uses_main_mesh_scope() -> None:
    ctx = _disconnected_ctx()
    ctx.corridor_state.active_goal_key = "bridge:sat"
    with patch(
        "peaky_finders.site_suggestions.mesh_backbone_completion.footprints_for_backbone_sites",
        return_value=ctx.session_footprints,
    ):
        main = main_footprint_slugs(ctx)
        assert main is not None
        assert main == frozenset({"main"})
        assert {s.slug for s in analysis_sites(ctx)} == {"main"}


def test_plan_corridor_for_bridge_goal() -> None:
    ctx = _disconnected_ctx()
    with patch(
        "peaky_finders.site_suggestions.mesh_backbone_completion.footprints_for_backbone_sites",
        return_value=ctx.session_footprints,
    ):
        routes = plan_corridors_for_goal(ctx, goal_key="bridge:sat", k=1, cell_m=500.0)
        assert len(routes) == 1
        assert routes[0].goal_key == "bridge:sat"
        assert routes[0].length_m > 0


def test_corridor_candidates_for_bridge_goal() -> None:
    ctx = _disconnected_ctx()
    with patch(
        "peaky_finders.site_suggestions.mesh_backbone_completion.footprints_for_backbone_sites",
        return_value=ctx.session_footprints,
    ):
        ctx.corridor_state.corridors = plan_corridors_for_goal(
            ctx, goal_key="bridge:sat", k=1, cell_m=500.0
        )
        ctx.corridor_state.active_goal_key = "bridge:sat"
        cands = generate_corridor_grow_candidates(ctx)
        assert cands
        assert all("bridge:sat" in c.strategy for c in cands)


def test_corridor_score_context_for_bridge_goal() -> None:
    ctx = _disconnected_ctx()
    with patch(
        "peaky_finders.site_suggestions.mesh_backbone_completion.footprints_for_backbone_sites",
        return_value=ctx.session_footprints,
    ):
        ctx.corridor_state.corridors = plan_corridors_for_goal(
            ctx, goal_key="bridge:sat", k=1, cell_m=500.0
        )
        ctx.corridor_state.active_goal_key = "bridge:sat"
        score_ctx = build_corridor_score_context(ctx)
        assert score_ctx is not None
        assert score_ctx.goal.key == "bridge:sat"
        assert {s.slug for s in score_ctx.sites} == {"main"}


def test_bridge_goal_captured_when_main_covers_sat_pin() -> None:
    ctx = _disconnected_ctx()
    merged = box(-120.0, 35.0, -114.0, 42.0)
    footprints = {"main": merged, "sat": box(-115.4, 36.1, -115.2, 36.3)}
    with patch(
        "peaky_finders.site_suggestions.mesh_backbone_completion.footprints_for_backbone_sites",
        return_value=footprints,
    ):
        assert is_goal_captured(ctx, "bridge:sat")
