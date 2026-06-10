from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


RadioClimate = Literal[
    "equatorial",
    "continental_subtropical",
    "maritime_subtropical",
    "desert",
    "continental_temperate",
    "maritime_temperate_land",
    "maritime_temperate_sea",
]


class LoRaModemParams(BaseModel):
    """LoRa modem / air-interface parameters (splatter decodes cutoff from here)."""

    model_config = ConfigDict(extra="ignore")

    spreading_factor: int = Field(ge=6, le=12)
    bandwidth_khz: float = Field(gt=0)
    coding_rate: int = Field(ge=5, le=8, default=5)
    implementation_margin_db: float = Field(ge=0, default=0.0)
    sensitivity_dbm: float | None = None


class SplatCoverageRequest(BaseModel):
    """Splatter-compatible coverage request (tx_power in dBm)."""

    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    tx_height: float = Field(ge=1)
    tx_power: float = Field(description="Transmitter power in dBm")
    tx_gain: float = Field(ge=0)
    frequency_mhz: float = Field(ge=20, le=30000)
    rx_height: float = Field(ge=1)
    rx_gain: float = Field(ge=0)
    signal_threshold: float = Field(le=0)
    clutter_height: float = Field(ge=0)
    ground_dielectric: float = Field(ge=1, default=15.0)
    ground_conductivity: float = Field(ge=0, default=0.005)
    atmosphere_bending: float = Field(ge=0, default=301.0)
    radius: float = Field(ge=1, description="Range in meters")
    system_loss: float = Field(ge=0, default=0.0)
    radio_climate: RadioClimate = "continental_temperate"
    polarization: Literal["horizontal", "vertical"] = "vertical"
    situation_fraction: float = Field(ge=1, le=100)
    time_fraction: float = Field(ge=1, le=100)
    fresnel_clearance_fraction: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        description=(
            "Fresnel clearance floor used with knife-edge diffraction (fraction of F₁ below LOS)."
        ),
    )
    colormap: str = "rainbow"
    min_dbm: float = -130.0
    max_dbm: float = -30.0
    raster_dimension: int = Field(
        default=500,
        ge=128,
        le=4096,
        description="Square splatter output raster size (pixels). Ground step ≈ radius_m / raster_dimension.",
    )
    modem: LoRaModemParams | None = Field(
        default=None,
        description="Populated preset: required for splatter decode cutoff.",
    )
