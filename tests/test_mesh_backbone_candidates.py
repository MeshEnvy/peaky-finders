"""Mesh-backbone candidate generation tests."""

from __future__ import annotations

from pathlib import Path

from shapely.geometry import box

from peaky_finders.site_suggestions.depth_grid import build_coverage_depth_grid
from peaky_finders.site_suggestions.mesh_backbone_candidates import (
    candidates_for_incomplete_link,
    generate_mesh_backbone_candidates,
    open_anchor_bias_for_link,
)
from peaky_finders.site_suggestions.mesh_backbone_completion import (
    BackboneSite,
    LinkCompletionResult,
    evaluate_link_completion,
)
from peaky_finders.site_suggestions.mesh_backbone_geom import (
    AnchorPoint,
    build_link_leg,
    link_search_zone,
)
from peaky_finders.site_suggestions.context import SiteSuggestionContext
from peaky_finders.sites_job import (
    BundleSiteSuggestionsConfig,
    MeshBackboneLinkEntry,
    MeshBackboneStrategyConfig,
    SiteSuggestionStrategy,
)
from peaky_finders.site_suggestions.strategies.mesh_backbone import MeshBackboneStrategy
from peaky_finders.site_suggestions.strategies.registry import resolve_site_suggestion_strategy


def _link_fixture():
    a = AnchorPoint(key="a", lat=39.0, lon=-115.8)
    b = AnchorPoint(key="b", lat=39.0, lon=-115.2)
    leg = build_link_leg(a, b)
    eligible = box(-116.5, 38.5, -114.5, 39.5)
    zone = link_search_zone(leg, buffer_m=20_000.0, eligible_ll=eligible)
    assert zone is not None
    link = MeshBackboneLinkEntry(name="a-b", endpoints=("a", "b"))
    return a, b, leg, zone, eligible, link


def test_open_anchor_bias_when_start_missing() -> None:
    a, b, leg, zone, _eligible, link = _link_fixture()
    sites = [BackboneSite(slug="mid", lat=39.0, lon=-115.5)]
    bias = open_anchor_bias_for_link(
        link=link,
        leg=leg,
        zone_ll=zone,
        anchors={"a": a, "b": b},
        sites=sites,
        footprints={"mid": box(-115.6, 38.9, -115.4, 39.1)},
        endpoint_capture_m=5000.0,
    )
    assert bias.anchor_key == "a"
    assert bias.toward_leg_start is True


def test_open_anchor_bias_when_end_missing() -> None:
    a, b, leg, zone, _eligible, link = _link_fixture()
    sites = [BackboneSite(slug="s0", lat=39.0, lon=-115.8)]
    bias = open_anchor_bias_for_link(
        link=link,
        leg=leg,
        zone_ll=zone,
        anchors={"a": a, "b": b},
        sites=sites,
        footprints={"s0": box(-115.9, 38.9, -115.4, 39.1)},
        endpoint_capture_m=5000.0,
    )
    assert bias.anchor_key == "b"
    assert bias.toward_leg_start is False


def test_candidates_for_incomplete_link_biased_toward_open_end() -> None:
    a, b, leg, zone, eligible, link = _link_fixture()
    aoi = eligible
    grid = build_coverage_depth_grid(
        aoi_ll=aoi,
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
        anchors={"a": a, "b": b},
        sites=sites,
        footprints=footprints,
        endpoint_capture_m=5000.0,
    )
    assert not result.complete
    cfg = MeshBackboneStrategyConfig(
        link_buffer_m=20_000.0,
        sample_spacing_m=10_000.0,
        endpoint_capture_m=5000.0,
        max_candidates_per_round=8,
    )
    cands = candidates_for_incomplete_link(
        result=result,
        leg=leg,
        zone_ll=zone,
        anchors={"a": a, "b": b},
        sites=sites,
        footprints=footprints,
        grid=grid,
        cfg=cfg,
        goal_depth=1,
        per_link_cap=8,
    )
    assert cands
    assert all(c.strategy == "link:a-b" for c in cands)
    # Open end is B; first kept sample should be closer to B than A.
    first = cands[0]
    assert first.lon > -115.5


def test_generate_mesh_backbone_candidates_skips_complete_links() -> None:
    a, b, leg, zone, eligible, link = _link_fixture()
    grid = build_coverage_depth_grid(
        aoi_ll=eligible,
        target_ll=eligible,
        footprint_gpkg_paths=[],
        max_raster_dimension=256,
    )
    sites = [
        BackboneSite(slug="s0", lat=39.0, lon=-115.8),
        BackboneSite(slug="s2", lat=39.0, lon=-115.2),
    ]
    footprints = {
        "s0": box(-115.9, 38.9, -115.1, 39.1),
        "s2": box(-115.9, 38.9, -115.1, 39.1),
    }
    ctx = SiteSuggestionContext(
        preset=type(
            "P",
            (),
            {
                "sites": {
                    "s0": type("E", (), {"lat": 39.0, "lon": -115.8})(),
                    "s2": type("E", (), {"lat": 39.0, "lon": -115.2})(),
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
                link_buffer_m=20_000.0,
                sample_spacing_m=10_000.0,
                endpoint_capture_m=5000.0,
                anchors={"a": (a.lat, a.lon), "b": (b.lat, b.lon)},
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
    from peaky_finders.site_suggestions.context import SiteSuggestionContext
    from peaky_finders.site_suggestions.mesh_backbone_completion import BackboneSite

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
                anchors={"a": (39.0, -115.8), "b": (39.0, -115.2)},
                links=[MeshBackboneLinkEntry(endpoints=("a", "b"))],
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
