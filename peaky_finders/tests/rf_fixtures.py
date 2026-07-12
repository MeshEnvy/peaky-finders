"""Test-only RF catalog names and values (fixtures/peaky_home; not bundled user defaults)."""

from __future__ import annotations

from typing import Any

TEST_MODEM_PRESET = "fixture-modem"
TEST_ENVIRONMENT_PRESET = "fixture-desert"

FIXTURE_MODEM: dict[str, Any] = {
    "frequency_mhz": 910.525,
    "bandwidth_khz": 62.5,
    "spreading_factor": 7,
    "coding_rate": 5,
    "implementation_margin_db": 3.0,
    "power_dbm": 22.0,
    "sensitivity_dbm": -121.0,
}

FIXTURE_ENVIRONMENT: dict[str, Any] = {
    "climate": "desert",
    "polarization": "vertical",
    "clutter_height_m": 1.0,
    "fresnel_clearance_fraction": 0.6,
    "coverage_pessimism_db": 0.0,
    "situation_pct": 95.0,
    "time_pct": 95.0,
    "ground_dielectric_v_m": 15.0,
    "ground_conductivity_s_m": 0.005,
    "atmosphere_bending_n": 301.0,
}

MINIMAL_SIMULATION: dict[str, Any] = {
    "modem": TEST_MODEM_PRESET,
    "environment": TEST_ENVIRONMENT_PRESET,
    "transmitter": {"height_m": 2.0, "gain_dbi": 2.0, "loss_db": 0.0},
    "receiver": {"height_m": 2.0, "gain_dbi": 2.0, "loss_db": 0.0},
}

MINIMAL_SPLATTER_SIMULATION: dict[str, Any] = {
    **MINIMAL_SIMULATION,
    "provider": "splatter",
    "radius_km": 60.0,
}


def goal_site(name: str, loc: tuple[float, float], *, slug: str | None = None) -> dict[str, object]:
    """YAML-shaped site entry with cosmetic ``goal`` tag for legacy suggest tests."""
    _ = slug
    return {"name": name, "loc": list(loc), "tags": ["goal"]}


def make_goal_entry(name: str, loc: tuple[float, float]) -> "SiteEntry":
    from peaky_finders.core.preset import SiteEntry

    return SiteEntry(name=name, loc=loc, tags=["goal"])
