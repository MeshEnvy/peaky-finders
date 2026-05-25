"""Mesh-backbone candidate generation tests."""

from __future__ import annotations

from pathlib import Path

from shapely.geometry import box

from peaky_finders.site_suggestions.context import BackboneSite, SiteSuggestionContext
from peaky_finders.site_suggestions.depth_grid import build_coverage_depth_grid
from peaky_finders.site_suggestions.mesh_backbone_candidates import (
    candidates_for_incomplete_link,
    generate_mesh_backbone_candidates,
    open_endpoint_bias_for_link,
)
from peaky_finders.site_suggestions.mesh_backbone_completion import (
    evaluate_link_completion,
)
from peaky_finders.site_suggestions.mesh_backbone_geom import (
    AnchorPoint,
    build_link_leg,
    link_search_zone,
)
from peaky_finders.sites_job import (
    BundleSiteSuggestionsConfig,
    MeshBackboneLinkEntry,
    MeshBackboneStrategyConfig,
    SiteSuggestionStrategy,
)
from peaky_finders.site_suggestions.strategies.mesh_backbone import MeshBackboneStrategy
from peaky_finders.site_suggestions.strategies.registry import resolve_site_suggestion_strategy


def _site_entry(lat: float, lon: float) -> object:
    return type("E", (), {"lat": float(lat), "lon": float(lon)})()


def _link_fixture():
    a = AnchorPoint(slug="s0", lat=39.0, lon=-115.8)
    b = AnchorPoint(slug="s2", lat=39.0, lon=-115.2)
    leg = build_link_leg(a, b)
    eligible = box(-116.5, 38.5, -114.5, 39.5)
    zone = link_search_zone(leg, buffer_m=20_000.0, eligible_ll=eligible)
    assert zone is not None
    link = MeshBackboneLinkEntry(name="s0-s2", endpoints=("s0", "s2"))
    anchors = {"s0": a, "s2": b}
    preset_sites = {
        "s0": _site_entry(39.0, -115.8),
        "s2": _site_entry(39.0, -115.2),
    }
    return link, leg, zone, eligible, anchors, preset_sites


def test_open_endpoint_bias_when_start_missing() -> None:
    link, leg, zone, _eligible, anchors, _preset_sites = _link_fixture()
    sites = [BackboneSite(slug="mid", lat=39.0, lon=-115.5)]
    bias = open_endpoint_bias_for_link(
        link=link,
        leg=leg,
        zone_ll=zone,
        anchors=anchors,
        sites=sites,
        footprints={"mid": box(-115.6, 38.9, -115.4, 39.1)},
    )
    assert bias.slug == "s0"
    assert bias.toward_leg_start is True


def test_open_endpoint_bias_when_end_missing() -> None:
    link, leg, zone, _eligible, anchors, _preset_sites = _link_fixture()
    sites = [BackboneSite(slug="s0", lat=39.0, lon=-115.8)]
    bias = open_endpoint_bias_for_link(
        link=link,
        leg=leg,
        zone_ll=zone,
        anchors=anchors,
        sites=sites,
        footprints={"s0": box(-115.9, 38.9, -115.4, 39.1)},
    )
    assert bias.slug == "s2"
    assert bias.toward_leg_start is False


def test_candidates_for_incomplete_link_biased_toward_open_end() -> None:
    link, leg, zone, eligible, anchors, _preset_sites = _link_fixture()
    grid = build_coverage_depth_grid(
        aoi_ll=eligible,
        target_ll=eligible,
        footprint_gpkg_paths=[],
        max_raster_dimension=256,
    )
    sites = [BackboneSite(slug="s0", lat=39.0, lon=-115.8)]
    footprints = {"s0": box(-115.9, 38.9, -115.4, 39.1)}
    result = evaluate_link_completion(
        link=link,
        leg=leg,
        zone_ll=zone,
        sites=sites,
        footprints=footprints,
    )
    assert not result.complete
    cfg = MeshBackboneStrategyConfig(
        link_buffer_m=20_000.0,
        sample_spacing_m=10_000.0,
        max_candidates_per_round=8,
    )
    cands = candidates_for_incomplete_link(
        result=result,
        leg=leg,
        zone_ll=zone,
        anchors=anchors,
        sites=sites,
        footprints=footprints,
        grid=grid,
        cfg=cfg,
        goal_depth=1,
        per_link_cap=8,
    )
    assert cands
    assert all(c.strategy == "link:s0-s2" for c in cands)
    first = cands[0]
    assert first.lon > -115.5


def test_generate_mesh_backbone_candidates_skips_complete_links() -> None:
    link, _leg, _zone, eligible, _anchors, preset_sites = _link_fixture()
    grid = build_coverage_depth_grid(
        aoi_ll=eligible,
        target_ll=eligible,
        footprint_gpkg_paths=[],
        max_raster_dimension=256,
    )
    footprints = {
        "s0": box(-115.9, 38.9, -115.1, 39.1),
        "s2": box(-115.9, 38.9, -115.1, 39.1),
    }
    ctx = SiteSuggestionContext(
        preset=type("P", (), {"sites": preset_sites})(),
        plan=type("Plan", (), {"viewshed_workspaces": ()})(),
        grid=grid,
        eligible_ll=eligible,
        aoi_ll=eligible,
        target_ll=eligible,
        suggest_root=Path("/tmp/suggest"),
        cfg=BundleSiteSuggestionsConfig(
            strategy=SiteSuggestionStrategy.MESH_BACKBONE,
            mesh_backbone=MeshBackboneStrategyConfig(
                link_buffer_m=20_000.0,
                sample_spacing_m=10_000.0,
                links=[link],
            ),
        ),
        dem_mirror_root=Path("/tmp/dem"),
        eligible_sha="x",
        jobs=1,
        verbose=False,
        session_footprints=footprints,
    )
    assert generate_mesh_backbone_candidates(ctx, goal_depth=1) == []


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
            mesh_backbone=MeshBackboneStrategyConfig(
                links=[MeshBackboneLinkEntry(endpoints=("s0", "s2"))],
                max_nodes=2,
            ),
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


def test_mesh_backbone_strategy_in_registry() -> None:
    provider = resolve_site_suggestion_strategy(
        BundleSiteSuggestionsConfig(strategy=SiteSuggestionStrategy.MESH_BACKBONE)
    )
    assert isinstance(provider, MeshBackboneStrategy)
    assert provider.name == "mesh-backbone"
    assert provider.goal_depth(BundleSiteSuggestionsConfig()) == 2
