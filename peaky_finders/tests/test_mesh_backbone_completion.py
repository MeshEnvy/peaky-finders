"""Mesh-grow completion and hop checks."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from shapely.geometry import box

from peaky_finders.site_suggestions.context import BackboneSite, SiteSuggestionContext
from peaky_finders.site_suggestions.providers.mesh_backbone.completion import (
    captured_goal_keys,
    hop_adjacency,
    mesh_grow_planning_complete,
    mutual_hop_neighbors,
    sites_capturing_goal,
)
from peaky_finders.site_suggestions.providers.mesh_backbone.geom import GoalPoint
from peaky_finders.sites_job import (
    Preset,
    SimulationConfig,
    SuggestConfig,
    SiteEntry,
    SiteSuggestionStrategy,
    SiteType,
    DisplayConfig,
)
from rf_fixtures import MINIMAL_SIMULATION, make_goal_entry


def _minimal_preset(*, goals: dict[str, SiteEntry] | None = None) -> Preset:
    sites: dict[str, SiteEntry] = {
        "seed": SiteEntry(type=SiteType.INSTALLED, name="Seed", loc=(39.0, -115.8)),
    }
    if goals:
        sites.update(goals)
    return Preset(
        simulation=SimulationConfig.model_validate(MINIMAL_SIMULATION),
        display=DisplayConfig(colormap="rainbow", min_dbm=-130.0, max_dbm=-80.0),
        sites=sites,
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


def test_sites_capturing_goal_requires_footprint_and_rf() -> None:
    goal = GoalPoint(key="g0", lat=39.0, lon=-115.8)
    sites = [BackboneSite(slug="near", lat=39.0, lon=-115.75)]
    footprints = {"near": box(-115.9, 38.9, -115.4, 39.1)}
    preset = _minimal_preset(
        goals={"g0": make_goal_entry("G0", (39.0, -115.8))},
    )
    assert (
        sites_capturing_goal(
            goal,
            sites,
            footprints,
            preset=preset,
            rf_pairs={("g0", "near")},
        )
        == {"near"}
    )
    assert (
        sites_capturing_goal(
            goal,
            sites,
            footprints,
            preset=preset,
            rf_pairs=set(),
        )
        == set()
    )


def test_mesh_grow_planning_complete_when_goals_captured_and_connected() -> None:
    goals = {"g0": make_goal_entry("G0", (39.0, -115.85))}
    preset = _minimal_preset(goals=goals)
    sites = [
        BackboneSite(slug="seed", lat=39.0, lon=-115.8),
        BackboneSite(slug="relay", lat=39.0, lon=-115.65),
    ]
    footprints = {
        "seed": box(-115.9, 38.9, -115.4, 39.1),
        "relay": box(-115.85, 38.9, -115.15, 39.1),
    }
    ctx = SiteSuggestionContext(
        preset=preset,
        plan=type("Plan", (), {"viewshed_workspaces": ()})(),
        grid=type("G", (), {"depth_at_point": lambda *a, **k: 1})(),
        eligible_ll=box(-116.5, 38.5, -114.5, 39.5),
        aoi_ll=box(-116.5, 38.5, -114.5, 39.5),
        target_ll=box(-116.5, 38.5, -114.5, 39.5),
        suggest_root=Path("/tmp/suggest"),
        cfg=SuggestConfig(strategy=SiteSuggestionStrategy.MESH_BACKBONE),
        dem_mirror_root=Path("/tmp/dem"),
        eligible_sha="x",
        jobs=1,
        verbose=False,
        session_sites=[sites[1]],
        session_footprints={"relay": footprints["relay"]},
    )
    with patch(
        "peaky_finders.site_suggestions.providers.mesh_backbone.completion._rf_viable_goal_site_pairs",
        return_value={("g0", "relay")},
    ):
        assert captured_goal_keys(goals, sites, footprints, preset) == {"g0"}
    adj = hop_adjacency(sites, footprints)
    assert "relay" in adj["seed"]
    with patch(
        "peaky_finders.site_suggestions.providers.mesh_backbone.completion.footprints_for_backbone_sites",
        return_value=footprints,
    ), patch(
        "peaky_finders.site_suggestions.providers.mesh_backbone.completion._rf_viable_goal_site_pairs",
        return_value={("g0", "relay")},
    ):
        assert mesh_grow_planning_complete(ctx)
