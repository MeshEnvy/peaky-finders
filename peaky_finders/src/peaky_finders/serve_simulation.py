"""Project and global ``simulation`` fields for ``peaky serve``."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from pydantic import ValidationError

from peaky_finders.peaky_preset_defaults import (
    load_home_simulation,
    resolve_preset_raw,
    serialize_home_simulation,
)
from peaky_finders.peaky_profiles import load_environment_presets_catalog, load_modem_presets_catalog
from peaky_finders.serve_viewshed_sim import (
    MAX_SERVE_RADIUS_KM,
    MAX_SERVE_RASTER_DIMENSION,
    MIN_SERVE_RADIUS_KM,
    MIN_SERVE_RASTER_DIMENSION,
)
from peaky_finders.sites_job import (
    SimulationConfig,
    load_preset,
    read_preset_document,
    update_preset_yaml_tree,
    yaml_plain_preset_value,
)

_SIMULATION_PATCH_KEYS = frozenset(
    {
        "modem",
        "environment",
        "radius_km",
        "raster_dimension",
        "transmitter",
        "receiver",
        "max_workers",
    }
)


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


def _deep_merge_mapping(base: Mapping[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = dict(base)
    for key, val in patch.items():
        base_val = out.get(key)
        if isinstance(base_val, dict) and isinstance(val, Mapping):
            out[key] = _deep_merge_mapping(base_val, val)
        else:
            out[key] = val
    return out


def _simulation_leaf_paths(raw: Any, prefix: str = "") -> set[str]:
    if not isinstance(raw, dict):
        return set()
    paths: set[str] = set()
    for key, val in raw.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(val, dict):
            paths |= _simulation_leaf_paths(val, path)
        else:
            paths.add(path)
    return paths


def _delete_nested_key(root: dict[str, Any], dotted: str) -> None:
    parts = dotted.split(".")
    if len(parts) == 1:
        root.pop(parts[0], None)
        return
    head, *rest = parts
    child = root.get(head)
    if not isinstance(child, dict):
        return
    _delete_nested_key(child, ".".join(rest))
    if not child:
        root.pop(head, None)


def _normalize_simulation_patch(patch: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(patch, Mapping):
        raise ValueError("body must be a JSON object")
    unknown = set(patch.keys()) - _SIMULATION_PATCH_KEYS - {"reset"}
    if unknown:
        raise ValueError(f"unsupported simulation field(s): {', '.join(sorted(unknown))}")
    out: dict[str, Any] = {}
    if "modem" in patch:
        out["modem"] = patch["modem"]
    if "environment" in patch:
        out["environment"] = patch["environment"]
    if "radius_km" in patch:
        out["radius_km"] = validate_viewshed_radius_km(float(patch["radius_km"]))
    if "raster_dimension" in patch:
        out["raster_dimension"] = validate_viewshed_raster_dimension(int(patch["raster_dimension"]))
    if "transmitter" in patch:
        if not isinstance(patch["transmitter"], Mapping):
            raise ValueError("transmitter must be an object")
        out["transmitter"] = dict(patch["transmitter"])
    if "receiver" in patch:
        if not isinstance(patch["receiver"], Mapping):
            raise ValueError("receiver must be an object")
        out["receiver"] = dict(patch["receiver"])
    if "max_workers" in patch:
        mw = patch["max_workers"]
        if not isinstance(mw, Mapping):
            raise ValueError("max_workers must be an object")
        splatter = int(mw.get("splatter", 1))
        if splatter < 1:
            raise ValueError("max_workers.splatter must be >= 1")
        out["max_workers"] = {"splatter": splatter}
    return out


def _validate_simulation_catalog_refs(patch: Mapping[str, Any]) -> None:
    modems = load_modem_presets_catalog()
    environments = load_environment_presets_catalog()
    if "modem" in patch:
        modem_ref = patch["modem"]
        if isinstance(modem_ref, str) and modem_ref.strip() and modem_ref.strip() not in modems:
            raise ValueError(f"unknown modem preset {modem_ref!r}")
        if isinstance(modem_ref, dict):
            preset_name = str(modem_ref.get("preset", "")).strip()
            if preset_name and preset_name not in modems:
                raise ValueError(f"unknown modem preset {preset_name!r}")
    if "environment" in patch:
        env_ref = patch["environment"]
        if isinstance(env_ref, str) and env_ref.strip() and env_ref.strip() not in environments:
            raise ValueError(f"unknown environment preset {env_ref!r}")
        if isinstance(env_ref, dict):
            preset_name = str(env_ref.get("preset", "")).strip()
            if preset_name and preset_name not in environments:
                raise ValueError(f"unknown environment preset {preset_name!r}")


def get_project_simulation_payload(preset_path: Path) -> dict[str, Any]:
    preset = load_preset(preset_path)
    defaults = serialize_home_simulation(load_home_simulation())
    effective = serialize_home_simulation(preset.simulation)
    project_doc = read_preset_document(preset_path)
    project_sim_raw = project_doc.get("simulation")
    if not isinstance(project_sim_raw, dict):
        project_sim_raw = {}
    overrides = sorted(_simulation_leaf_paths(project_sim_raw))
    return {
        "simulation": effective,
        "defaults": defaults,
        "overrides": overrides,
        "radius_km_min": MIN_SERVE_RADIUS_KM,
        "radius_km_max": MAX_SERVE_RADIUS_KM,
        "raster_dimension_min": MIN_SERVE_RASTER_DIMENSION,
        "raster_dimension_max": MAX_SERVE_RASTER_DIMENSION,
        "modem_names": sorted(load_modem_presets_catalog().keys()),
        "environment_names": sorted(load_environment_presets_catalog().keys()),
    }


def patch_project_simulation(preset_path: Path, body: Mapping[str, Any]) -> dict[str, Any]:
    patch = _normalize_simulation_patch(body)
    reset_raw = body.get("reset")
    reset_paths: list[str] = []
    if reset_raw is not None:
        if not isinstance(reset_raw, list):
            raise ValueError("reset must be an array of field paths")
        reset_paths = [str(p).strip() for p in reset_raw if str(p).strip()]
    if not patch and not reset_paths:
        raise ValueError("no supported simulation fields in request body")
    if patch:
        _validate_simulation_catalog_refs(patch)

    def mutator(_yaml_rt: Any, root: dict[str, Any]) -> dict[str, Any]:
        sim_raw = root.get("simulation")
        if not isinstance(sim_raw, dict):
            sim_raw = {}
            root["simulation"] = sim_raw
        for path in reset_paths:
            _delete_nested_key(sim_raw, path)
        if not sim_raw:
            root.pop("simulation", None)
        if patch:
            sim_raw = root.get("simulation")
            if not isinstance(sim_raw, dict):
                sim_raw = {}
                root["simulation"] = sim_raw
            plain_sim = yaml_plain_preset_value(sim_raw)
            if not isinstance(plain_sim, dict):
                plain_sim = {}
            merged = _deep_merge_mapping(plain_sim, patch)
            for key, val in merged.items():
                sim_raw[key] = yaml_plain_preset_value(val)
        merged_preset = resolve_preset_raw(yaml_plain_preset_value(root))
        validated = SimulationConfig.model_validate(merged_preset.get("simulation", {}))
        return serialize_home_simulation(validated)

    try:
        result = update_preset_yaml_tree(preset_path, mutator, validate=True)
    except ValidationError as e:
        raise ValueError(str(e)) from e
    return get_project_simulation_payload(preset_path) | {"simulation": result}


def update_viewshed_sim_to_preset(
    preset_path: Path,
    *,
    radius_km: float,
    raster_dimension: int,
) -> dict[str, float | int]:
    """Persist viewshed grid knobs under project ``simulation``."""
    payload = patch_project_simulation(
        preset_path,
        {"radius_km": radius_km, "raster_dimension": raster_dimension},
    )
    sim = payload["simulation"]
    return {"radius_km": float(sim["radius_km"]), "raster_dimension": int(sim["raster_dimension"])}
