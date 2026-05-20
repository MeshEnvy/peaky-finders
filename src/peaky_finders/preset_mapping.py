"""Map preset JSON RF blocks to SplatCoverageRequest."""

from __future__ import annotations

import math
from typing import Any, Literal, cast

from peaky_finders.models import RadioClimate, SplatCoverageRequest
from peaky_finders.sites_job import Preset


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


def _f(x: Any) -> float:
    return float(x)


def preset_to_request(
    preset: Preset,
    lat: float,
    lon: float,
    *,
    high_resolution: bool = True,
) -> SplatCoverageRequest:
    tx = preset.transmitter
    rx = preset.receiver
    env = preset.environment
    sim = preset.simulation
    disp = preset.display

    power_w = _f(tx["power_w"])
    if power_w <= 0:
        raise ValueError("transmitter.power_w must be positive")

    tx_power_dbm = 10 * math.log10(power_w) + 30
    system_loss = _f(tx.get("loss_db", 0)) + _f(rx.get("loss_db", 0))

    climate = str(env["climate"])
    if climate not in _CLIMATES:
        raise ValueError(f"Unsupported radio climate {climate!r}")

    polar = str(env["polarization"]).lower()
    pol: Literal["horizontal", "vertical"] = "vertical" if polar == "vertical" else "horizontal"

    return SplatCoverageRequest(
        lat=lat,
        lon=lon,
        tx_height=max(1.0, _f(tx["height_m"])),
        tx_power=tx_power_dbm,
        tx_gain=_f(tx["gain_dbi"]),
        frequency_mhz=_f(tx["frequency_mhz"]),
        rx_height=max(1.0, _f(rx["height_m"])),
        rx_gain=_f(rx["gain_dbi"]),
        signal_threshold=_f(rx["sensitivity_dbm"]),
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
    )
