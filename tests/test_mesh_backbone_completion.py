"""Mesh-grow completion and hop checks."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from shapely.geometry import box

from peaky_finders.site_suggestions.context import BackboneSite, SiteSuggestionContext
from peaky_finders.site_suggestions.mesh_backbone_completion import (
    captured_goal_keys,
    hop_adjacency,
    mesh_grow_planning_complete,
    mutual_hop_neighbors,
    sites_capturing_goal,
)
from peaky_finders.site_suggestions.mesh_backbone_geom import GoalPoint
from peaky_finders.sites_job import (
    BundleSiteSuggestionsConfig,
    MeshBackboneGoalEntry,
    MeshBackboneStrategyConfig,
    SiteSuggestionStrategy,
)


def test_mutual_hop_neighbors() -> None:
    sites = [
        BackboneSite(slug="s0", lat=39.0, lon=-115.8),
        BackboneSite(slug="s1", lat=39.0, lon=-115.5),
    ]
    footprints = {
        "s0": box(-115.9, 38.9, -115.4, 39.1),
        "s1": box(-115.85, 38.9, -115.15, 39.1),
    }
    new_fp = box(-115.9, 38.95, -115.45, 39.05)
    hops = mutual_hop_neighbors(
        new_lat=39.0,
        new_lon=-115.65,
        new_footprint=new_fp,
        sites=sites,
        footprints=footprints,
    )
    assert "s0" in hops
    assert "s1" in hops


def test_sites_capturing_goal() -> None:
    goal = GoalPoint(key="g0", lat=39.0, lon=-115.8)
    sites = [BackboneSite(slug="near", lat=39.0, lon=-115.75)]
    footprints = {"near": box(-115.9, 38.9, -115.4, 39.1)}
    assert sites_capturing_goal(goal, sites, footprints) == {"near"}


def test_mesh_grow_planning_complete_when_goals_captured_and_connected() -> None:
    cfg = MeshBackboneStrategyConfig(
        goals={
            "g0": MeshBackboneGoalEntry(loc=(39.0, -115.85)),
        }
    )
    sites = [
        BackboneSite(slug="seed", lat=39.0, lon=-115.8),
        BackboneSite(slug="relay", lat=39.0, lon=-115.65),
    ]
    footprints = {
        "seed": box(-115.9, 38.9, -115.4, 39.1),
        "relay": box(-115.85, 38.9, -115.15, 39.1),
    }
    ctx = SiteSuggestionContext(
        preset=type("P", (), {"sites": {"seed": type("E", (), {"lat": 39.0, "lon": -115.8})()}})(),
        plan=type("Plan", (), {"viewshed_workspaces": ()})(),
        grid=type("G", (), {"depth_at_point": lambda *a, **k: 1})(),
        eligible_ll=box(-116.5, 38.5, -114.5, 39.5),
        aoi_ll=box(-116.5, 38.5, -114.5, 39.5),
        target_ll=box(-116.5, 38.5, -114.5, 39.5),
        suggest_root=Path("/tmp/suggest"),
        cfg=BundleSiteSuggestionsConfig(strategy=SiteSuggestionStrategy.MESH_BACKBONE, mesh_backbone=cfg),
        dem_mirror_root=Path("/tmp/dem"),
        eligible_sha="x",
        jobs=1,
        verbose=False,
        session_sites=[sites[1]],
        session_footprints={"relay": footprints["relay"]},
    )
    assert captured_goal_keys(cfg, sites, footprints) == {"g0"}
    adj = hop_adjacency(sites, footprints)
    assert "relay" in adj["seed"]
    with patch(
        "peaky_finders.site_suggestions.mesh_backbone_completion.footprints_for_backbone_sites",
        return_value=footprints,
    ):
        assert mesh_grow_planning_complete(ctx)
