"""Mesh-backbone schema and link geometry."""

from __future__ import annotations

from pathlib import Path

import pytest
from shapely.geometry import Point, box

from peaky_finders.site_suggestions.mesh_backbone_geom import (
    AnchorPoint,
    anchor_from_loc,
    build_link_leg,
    distance_to_link_m,
    link_search_zone,
    resolved_link_legs,
    sample_along_link,
)
from peaky_finders.sites_job import (
    BundleSiteSuggestionsConfig,
    MeshBackboneLinkEntry,
    MeshBackboneStrategyConfig,
    SiteSuggestionStrategy,
    load_preset,
    write_preset_document,
)

_PRESET_SIMULATION = {
    "modem_presets": {
        "meshcore-us": {
            "frequency_mhz": 910.525,
            "bandwidth_khz": 62.5,
            "spreading_factor": 7,
            "coding_rate": 5,
            "implementation_margin_db": 3.0,
            "power_dbm": 22.0,
            "sensitivity_dbm": -121.0,
        }
    },
    "environment_presets": {
        "test-desert": {
            "climate": "desert",
            "polarization": "vertical",
            "clutter_height_m": 1.0,
        }
    },
    "modem": "meshcore-us",
    "environment": "test-desert",
    "transmitter": {"height_m": 2.0, "gain_dbi": 2.0, "loss_db": 0.0},
    "receiver": {"height_m": 2.0, "gain_dbi": 2.0, "loss_db": 0.0},
}

_SILVER_TRIANGLE_ANCHORS = {
    "reno": (39.5296, -119.8138),
    "elko": (40.8324, -115.7631),
    "wells": (41.1116, -114.9647),
    "vegas": (36.1699, -115.1398),
}


def _silver_triangle_links() -> list[MeshBackboneLinkEntry]:
    return [
        MeshBackboneLinkEntry(name="reno-elko", endpoints=("reno", "elko")),
        MeshBackboneLinkEntry(name="elko-wells", endpoints=("elko", "wells")),
        MeshBackboneLinkEntry(name="wells-vegas", endpoints=("wells", "vegas")),
        MeshBackboneLinkEntry(name="reno-vegas", endpoints=("reno", "vegas")),
    ]


def test_build_link_leg_between_anchors() -> None:
    reno = anchor_from_loc("reno", _SILVER_TRIANGLE_ANCHORS["reno"])
    elko = anchor_from_loc("elko", _SILVER_TRIANGLE_ANCHORS["elko"])
    leg = build_link_leg(reno, elko)
    assert not leg.is_empty
    assert leg.length > 100_000.0


def test_resolved_link_legs_silver_triangle() -> None:
    cfg = MeshBackboneStrategyConfig(
        anchors=dict(_SILVER_TRIANGLE_ANCHORS),
        links=_silver_triangle_links(),
    )
    pairs = resolved_link_legs(cfg)
    assert len(pairs) == 4
    assert all(not leg.is_empty for _link, leg in pairs)


def test_link_search_zone_respects_eligible() -> None:
    reno = anchor_from_loc("reno", _SILVER_TRIANGLE_ANCHORS["reno"])
    elko = anchor_from_loc("elko", _SILVER_TRIANGLE_ANCHORS["elko"])
    leg = build_link_leg(reno, elko)
    eligible = box(-121.0, 35.0, -114.0, 42.0)
    zone = link_search_zone(leg, buffer_m=50_000.0, eligible_ll=eligible)
    assert zone is not None
    assert zone.intersects(Point(reno.lon, reno.lat))
    assert zone.within(eligible) or eligible.contains(zone)


def test_link_search_zone_clips_exclusion_hole() -> None:
    a = AnchorPoint(key="a", lat=39.0, lon=-116.0)
    b = AnchorPoint(key="b", lat=39.0, lon=-115.0)
    leg = build_link_leg(a, b)
    eligible = box(-116.5, 38.5, -114.5, 39.5).difference(box(-115.6, 38.9, -115.4, 39.1))
    zone = link_search_zone(leg, buffer_m=20_000.0, eligible_ll=eligible)
    assert zone is not None
    hole = box(-115.6, 38.9, -115.4, 39.1)
    assert not zone.intersects(hole.centroid)


