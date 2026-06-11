"""Mesh-grow schema and goal geometry."""

from __future__ import annotations

from pathlib import Path

import pytest

from peaky_finders.site_suggestions.providers.mesh_backbone.geom import goals_from_preset
from peaky_finders.sites_job import (
    SuggestConfig,
    MeshBackboneStrategyConfig,
    SiteSuggestionStrategy,
    load_preset,
    write_preset_document,
)
from rf_fixtures import MINIMAL_SIMULATION, make_goal_entry

_PRESET_SIMULATION = MINIMAL_SIMULATION


def test_mesh_backbone_config_defaults() -> None:
    cfg = MeshBackboneStrategyConfig()
    assert cfg.max_candidates_per_round == 48
    assert cfg.frontier_sample_spacing_m == 500.0


def test_goals_from_preset() -> None:
    goals = {
        "reno": make_goal_entry("Reno", (39.5296, -119.8138)),
        "elko": make_goal_entry("Elko", (40.8324, -115.7631)),
    }
    pts = goals_from_preset(goals)
    assert set(pts) == {"elko", "reno"}
    assert pts["reno"].lat == pytest.approx(39.5296)


def test_preset_loads_top_level_goals(tmp_path: Path) -> None:
    preset_path = tmp_path / "job.yaml"
    write_preset_document(
        preset_path,
        {
            "simulation": dict(_PRESET_SIMULATION),
            "display": {"colormap": "rainbow", "min_dbm": -130.0, "max_dbm": -80.0},
            "suggest": {
                "strategy": "mesh-backbone",
                "mesh_backbone": {
                    "goal_order": ["goal-a", "goal-b"],
                },
            },
            "sites": {
                "seed": {"name": "Seed", "loc": [39.0, -119.0]},
                "goal-a": {"type": "goal", "name": "Goal A", "loc": [39.0, -119.0]},
                "goal-b": {"type": "goal", "name": "Goal B", "loc": [40.0, -118.0]},
            },
        },
    )
    preset = load_preset(preset_path)
    assert len(preset.goals) == 2
    assert preset.suggest.mesh_backbone.goal_order == ["goal-a", "goal-b"]
    assert preset.suggest.strategy == SiteSuggestionStrategy.MESH_BACKBONE


def test_mesh_backbone_strategy_in_registry() -> None:
    from peaky_finders.site_suggestions.providers.mesh_backbone import MeshBackboneStrategy
    from peaky_finders.site_suggestions.providers.registry import resolve_site_suggestion_strategy

    cfg = SuggestConfig(strategy=SiteSuggestionStrategy.MESH_BACKBONE)
    provider = resolve_site_suggestion_strategy(cfg)
    assert isinstance(provider, MeshBackboneStrategy)
    assert provider.name == "mesh-backbone"
    assert provider.goal_depth(cfg) == 1
