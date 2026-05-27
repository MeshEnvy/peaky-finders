"""Map preset simulation RF blocks to SplatCoverageRequest."""

from __future__ import annotations

import math
from statistics import NormalDist
from typing import Any, Literal, cast

from peaky_finders.models import LoRaModemParams, RadioClimate, SplatCoverageRequest
from peaky_finders.sites_job import Preset, SimulationConfig

_CLIMATES: frozenset[str] = frozenset(
    {
        "equatorial",
        "continental_subtropical",
        "maritime_subtropical",
        "desert",
        "continental_temperate",
        "maritime_temperate_land",
        "maritime_temperate_sea",
    }
)

# Semtech-order LoRa chip sensitivity at 125 kHz BW (dBm); narrow BW scales ~+3 dB per halving.
_LORA_SENSITIVITY_125KHZ_DBM: dict[int, float] = {
    7: -123.0,
    8: -126.0,
    9: -129.0,
    10: -132.0,
    11: -134.5,
    12: -137.0,
}

# RSS × σ model for combined situation×time variability (Gaussian quantiles).
_RELIABILITY_SIGMA_DB = 2.375

_DEFAULT_ENVIRONMENT: dict[str, Any] = {
    "climate": "continental_temperate",
    "polarization": "vertical",
    "clutter_height_m": 0.0,
    "ground_dielectric_v_m": 15.0,
    "ground_conductivity_s_m": 0.005,
    "atmosphere_bending_n": 301.0,
}


def _f(x: Any) -> float:
    return float(x)


def _sim(preset: Preset) -> SimulationConfig:
    return preset.simulation