def test_distance_to_link_m() -> None:
    a = AnchorPoint(key="a", lat=39.0, lon=-116.0)
    b = AnchorPoint(key="b", lat=39.0, lon=-115.0)
    leg = build_link_leg(a, b)
    on_link = distance_to_link_m(-115.5, 39.0, leg)
    far = distance_to_link_m(-115.5, 41.0, leg)
    assert on_link < 100.0
    assert far > 50_000.0


def test_sample_along_link_inside_zone() -> None:
    a = AnchorPoint(key="a", lat=39.0, lon=-116.0)
    b = AnchorPoint(key="b", lat=39.0, lon=-115.0)
    leg = build_link_leg(a, b)
    eligible = box(-116.5, 38.5, -114.5, 39.5)
    zone = link_search_zone(leg, buffer_m=15_000.0, eligible_ll=eligible)
    assert zone is not None
    samples = sample_along_link(leg, spacing_m=25_000.0, zone_ll=zone)
    assert len(samples) >= 2
    for lat, lon in samples:
        assert zone.contains(Point(lon, lat))


def test_mesh_backbone_config_validates_unknown_anchor() -> None:
    with pytest.raises(ValueError, match="unknown anchor"):
        MeshBackboneStrategyConfig(
            anchors={
                "reno": _SILVER_TRIANGLE_ANCHORS["reno"],
                "wells": _SILVER_TRIANGLE_ANCHORS["wells"],
            },
            links=[MeshBackboneLinkEntry(endpoints=("reno", "elko"))],
        )


def test_mesh_backbone_config_rejects_same_endpoint() -> None:
    with pytest.raises(ValueError, match="must differ"):
        MeshBackboneStrategyConfig(
            anchors={
                "reno": _SILVER_TRIANGLE_ANCHORS["reno"],
                "wells": _SILVER_TRIANGLE_ANCHORS["wells"],
            },
            links=[MeshBackboneLinkEntry(endpoints=("reno", "reno"))],
        )


def test_mesh_backbone_config_defaults() -> None:
    cfg = MeshBackboneStrategyConfig()
    assert cfg.site_goal_depth == 2
    assert cfg.links == []
    assert cfg.anchors == {}


def test_preset_loads_mesh_backbone_block(tmp_path: Path) -> None:
    preset_path = tmp_path / "job.yaml"
    write_preset_document(
        preset_path,
        {
            "simulation": dict(_PRESET_SIMULATION),
            "display": {"colormap": "rainbow", "min_dbm": -130.0, "max_dbm": -80.0},
            "bundle": {
                "site_suggestions": {
                    "strategy": "land-grab",
                    "mesh_backbone": {
                        "site_goal_depth": 2,
                        "link_buffer_m": 35000,
                        "anchors": {
                            "reno": [39.5296, -119.8138],
                            "elko": [40.8324, -115.7631],
                            "wells": [41.1116, -114.9647],
                            "vegas": [36.1699, -115.1398],
                        },
                        "links": [
                            {"name": "reno-elko", "endpoints": ["reno", "elko"]},
                            {"name": "wells-vegas", "endpoints": ["wells", "vegas"]},
                        ],
                    },
                }
            },
            "sites": {"seed": {"name": "Seed", "loc": [39.0, -119.0]}},
        },
    )
    preset = load_preset(preset_path)
    mb = preset.bundle.site_suggestions.mesh_backbone
    assert len(mb.anchors) == 4
    assert len(mb.links) == 2
    assert mb.links[0].name == "reno-elko"
    assert preset.bundle.site_suggestions.strategy == SiteSuggestionStrategy.LAND_GRAB


def test_mesh_backbone_strategy_in_registry() -> None:
    from peaky_finders.site_suggestions.strategies.mesh_backbone import MeshBackboneStrategy
    from peaky_finders.site_suggestions.strategies.registry import resolve_site_suggestion_strategy

    cfg = BundleSiteSuggestionsConfig(strategy=SiteSuggestionStrategy.MESH_BACKBONE)
    provider = resolve_site_suggestion_strategy(cfg)
    assert isinstance(provider, MeshBackboneStrategy)
    assert provider.name == "mesh-backbone"
