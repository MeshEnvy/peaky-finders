"""Top-level ``links`` preset validation."""

from __future__ import annotations

import pytest

from fixture_paths import SAMPLE_PROJECT_CONFIG

from peaky_finders.sites_job import (
    CoverageProvider,
    Preset,
    SimulationConfig,
    load_preset,
    parse_preset_dict,
)


def _minimal_simulation() -> SimulationConfig:
    return SimulationConfig.model_validate(
        {
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
            "transmitter": {"height_m": 2.0, "gain_dbi": 3.0, "loss_db": 2.0},
            "receiver": {"height_m": 2.0, "gain_dbi": 3.0, "loss_db": 2.0},
            "provider": CoverageProvider.LOS,
            "radius_km": 60.0,
        }
    )


def test_links_unknown_slug_raises() -> None:
    with pytest.raises(ValueError, match="links references unknown site slug 'missing'"):
        Preset.model_validate(
            {
                "simulation": _minimal_simulation(),
                "display": {"colormap": "plasma", "min_dbm": -130.0, "max_dbm": -80.0},
                "sites": {
                    "a": {"name": "A", "loc": [39.0, -119.0]},
                    "b": {"name": "B", "loc": [39.1, -119.1]},
                },
                "links": [["a", "missing"]],
            }
        )


def test_links_rejects_mapping_form() -> None:
    with pytest.raises(ValueError, match="links must be a list of"):
        Preset.model_validate(
            {
                "simulation": _minimal_simulation(),
                "display": {"colormap": "plasma", "min_dbm": -130.0, "max_dbm": -80.0},
                "sites": {
                    "a": {"name": "A", "loc": [39.0, -119.0]},
                    "b": {"name": "B", "loc": [39.1, -119.1]},
                },
                "links": {"a": "b"},
            }
        )


def test_links_rejects_legacy_site_sees() -> None:
    with pytest.raises(ValueError, match="sites.a.sees is removed"):
        parse_preset_dict(
            {
                "simulation": _minimal_simulation().model_dump(),
                "display": {"colormap": "plasma", "min_dbm": -130.0, "max_dbm": -80.0},
                "sites": {
                    "a": {"name": "A", "loc": [39.0, -119.0], "sees": ["b"]},
                    "b": {"name": "B", "loc": [39.1, -119.1]},
                },
            }
        )


def test_sample_preset_hub_links() -> None:
    job = load_preset(SAMPLE_PROJECT_CONFIG)
    assert job.links == [
        ("hub", "peer-a"),
        ("hub", "peer-b"),
        ("hub", "peer-c"),
    ]
