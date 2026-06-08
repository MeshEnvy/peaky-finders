"""Preset RF mapping (simulation.modem_presets / environment_presets → SplatCoverageRequest)."""

from __future__ import annotations

import pytest

from peaky_finders.preset_mapping import (
    _lora_sensitivity_dbm,
    _preset_frequency_mhz,
    _preset_tx_power_dbm,
    modem_decode_threshold_dbm,
    preset_to_request,
    reliability_margin_db,
    resolved_environment,
    resolved_modem,
)
from peaky_finders.sites_job import CoverageProvider, Preset, SimulationConfig

_MESHCORE_US = {
    "frequency_mhz": 910.525,
    "bandwidth_khz": 62.5,
    "spreading_factor": 7,
    "coding_rate": 5,
    "implementation_margin_db": 3.0,
    "power_dbm": 22.0,
    "sensitivity_dbm": -121.0,
}

_TEST_DESERT = {
    "description": "Arid open terrain for unit tests",
    "climate": "desert",
    "polarization": "vertical",
    "clutter_height_m": 1.0,
    "ground_dielectric_v_m": 15.0,
    "ground_conductivity_s_m": 0.005,
    "atmosphere_bending_n": 301.0,
}


def _minimal_simulation(**updates: object) -> SimulationConfig:
    base = {
        "modem_presets": {"meshcore-us": dict(_MESHCORE_US)},
        "environment_presets": {"test-desert": dict(_TEST_DESERT)},
        "modem": "meshcore-us",
        "environment": "test-desert",
        "transmitter": {"height_m": 2.0, "gain_dbi": 3.0, "loss_db": 2.0},
        "receiver": {"height_m": 2.0, "gain_dbi": 3.0, "loss_db": 2.0},
        "provider": CoverageProvider.SPLATTER,
        "radius_km": 60.0,
    }
    base.update(updates)
    return SimulationConfig.model_validate(base)


def _minimal_preset(**updates: object) -> Preset:
    base = {
        "simulation": _minimal_simulation(),
        "display": {"colormap": "plasma", "min_dbm": -130.0, "max_dbm": -80.0},
        "sites": {"a": {"name": "A", "loc": [39.0, -119.0]}},
    }
    base.update(updates)
    return Preset.model_validate(base)


def test_resolved_modem_by_name() -> None:
    preset = _minimal_preset()
    assert resolved_modem(preset) == _MESHCORE_US


def test_resolved_environment_by_name() -> None:
    preset = _minimal_preset()
    env = resolved_environment(preset)
    assert env["climate"] == "desert"
    assert env["clutter_height_m"] == 1.0


def test_resolved_modem_with_overrides() -> None:
    sim = _minimal_simulation(modem={"preset": "meshcore-us", "power_dbm": 20.0})
    preset = _minimal_preset(simulation=sim)
    modem = resolved_modem(preset)
    assert modem["frequency_mhz"] == 910.525
    assert modem["power_dbm"] == 20.0


def test_unknown_modem_preset_raises() -> None:
    sim = _minimal_simulation(modem="missing")
    preset = _minimal_preset(simulation=sim)
    with pytest.raises(ValueError, match="unknown modem preset"):
        resolved_modem(preset)


def test_modem_frequency_and_power_from_preset() -> None:
    preset = _minimal_preset()
    assert _preset_frequency_mhz(preset) == 910.525
    assert _preset_tx_power_dbm(preset) == 22.0


def test_transmitter_power_overrides_modem_preset() -> None:
    preset = _minimal_preset()
    preset.simulation.transmitter["power_dbm"] = 19.0
    assert _preset_tx_power_dbm(preset) == 19.0


def test_lora_sensitivity_sf7_62khz() -> None:
    sens = _lora_sensitivity_dbm(
        {"spreading_factor": 7, "bandwidth_khz": 62.5, "coding_rate": 5}
    )
    assert sens == -126.01029995663981


def test_derived_modem_decode_without_modem_sensitivity() -> None:
    catalog = dict(_MESHCORE_US)
    catalog.pop("sensitivity_dbm")
    sim = _minimal_simulation()
    sim.modem_presets["meshcore-us"] = catalog
    preset = _minimal_preset(simulation=sim)
    modem = resolved_modem(preset)
    expected = modem_decode_threshold_dbm(modem)
    assert expected == pytest.approx(-123.01029995663981)


def test_preset_to_request_uses_simulation_presets() -> None:
    preset = _minimal_preset()
    req = preset_to_request(preset, 39.0, -119.0)
    assert req.frequency_mhz == 910.525
    assert req.tx_power == 22.0
    assert req.signal_threshold == pytest.approx(
        modem_decode_threshold_dbm(resolved_modem(preset))
        + reliability_margin_db(float(preset.simulation.situation_pct), float(preset.simulation.time_pct))
    )
    assert req.modem is not None
    assert req.modem.spreading_factor == 7
    assert req.modem.bandwidth_khz == 62.5
    assert req.modem.implementation_margin_db == 3.0
    assert req.modem.sensitivity_dbm == -121.0
    assert req.radio_climate == "desert"
    assert req.clutter_height == 1.0


def test_coverage_pessimism_db_folds_into_emitted_modem_margin() -> None:
    """Preset catalogs stay literal; pessimism merges into ``request.json`` modem block only."""
    preset = _minimal_preset(simulation=_minimal_simulation(coverage_pessimism_db=5.5))
    assert resolved_modem(preset)["implementation_margin_db"] == 3.0

    req = preset_to_request(preset, 39.0, -119.0)
    assert req.modem is not None
    assert req.modem.implementation_margin_db == pytest.approx(3.0 + 5.5)
    rel = reliability_margin_db(
        float(preset.simulation.situation_pct), float(preset.simulation.time_pct)
    )
    assert req.signal_threshold == pytest.approx(-121.0 + 3.0 + 5.5 + rel)


def test_receiver_sensitivity_overrides_modem_in_request_modem_block() -> None:
    preset = _minimal_preset()
    preset.simulation.receiver["sensitivity_dbm"] = -117.5
    req = preset_to_request(preset, 39.0, -119.0)
    assert req.modem is not None and req.modem.sensitivity_dbm == -117.5
    rel = reliability_margin_db(
        float(preset.simulation.situation_pct), float(preset.simulation.time_pct)
    )
    assert req.signal_threshold == pytest.approx(-117.5 + 3.0 + rel)


