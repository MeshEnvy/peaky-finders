"""Append goals to preset YAML for ``peaky serve``."""

from __future__ import annotations

from pathlib import Path

import pytest

from peaky_finders.serve_goals import append_goal_to_preset, delete_goal_from_preset, unique_goal_slug
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
            "simulation": {
                "modem_presets": {
                    "m": {
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
                    "e": {
                        "climate": "desert",
                        "polarization": "vertical",
                        "clutter_height_m": 1.0,
                    }
                },
                "modem": "m",
                "environment": "e",
                "transmitter": {"height_m": 2.0, "gain_dbi": 2.0, "loss_db": 0.0},
                "receiver": {"height_m": 2.0, "gain_dbi": 2.0, "loss_db": 0.0},
            },
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
            "simulation": {
                "modem_presets": {
                    "m": {
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
                    "e": {
                        "climate": "desert",
                        "polarization": "vertical",
                        "clutter_height_m": 1.0,
                    }
                },
                "modem": "m",
                "environment": "e",
                "transmitter": {"height_m": 2.0, "gain_dbi": 2.0, "loss_db": 0.0},
                "receiver": {"height_m": 2.0, "gain_dbi": 2.0, "loss_db": 0.0},
            },
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
            "simulation": {
                "modem_presets": {
                    "m": {
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
                    "e": {
                        "climate": "desert",
                        "polarization": "vertical",
                        "clutter_height_m": 1.0,
                    }
                },
                "modem": "m",
                "environment": "e",
                "transmitter": {"height_m": 2.0, "gain_dbi": 2.0, "loss_db": 0.0},
                "receiver": {"height_m": 2.0, "gain_dbi": 2.0, "loss_db": 0.0},
            },
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
            "simulation": {
                "modem_presets": {
                    "m": {
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
                    "e": {
                        "climate": "desert",
                        "polarization": "vertical",
                        "clutter_height_m": 1.0,
                    }
                },
                "modem": "m",
                "environment": "e",
                "transmitter": {"height_m": 2.0, "gain_dbi": 2.0, "loss_db": 0.0},
                "receiver": {"height_m": 2.0, "gain_dbi": 2.0, "loss_db": 0.0},
            },
            "display": {"colormap": "rainbow", "min_dbm": -130.0, "max_dbm": -80.0},
            "sites": {"hub": {"name": "Hub", "loc": [39.5, -119.5]}},
        },
    )
    with pytest.raises(ValueError, match="not found"):
        delete_goal_from_preset(preset_path, "missing")
