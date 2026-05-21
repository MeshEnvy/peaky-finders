"""``sites.<slug>.sees`` preset validation."""

from __future__ import annotations

import pytest

from fixture_paths import SAMPLE_PROJECT_CONFIG

from peaky_finders.sites_job import CoverageProvider, Preset, SimulationConfig, load_preset


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


def test_site_sees_unknown_slug_raises() -> None:
    with pytest.raises(ValueError, match="sites.a.sees references unknown site slug 'missing'"):
        Preset.model_validate(
            {
                "simulation": _minimal_simulation(),
                "display": {"colormap": "plasma", "min_dbm": -130.0, "max_dbm": -80.0},
                "sites": {
                    "a": {"name": "A", "loc": [39.0, -119.0], "sees": ["missing"]},
                },
            }
        )


def test_sample_preset_hub_sees_peers_and_peers_see_hub() -> None:
    job = load_preset(SAMPLE_PROJECT_CONFIG)
    assert job.sites["hub"].sees == ["peer-a", "peer-b", "peer-c"]
    assert job.sites["peer-a"].sees == ["hub"]
    assert job.sites["peer-b"].sees == ["hub"]
    assert job.sites["peer-c"].sees == ["hub"]