def _merge_catalog_dict(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in overrides.items():
        if key == "preset":
            continue
        out[key] = value
    return out


def _resolve_catalog_entry(
    *,
    selected: str | dict[str, Any] | None,
    catalog: dict[str, dict[str, Any]],
    label: str,
) -> dict[str, Any]:
    if selected is None:
        if catalog:
            raise ValueError(f"preset defines {label}_presets but {label} is unset")
        return {}

    if isinstance(selected, str):
        name = selected.strip()
        if name not in catalog:
            known = ", ".join(sorted(catalog)) or "(none)"
            raise ValueError(f"unknown {label} preset {name!r}; known: {known}")
        return dict(catalog[name])

    if not isinstance(selected, dict):
        raise ValueError(f"{label} must be a preset name string or a mapping")

    if "preset" in selected:
        name = str(selected["preset"]).strip()
        if name not in catalog:
            known = ", ".join(sorted(catalog)) or "(none)"
            raise ValueError(f"unknown {label} preset {name!r}; known: {known}")
        base = dict(catalog[name])
        return _merge_catalog_dict(base, selected)

    return dict(selected)


def resolved_modem(preset: Preset) -> dict[str, Any]:
    """Merge ``simulation.modem_presets`` entry, active ``modem`` name, and inline overrides."""
    sim = _sim(preset)
    return _resolve_catalog_entry(selected=sim.modem, catalog=sim.modem_presets, label="modem")


def resolved_environment(preset: Preset) -> dict[str, Any]:
    """Merge ``simulation.environment_presets`` entry, active ``environment`` name, and overrides."""
    sim = _sim(preset)
    env = _resolve_catalog_entry(
        selected=sim.environment,
        catalog=sim.environment_presets,
        label="environment",
    )
    if not env:
        return dict(_DEFAULT_ENVIRONMENT)
    merged = dict(_DEFAULT_ENVIRONMENT)
    merged.update(env)
    return merged


def _preset_frequency_mhz(preset: Preset) -> float:
    modem = resolved_modem(preset)
    if "frequency_mhz" in modem:
        return _f(modem["frequency_mhz"])
    tx = _sim(preset).transmitter
    if "frequency_mhz" in tx:
        return _f(tx["frequency_mhz"])
    raise ValueError(
        "preset requires modem frequency (via simulation.modem_presets) "
        "or simulation.transmitter.frequency_mhz"
    )


def _preset_tx_power_dbm(preset: Preset) -> float:
    tx = _sim(preset).transmitter
    if "power_dbm" in tx:
        return _f(tx["power_dbm"])
    if "power_w" in tx:
        power_w = _f(tx["power_w"])
        if power_w <= 0:
            raise ValueError("simulation.transmitter.power_w must be positive")
        return 10 * math.log10(power_w) + 30
    modem = resolved_modem(preset)
    if "power_dbm" in modem:
        return _f(modem["power_dbm"])
    raise ValueError(
        "preset requires simulation.transmitter power or modem preset power_dbm"
    )


def reliability_margin_db(
    situation_pct: float, time_pct: float, *, sigma_db: float | None = None
) -> float:
    """Combined reliability margin (dB) folded into SPLAT flat ``signal_threshold``.

    Mirrors splatter logic: Gaussian quantiles for location and time, RSS-combined × ``σ``.
    """
    sigma = _RELIABILITY_SIGMA_DB if sigma_db is None else float(sigma_db)

    def _z(p_pct: float) -> float:
        p = max(1e-9, min(1.0 - 1e-9, float(p_pct) / 100.0))
        return float(NormalDist().inv_cdf(p))

    zs = _z(situation_pct)
    zt = _z(time_pct)
    return math.hypot(zs, zt) * sigma


def _lora_sensitivity_dbm(modem: dict[str, Any]) -> float:
    sf = int(modem["spreading_factor"])
    if sf not in _LORA_SENSITIVITY_125KHZ_DBM:
        raise ValueError(f"unsupported modem.spreading_factor {sf!r}")
    sens = _LORA_SENSITIVITY_125KHZ_DBM[sf]
    bw_khz = _f(modem.get("bandwidth_khz", 125.0))
    if bw_khz <= 0:
        raise ValueError("modem.bandwidth_khz must be positive")
    sens += 10.0 * math.log10(bw_khz / 125.0)
    cr = int(modem.get("coding_rate", 5))
    if cr < 5:
        sens -= 1.0
    elif cr > 5:
        sens += 1.0
    return sens


def _modem_dict_for_request(preset: Preset) -> dict[str, Any]:
    modem = dict(resolved_modem(preset))
    rx = _sim(preset).receiver
    if "sensitivity_dbm" in rx:
        modem["sensitivity_dbm"] = _f(rx["sensitivity_dbm"])
    return modem


def modem_decode_threshold_dbm(modem: dict[str, Any]) -> float:
    """Minimum received power at decode (dBm) before reliability margin."""
    impl = _f(modem.get("implementation_margin_db", 0.0))
    if "sensitivity_dbm" in modem and modem["sensitivity_dbm"] is not None:
        return _f(modem["sensitivity_dbm"]) + impl
    required = ("spreading_factor", "bandwidth_khz")
    if not all(k in modem for k in required):
        raise ValueError(
            "preset requires modem sensitivity_dbm, or spreading_factor + bandwidth_khz "
            "(or simulation.receiver.sensitivity_dbm, merged onto modem)"
        )
    return _lora_sensitivity_dbm(modem) + impl


def preset_to_request(
    preset: Preset,
    lat: float,
    lon: float,
    *,
    high_resolution: bool = True,
) -> SplatCoverageRequest:
    sim = _sim(preset)
    tx = sim.transmitter
    rx = sim.receiver
    env = resolved_environment(preset)
    disp = preset.display

    tx_power_dbm = _preset_tx_power_dbm(preset)
    system_loss = _f(tx.get("loss_db", 0)) + _f(rx.get("loss_db", 0))

    climate = str(env["climate"])
    if climate not in _CLIMATES:
        raise ValueError(f"Unsupported radio climate {climate!r}")

    polar = str(env["polarization"]).lower()
    pol: Literal["horizontal", "vertical"] = "vertical" if polar == "vertical" else "horizontal"

    modem_raw = _modem_dict_for_request(preset)
    # Fold scenario pessimism into emitted modem margins only (YAML catalogs stay literal on-spec).
    modem_eff = dict(modem_raw)
    modem_eff["implementation_margin_db"] = _f(modem_eff.get("implementation_margin_db", 0.0)) + _f(
        sim.coverage_pessimism_db
    )
    rel_margin = reliability_margin_db(_f(sim.situation_pct), _f(sim.time_pct))
    decode = modem_decode_threshold_dbm(modem_eff)
    sens_raw = modem_eff.get("sensitivity_dbm")

    lm = LoRaModemParams(
        spreading_factor=int(modem_eff["spreading_factor"]),
        bandwidth_khz=_f(modem_eff["bandwidth_khz"]),
        coding_rate=int(modem_eff.get("coding_rate", 5)),
        implementation_margin_db=_f(modem_eff.get("implementation_margin_db", 0.0)),
        sensitivity_dbm=None if sens_raw is None else _f(sens_raw),
    )

    return SplatCoverageRequest(
        lat=lat,
        lon=lon,
        tx_height=max(1.0, _f(tx["height_m"])),
        tx_power=tx_power_dbm,
        tx_gain=_f(tx["gain_dbi"]),
        frequency_mhz=_preset_frequency_mhz(preset),
        rx_height=max(1.0, _f(rx["height_m"])),
        rx_gain=_f(rx["gain_dbi"]),
        signal_threshold=decode + rel_margin,
        clutter_height=max(0.0, _f(env["clutter_height_m"])),
        ground_dielectric=_f(env["ground_dielectric_v_m"]),
        ground_conductivity=_f(env["ground_conductivity_s_m"]),
        atmosphere_bending=_f(env["atmosphere_bending_n"]),
        radius=_f(sim.radius_km) * 1000.0,
        system_loss=system_loss,
        radio_climate=cast(RadioClimate, climate),
        polarization=pol,
        situation_fraction=_f(sim.situation_pct),
        time_fraction=_f(sim.time_pct),
        fresnel_clearance_fraction=float(sim.fresnel_clearance_fraction),
        colormap=str(disp["colormap"]),
        min_dbm=_f(disp["min_dbm"]),
        max_dbm=_f(disp["max_dbm"]),
        high_resolution=high_resolution,
        modem=lm,
    )
