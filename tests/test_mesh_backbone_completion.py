"""Mesh-backbone link completion checks."""

from __future__ import annotations

from shapely.geometry import box

from peaky_finders.site_suggestions.context import BackboneSite
from peaky_finders.site_suggestions.mesh_backbone_completion import (
    all_links_complete,
    evaluate_link_completion,
    evaluate_mesh_backbone_completion,
    hop_adjacency,
    order_sites_along_leg,
    sites_capturing_goal,
)
from peaky_finders.site_suggestions.mesh_backbone_geom import (
    AnchorPoint,
    build_link_leg,
    link_search_zone,
)
from peaky_finders.sites_job import MeshBackboneGoalEntry, MeshBackboneLinkEntry, MeshBackboneStrategyConfig


def _chain_fixtures():
    a = AnchorPoint(slug="g0", lat=39.0, lon=-115.85)
    b = AnchorPoint(slug="g2", lat=39.0, lon=-115.15)
    anchors = {"g0": a, "g2": b}
    leg = build_link_leg(a, b)
    eligible = box(-116.5, 38.5, -114.5, 39.5)
    zone = link_search_zone(leg, buffer_m=20_000.0, eligible_ll=eligible)
    assert zone is not None
    sites = [
        BackboneSite(slug="s0", lat=39.0, lon=-115.8),
        BackboneSite(slug="s1", lat=39.0, lon=-115.5),
        BackboneSite(slug="s2", lat=39.0, lon=-115.2),
    ]
    footprints = {
        "s0": box(-115.9, 38.9, -115.47, 39.1),
        "s1": box(-115.82, 38.95, -115.18, 39.05),
        "s2": box(-115.58, 38.9, -115.08, 39.1),
    }
    link = MeshBackboneLinkEntry(name="g0-g2", endpoints=("g0", "g2"))
    cfg = MeshBackboneStrategyConfig(
        goals={
            "g0": MeshBackboneGoalEntry(loc=(39.0, -115.85)),
            "g2": MeshBackboneGoalEntry(loc=(39.0, -115.15)),
        },
        links=[link],
        link_buffer_m=20_000.0,
    )
    return link, leg, zone, sites, footprints, anchors, cfg


def test_order_sites_along_leg() -> None:
    a = AnchorPoint(slug="a", lat=39.0, lon=-115.8)
    b = AnchorPoint(slug="b", lat=39.0, lon=-115.2)
    leg = build_link_leg(a, b)
    sites = [
        BackboneSite(slug="mid", lat=39.0, lon=-115.5),
        BackboneSite(slug="start", lat=39.0, lon=-115.8),
        BackboneSite(slug="end", lat=39.0, lon=-115.2),
    ]
    ordered = order_sites_along_leg(leg, sites)
    assert [s.slug for s in ordered] == ["start", "mid", "end"]


def test_hop_adjacency_mutual_footprint() -> None:
    sites = [
        BackboneSite(slug="s0", lat=39.0, lon=-115.8),
        BackboneSite(slug="s1", lat=39.0, lon=-115.5),
    ]
    footprints = {
        "s0": box(-115.9, 38.9, -115.4, 39.1),
        "s1": box(-115.85, 38.9, -115.15, 39.1),
    }
    adj = hop_adjacency(sites, footprints)
    assert "s1" in adj["s0"]
    assert "s0" in adj["s1"]


def test_sites_capturing_goal() -> None:
    goal = AnchorPoint(slug="g0", lat=39.0, lon=-115.8)
    sites = [
        BackboneSite(slug="near", lat=39.0, lon=-115.75),
        BackboneSite(slug="far", lat=39.0, lon=-115.2),
    ]
    footprints = {
        "near": box(-115.9, 38.9, -115.4, 39.1),
        "far": box(-115.35, 38.9, -115.1, 39.1),
    }
    assert sites_capturing_goal(goal, sites, footprints) == {"near"}


def test_evaluate_link_completion_multihop() -> None:
    link, leg, zone, sites, footprints, anchors, _cfg = _chain_fixtures()
    result = evaluate_link_completion(
        link=link,
        leg=leg,
        zone_ll=zone,
        anchors=anchors,
        sites=sites,
        footprints=footprints,
    )
    assert result.complete
    assert result.chain_slugs == ("s0", "s1", "s2")


def test_evaluate_link_completion_direct_hop() -> None:
    a = AnchorPoint(slug="g0", lat=39.0, lon=-115.85)
    b = AnchorPoint(slug="g2", lat=39.0, lon=-115.15)
    anchors = {"g0": a, "g2": b}
    leg = build_link_leg(a, b)
    eligible = box(-116.5, 38.5, -114.5, 39.5)
    zone = link_search_zone(leg, buffer_m=20_000.0, eligible_ll=eligible)
    assert zone is not None
    sites = [
        BackboneSite(slug="s0", lat=39.0, lon=-115.8),
        BackboneSite(slug="s2", lat=39.0, lon=-115.2),
    ]
    footprints = {
        "s0": box(-115.9, 38.9, -115.18, 39.1),
        "s2": box(-115.82, 38.9, -115.08, 39.1),
    }
    link = MeshBackboneLinkEntry(name="g0-g2", endpoints=("g0", "g2"))
    result = evaluate_link_completion(
        link=link,
        leg=leg,
        zone_ll=zone,
        anchors=anchors,
        sites=sites,
        footprints=footprints,
    )
    assert result.complete
    assert result.chain_slugs == ("s0", "s2")
    assert "direct" in result.detail


def test_evaluate_link_completion_fails_when_goal_not_captured() -> None:
    link, leg, zone, sites, _footprints, anchors, _cfg = _chain_fixtures()
    footprints = {
        "s1": box(-115.40, 38.95, -115.25, 39.05),
    }
    result = evaluate_link_completion(
        link=link,
        leg=leg,
        zone_ll=zone,
        anchors=anchors,
        sites=sites,
        footprints=footprints,
    )
    assert not result.complete
    assert "not captured" in result.detail


def test_evaluate_link_completion_fails_broken_hop() -> None:
    link, leg, zone, sites, footprints, anchors, _cfg = _chain_fixtures()
    footprints = {
        "s0": box(-115.9, 38.9, -115.75, 39.1),
        "s1": box(-115.40, 38.95, -115.25, 39.05),
        "s2": box(-115.6, 38.9, -115.1, 39.1),
    }
    result = evaluate_link_completion(
        link=link,
        leg=leg,
        zone_ll=zone,
        anchors=anchors,
        sites=sites,
        footprints=footprints,
    )
    assert not result.complete
    assert "hop" in result.detail.lower()


def test_evaluate_mesh_backbone_all_links() -> None:
    _link, _leg, _zone, sites, footprints, _anchors, cfg = _chain_fixtures()
    eligible = box(-116.5, 38.5, -114.5, 39.5)
    results = evaluate_mesh_backbone_completion(
        cfg=cfg,
        eligible_ll=eligible,
        sites=sites,
        footprints=footprints,
    )
    assert len(results) == 1
    assert all_links_complete(results)
