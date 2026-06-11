"""Mesh connectivity pass (components, synthetic bridge goals)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from shapely.geometry import box

from peaky_finders.site_suggestions.context import BackboneSite, SiteSuggestionContext
from peaky_finders.site_suggestions.providers.mesh_backbone.completion import (
    anchor_slugs,
    hop_adjacency,
    hop_connected_components,
    mesh_connectivity_complete,
    mesh_grow_goals_complete,
)
from peaky_finders.site_suggestions.mesh_connectivity import (
    healing_context,
    mesh_healing_needed,
    minimum_component_gap,
    satellite_in_main_component,
    uncaptured_healing_goals,
)
from peaky_finders.site_suggestions.providers.mesh_backbone.scoring import grow_goals, main_footprint_slugs
from rf_fixtures import make_goal_entry
from peaky_finders.sites_job import (
    SuggestConfig,
    MeshBackboneStrategyConfig,
    SiteEntry,
    SiteSuggestionStrategy,
    SiteType,
)


def test_hop_connected_components_two_islands() -> None:
    sites = [
        BackboneSite(slug="a", lat=39.0, lon=-116.0),
        BackboneSite(slug="b", lat=39.0, lon=-115.9),
        BackboneSite(slug="c", lat=39.0, lon=-115.0),
        BackboneSite(slug="d", lat=39.0, lon=-114.9),
    ]
    footprints = {
        "a": box(-116.1, 38.9, -115.85, 39.1),
        "b": box(-116.0, 38.9, -115.75, 39.1),
        "c": box(-115.15, 38.9, -114.85, 39.1),
        "d": box(-115.05, 38.9, -114.75, 39.1),
    }
    adj = hop_adjacency(sites, footprints)
    components = hop_connected_components(adj, [s.slug for s in sites])
    assert len(components) == 2
    assert {frozenset(c) for c in components} == {frozenset({"a", "b"}), frozenset({"c", "d"})}


def test_anchor_slugs_excludes_suggested() -> None:
    preset = type(
        "P",
        (),
        {
            "sites": {
                "seed": SiteEntry(type=SiteType.INSTALLED, name="Seed", loc=(39.0, -115.8)),
                "suggest-01": SiteEntry(type=SiteType.SUGGESTED, name="S", loc=(39.0, -115.7)),
            }
        },
    )()
    assert anchor_slugs(preset) == {"seed"}


def test_bridge_uncaptured_until_satellite_in_main_hop_component() -> None:
    sites = [
        BackboneSite(slug="main", lat=41.5, lon=-119.0),
        BackboneSite(slug="sat", lat=36.2, lon=-115.3),
    ]
    pin_only = box(-120.0, 35.0, -114.0, 42.0)
    footprints_pin_only = {"main": pin_only, "sat": box(-115.4, 36.1, -115.2, 36.3)}
    footprints_hop = {
        "main": box(-120.0, 35.0, -114.0, 42.0),
        "sat": box(-119.5, 36.0, -115.0, 41.8),
    }
    preset = type(
        "P",
        (),
        {
            "sites": {
                "main": SiteEntry(type=SiteType.INSTALLED, name="Main", loc=(41.5, -119.0)),
                "sat": SiteEntry(type=SiteType.INSTALLED, name="Sat", loc=(36.2, -115.3)),
            },
            "goals": {},
            "repeaters": {
                "main": SiteEntry(type=SiteType.INSTALLED, name="Main", loc=(41.5, -119.0)),
                "sat": SiteEntry(type=SiteType.INSTALLED, name="Sat", loc=(36.2, -115.3)),
            },
        },
    )()
    ctx = SiteSuggestionContext(
        preset=preset,
        plan=type("Plan", (), {"viewshed_workspaces": ()})(),
        grid=type("G", (), {"depth_at_point": lambda *a, **k: 0})(),
        eligible_ll=box(-120.0, 35.0, -114.0, 42.0),
        aoi_ll=box(-120.0, 35.0, -114.0, 42.0),
        target_ll=box(-120.0, 35.0, -114.0, 42.0),
        suggest_root=Path("/tmp/suggest"),
        cfg=SuggestConfig(strategy=SiteSuggestionStrategy.MESH_BACKBONE),
        dem_mirror_root=Path("/tmp/dem"),
        eligible_sha="x",
        jobs=1,
        verbose=False,
    )
    with patch(
        "peaky_finders.site_suggestions.providers.mesh_backbone.completion.footprints_for_backbone_sites",
        return_value=footprints_pin_only,
    ):
        assert "bridge:sat" in uncaptured_healing_goals(ctx)
        assert not satellite_in_main_component(
            satellite_slug="sat",
            sites=sites,
            footprints=footprints_pin_only,
        )
    with patch(
        "peaky_finders.site_suggestions.providers.mesh_backbone.completion.footprints_for_backbone_sites",
        return_value=footprints_hop,
    ):
        assert "bridge:sat" not in uncaptured_healing_goals(ctx)
        assert satellite_in_main_component(
            satellite_slug="sat",
            sites=sites,
            footprints=footprints_hop,
        )


def test_mesh_healing_needed_when_disconnected_even_if_goals_captured() -> None:
    goals = {"g0": make_goal_entry("G0", (39.0, -115.95))}
    sites = [
        BackboneSite(slug="seed", lat=39.0, lon=-115.98),
        BackboneSite(slug="relay", lat=39.0, lon=-115.92),
        BackboneSite(slug="orphan", lat=39.0, lon=-115.05),
    ]
    footprints = {
        "seed": box(-116.1, 38.9, -115.85, 39.1),
        "relay": box(-116.05, 38.9, -115.8, 39.1),
        "orphan": box(-115.2, 38.9, -114.9, 39.1),
    }
    preset = type(
        "P",
        (),
        {
            "sites": {
                "seed": SiteEntry(type=SiteType.INSTALLED, name="Seed", loc=(39.0, -115.98)),
                **goals,
            },
            "goals": goals,
            "repeaters": {
                "seed": SiteEntry(type=SiteType.INSTALLED, name="Seed", loc=(39.0, -115.98)),
            },
        },
    )()
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
        session_sites=[sites[1], sites[2]],
        session_footprints={"relay": footprints["relay"], "orphan": footprints["orphan"]},
    )
    with patch(
        "peaky_finders.site_suggestions.providers.mesh_backbone.completion.footprints_for_backbone_sites",
        return_value=footprints,
    ), patch(
        "peaky_finders.site_suggestions.providers.mesh_backbone.completion._rf_viable_goal_site_pairs",
        return_value={("g0", "relay")},
    ):
        assert mesh_grow_goals_complete(ctx)
        assert not mesh_connectivity_complete(ctx)
        assert mesh_healing_needed(ctx)


def test_minimum_component_gap_uses_pins_not_overlapping_footprints() -> None:
    """Overlapping footprint unions must not collapse the bridge gap to 0 km."""
    sites = [
        BackboneSite(slug="west", lat=39.0, lon=-116.0),
        BackboneSite(slug="east", lat=39.0, lon=-115.0),
    ]
    footprints = {
        "west": box(-116.5, 38.9, -115.05, 39.1),
        "east": box(-115.95, 38.9, -114.5, 39.1),
    }
    ctx = SiteSuggestionContext(
        preset=type("P", (), {"sites": {}, "goals": {}, "repeaters": {}})(),
        plan=type("Plan", (), {"viewshed_workspaces": ()})(),
        grid=type("G", (), {"depth_at_point": lambda *a, **k: 0})(),
        eligible_ll=box(-117.0, 38.5, -114.0, 39.5),
        aoi_ll=box(-117.0, 38.5, -114.0, 39.5),
        target_ll=box(-117.0, 38.5, -114.0, 39.5),
        suggest_root=Path("/tmp/suggest"),
        cfg=SuggestConfig(strategy=SiteSuggestionStrategy.MESH_BACKBONE),
        dem_mirror_root=Path("/tmp/dem"),
        eligible_sha="x",
        jobs=1,
        verbose=False,
        session_sites=sites,
        session_footprints=footprints,
    )
    with patch(
        "peaky_finders.site_suggestions.providers.mesh_backbone.completion.footprints_for_backbone_sites",
        return_value=footprints,
    ):
        gap = minimum_component_gap(ctx)
    assert gap is not None
    assert gap.site_a_slug in {"west", "east"}
    assert gap.site_b_slug in {"west", "east"}
    assert gap.site_a_slug != gap.site_b_slug
    assert gap.distance_m > 50_000.0


def test_healing_context_yields_bridge_goals_on_satellite_pins() -> None:
    sites = [
        BackboneSite(slug="main", lat=41.5, lon=-119.0),
        BackboneSite(slug="sat", lat=36.2, lon=-115.3),
    ]
    footprints = {
        "main": box(-119.1, 41.4, -118.9, 41.6),
        "sat": box(-115.4, 36.1, -115.2, 36.3),
    }
    preset = type(
        "P",
        (),
        {
            "sites": {
                "main": SiteEntry(type=SiteType.INSTALLED, name="Main", loc=(41.5, -119.0)),
                "sat": SiteEntry(type=SiteType.INSTALLED, name="Sat", loc=(36.2, -115.3)),
                "g0": make_goal_entry("G0", (39.0, -115.5)),
            },
            "goals": {"g0": make_goal_entry("G0", (39.0, -115.5))},
            "repeaters": {
                "main": SiteEntry(type=SiteType.INSTALLED, name="Main", loc=(41.5, -119.0)),
                "sat": SiteEntry(type=SiteType.INSTALLED, name="Sat", loc=(36.2, -115.3)),
            },
        },
    )()
    ctx = SiteSuggestionContext(
        preset=preset,
        plan=type("Plan", (), {"viewshed_workspaces": ()})(),
        grid=type("G", (), {"depth_at_point": lambda *a, **k: 0})(),
        eligible_ll=box(-120.0, 35.0, -114.0, 42.0),
        aoi_ll=box(-120.0, 35.0, -114.0, 42.0),
        target_ll=box(-120.0, 35.0, -114.0, 42.0),
        suggest_root=Path("/tmp/suggest"),
        cfg=SuggestConfig(strategy=SiteSuggestionStrategy.MESH_BACKBONE),
        dem_mirror_root=Path("/tmp/dem"),
        eligible_sha="x",
        jobs=1,
        verbose=False,
    )
    with patch(
        "peaky_finders.site_suggestions.providers.mesh_backbone.completion.footprints_for_backbone_sites",
        return_value=footprints,
    ):
        healing = healing_context(ctx)
        assert healing is not None
        assert healing.bridge_goals["bridge:sat"].lat == 36.2
        assert "main" in healing.main_slugs
        assert "sat" not in healing.main_slugs
        goals = grow_goals(ctx)
        assert goals == healing.bridge_goals
        assert main_footprint_slugs(ctx) == healing.main_slugs


def test_healing_goals_one_bridge_goal_per_satellite_component() -> None:
    sites = [
        BackboneSite(slug="main", lat=41.5, lon=-119.0),
        BackboneSite(slug="sat-a", lat=36.2, lon=-115.3),
        BackboneSite(slug="sat-b", lat=38.0, lon=-114.5),
    ]
    footprints = {
        "main": box(-119.1, 41.4, -118.9, 41.6),
        "sat-a": box(-115.4, 36.1, -115.2, 36.3),
        "sat-b": box(-114.6, 37.9, -114.4, 38.1),
    }
    preset = type(
        "P",
        (),
        {
            "sites": {
                slug: SiteEntry(type=SiteType.INSTALLED, name=slug, loc=(sites[i].lat, sites[i].lon))
                for i, slug in enumerate(["main", "sat-a", "sat-b"])
            },
            "goals": {},
            "repeaters": {
                slug: SiteEntry(type=SiteType.INSTALLED, name=slug, loc=(sites[i].lat, sites[i].lon))
                for i, slug in enumerate(["main", "sat-a", "sat-b"])
            },
        },
    )()
    ctx = SiteSuggestionContext(
        preset=preset,
        plan=type("Plan", (), {"viewshed_workspaces": ()})(),
        grid=type("G", (), {"depth_at_point": lambda *a, **k: 0})(),
        eligible_ll=box(-120.0, 35.0, -114.0, 42.0),
        aoi_ll=box(-120.0, 35.0, -114.0, 42.0),
        target_ll=box(-120.0, 35.0, -114.0, 42.0),
        suggest_root=Path("/tmp/suggest"),
        cfg=SuggestConfig(strategy=SiteSuggestionStrategy.MESH_BACKBONE),
        dem_mirror_root=Path("/tmp/dem"),
        eligible_sha="x",
        jobs=1,
        verbose=False,
    )
    with patch(
        "peaky_finders.site_suggestions.providers.mesh_backbone.completion.footprints_for_backbone_sites",
        return_value=footprints,
    ):
        goals = grow_goals(ctx)
    assert set(goals) == {"bridge:sat-a", "bridge:sat-b"}


def test_mesh_connectivity_incomplete_when_two_anchor_islands() -> None:
    """All-installed islands must not read as unified (each anchor reaches itself)."""
    sites = [
        BackboneSite(slug="slpt", lat=41.5, lon=-119.0),
        BackboneSite(slug="vegas", lat=36.2, lon=-115.3),
    ]
    footprints = {
        "slpt": box(-119.1, 41.4, -118.9, 41.6),
        "vegas": box(-115.4, 36.1, -115.2, 36.3),
    }
    preset = type(
        "P",
        (),
        {
            "sites": {
                "slpt": SiteEntry(type=SiteType.INSTALLED, name="SLPT", loc=(41.5, -119.0)),
                "vegas": SiteEntry(type=SiteType.INSTALLED, name="Vegas", loc=(36.2, -115.3)),
            },
            "goals": {},
            "repeaters": {
                "slpt": SiteEntry(type=SiteType.INSTALLED, name="SLPT", loc=(41.5, -119.0)),
                "vegas": SiteEntry(type=SiteType.INSTALLED, name="Vegas", loc=(36.2, -115.3)),
            },
        },
    )()
    ctx = SiteSuggestionContext(
        preset=preset,
        plan=type("Plan", (), {"viewshed_workspaces": ()})(),
        grid=type("G", (), {"depth_at_point": lambda *a, **k: 0})(),
        eligible_ll=box(-120.0, 35.0, -114.0, 42.0),
        aoi_ll=box(-120.0, 35.0, -114.0, 42.0),
        target_ll=box(-120.0, 35.0, -114.0, 42.0),
        suggest_root=Path("/tmp/suggest"),
        cfg=SuggestConfig(strategy=SiteSuggestionStrategy.MESH_BACKBONE),
        dem_mirror_root=Path("/tmp/dem"),
        eligible_sha="x",
        jobs=1,
        verbose=False,
    )
    with patch(
        "peaky_finders.site_suggestions.providers.mesh_backbone.completion.footprints_for_backbone_sites",
        return_value=footprints,
    ):
        assert not mesh_connectivity_complete(ctx)
        assert mesh_healing_needed(ctx)
