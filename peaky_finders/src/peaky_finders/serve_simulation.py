"""Update preset ``simulation`` fields from ``peaky serve``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from peaky_finders.serve_viewshed_sim import (
    MAX_SERVE_RADIUS_KM,
    MAX_SERVE_RASTER_DIMENSION,
    MIN_SERVE_RADIUS_KM,
    MIN_SERVE_RASTER_DIMENSION,
)
from peaky_finders.sites_job import update_preset_yaml_tree


def validate_viewshed_radius_km(radius_km: float) -> float:
    value = float(radius_km)
    if not (MIN_SERVE_RADIUS_KM <= value <= MAX_SERVE_RADIUS_KM):
        raise ValueError(
            f"radius_km must be between {MIN_SERVE_RADIUS_KM:g} and {MAX_SERVE_RADIUS_KM:g}"
        )
    return value


def validate_viewshed_raster_dimension(raster_dimension: int) -> int:
    value = int(raster_dimension)
    if not (MIN_SERVE_RASTER_DIMENSION <= value <= MAX_SERVE_RASTER_DIMENSION):
        raise ValueError(
            "raster_dimension must be between "
            f"{MIN_SERVE_RASTER_DIMENSION} and {MAX_SERVE_RASTER_DIMENSION}"
        )
    return value


def update_viewshed_sim_to_preset(
    preset_path: Path,
    *,
    radius_km: float,
    raster_dimension: int,
) -> dict[str, float | int]:
    """Persist viewshed grid knobs under ``simulation``; return written values."""
    radius_km = validate_viewshed_radius_km(radius_km)
    raster_dimension = validate_viewshed_raster_dimension(raster_dimension)

    def mutator(_yaml_rt: Any, root: dict[str, Any]) -> dict[str, float | int]:
        sim_raw = root.get("simulation")
        if not isinstance(sim_raw, dict):
            sim_raw = {}
            root["simulation"] = sim_raw
        sim_raw["radius_km"] = radius_km
        sim_raw["raster_dimension"] = raster_dimension
        return {"radius_km": radius_km, "raster_dimension": raster_dimension}

    return dict(update_preset_yaml_tree(preset_path, mutator, validate=False))
