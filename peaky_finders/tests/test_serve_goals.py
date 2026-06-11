"""Append goals to preset YAML for ``peaky serve``."""

from __future__ import annotations

from pathlib import Path

import pytest

from rf_fixtures import MINIMAL_SIMULATION
from peaky_finders.serve_goals import (
    append_goal_to_preset,
    delete_goal_from_preset,
    unique_goal_slug,
    update_goal_in_preset,
)
from peaky_finders.sites_job import load_preset, write_preset_document


def test_unique_goal_slug_avoids_sites_and_goals() -> None:
    assert unique_goal_slug(set(), set(), "Hub") == "hub"
    assert unique_goal_slug({"hub"}, set(), "Hub") == "hub-2"
    assert unique_goal_slug(set(), set(), "Bridge") == "bridge"
    assert unique_goal_slug(set(), {"bridge"}, "Bridge") == "bridge-2"


def test_append_goal_to_preset(tmp_path: Path) -> None:
    preset_path = tmp_path / "config.yaml"
    write_preset_document(
        preset_path,
        {
            "simulation": dict(MINIMAL_SIMULATION),
            "display": {"colormap": "rainbow", "min_dbm": -130.0, "max_dbm": -80.0},
            "sites": {"hub": {"name": "Hub", "loc": [39.5, -119.5]}},
        },
    )
    slug = append_goal_to_preset(
        preset_path,
        name="Valley Bridge",
        lat=39.6,
        lon=-119.4,
    )
    assert slug == "valley-bridge"
    preset = load_preset(preset_path)
    assert preset.goals["valley-bridge"].name == "Valley Bridge"


def test_append_goal_rejects_site_slug_collision(tmp_path: Path) -> None:
    preset_path = tmp_path / "config.yaml"
    write_preset_document(
        preset_path,
        {
            "simulation": dict(MINIMAL_SIMULATION),
            "display": {"colormap": "rainbow", "min_dbm": -130.0, "max_dbm": -80.0},
            "sites": {"hub": {"name": "Hub", "loc": [39.5, -119.5]}},
            "goals": {"hub": {"name": "Hub goal", "loc": [39.6, -119.4]}},
        },
    )
    with pytest.raises(ValueError, match="collides"):
        load_preset(preset_path)


def test_delete_goal_from_preset(tmp_path: Path) -> None:
    preset_path = tmp_path / "config.yaml"
    write_preset_document(
        preset_path,
        {
            "simulation": dict(MINIMAL_SIMULATION),
            "display": {"colormap": "rainbow", "min_dbm": -130.0, "max_dbm": -80.0},
            "sites": {"hub": {"name": "Hub", "loc": [39.5, -119.5]}},
            "goals": {"bridge": {"name": "Bridge", "loc": [39.6, -119.4]}},
        },
    )
    assert delete_goal_from_preset(preset_path, "bridge") == "bridge"
    preset = load_preset(preset_path)
    assert "bridge" not in preset.goals


def test_delete_goal_missing_raises(tmp_path: Path) -> None:
    preset_path = tmp_path / "config.yaml"
    write_preset_document(
        preset_path,
        {
            "simulation": dict(MINIMAL_SIMULATION),
            "display": {"colormap": "rainbow", "min_dbm": -130.0, "max_dbm": -80.0},
            "sites": {"hub": {"name": "Hub", "loc": [39.5, -119.5]}},
        },
    )
    with pytest.raises(ValueError, match="not found"):
        delete_goal_from_preset(preset_path, "missing")


def test_update_goal_promotes_preserving_fields(tmp_path: Path) -> None:
    preset_path = tmp_path / "config.yaml"
    write_preset_document(
        preset_path,
        {
            "simulation": dict(MINIMAL_SIMULATION),
            "display": {"colormap": "rainbow", "min_dbm": -130.0, "max_dbm": -80.0},
            "sites": {"hub": {"name": "Hub", "loc": [39.5, -119.5]}},
            "goals": {
                "bridge": {
                    "name": "Bridge",
                    "loc": [39.6, -119.4],
                    "description": "valley crossing",
                    "plss": "T24N R19E",
                },
            },
        },
    )
    update_goal_in_preset(
        preset_path,
        "bridge",
        name="Bridge site",
        lat=39.61,
        lon=-119.41,
        promote_site_type="planned",
    )
    preset = load_preset(preset_path)
    assert "bridge" not in preset.goals
    site = preset.sites["bridge"]
    assert site.name == "Bridge site"
    assert site.lat == 39.61
    assert site.lon == -119.41
    assert site.type.value == "planned"
    assert site.description == "valley crossing"
    assert site.plss == "T24N R19E"
