"""``$PEAKY_HOME`` global settings for ``peaky serve``."""

from __future__ import annotations

from typing import Any, Mapping

from peaky_finders.peaky_preset_defaults import (
    load_home_simulation,
    serialize_home_simulation,
    update_home_simulation,
)
from peaky_finders.peaky_profiles import (
    delete_environment_preset,
    delete_modem_preset,
    load_environment_presets_catalog,
    load_modem_presets_catalog,
    upsert_environment_preset,
    upsert_modem_preset,
    validate_environment_preset_body,
    validate_modem_preset_body,
    validate_preset_name,
)
from peaky_finders.serve_simulation import (
    validate_viewshed_radius_km,
    validate_viewshed_raster_dimension,
)


def get_home_simulation_payload() -> dict[str, Any]:
    sim = load_home_simulation()
    modems = load_modem_presets_catalog()
    environments = load_environment_presets_catalog()
    return {
        "simulation": serialize_home_simulation(sim),
        "modem_names": sorted(modems.keys()),
        "environment_names": sorted(environments.keys()),
    }


def patch_home_simulation(body: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(body, Mapping):
        raise ValueError("body must be a JSON object")
    patch: dict[str, Any] = {}
    if "modem" in body:
        patch["modem"] = body["modem"]
    if "environment" in body:
        patch["environment"] = body["environment"]
    if "radius_km" in body:
        patch["radius_km"] = validate_viewshed_radius_km(float(body["radius_km"]))
    if "raster_dimension" in body:
        patch["raster_dimension"] = validate_viewshed_raster_dimension(int(body["raster_dimension"]))
    if "transmitter" in body:
        if not isinstance(body["transmitter"], Mapping):
            raise ValueError("transmitter must be an object")
        patch["transmitter"] = dict(body["transmitter"])
    if "receiver" in body:
        if not isinstance(body["receiver"], Mapping):
            raise ValueError("receiver must be an object")
        patch["receiver"] = dict(body["receiver"])
    if "max_workers" in body:
        mw = body["max_workers"]
        if not isinstance(mw, Mapping):
            raise ValueError("max_workers must be an object")
        if "splatter" in mw:
            splatter = int(mw["splatter"])
            if splatter < 1:
                raise ValueError("max_workers.splatter must be >= 1")
            patch["max_workers"] = {"splatter": splatter}
    if not patch:
        raise ValueError("no supported simulation fields in request body")
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
    simulation = update_home_simulation(patch)
    return {"simulation": simulation}


def get_modem_catalog_payload() -> dict[str, Any]:
    presets = load_modem_presets_catalog()
    return {"presets": presets}


def create_modem_preset(body: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(body, Mapping):
        raise ValueError("body must be a JSON object")
    if "name" not in body:
        raise ValueError("name is required")
    name = validate_preset_name(str(body["name"]))
    catalog = load_modem_presets_catalog()
    if name in catalog:
        raise ValueError(f"modem preset {name!r} already exists")
    fields = {k: v for k, v in body.items() if k != "name"}
    return upsert_modem_preset(name, fields)


def patch_modem_preset(name: str, body: Mapping[str, Any]) -> dict[str, Any]:
    key = validate_preset_name(name)
    catalog = load_modem_presets_catalog()
    if key not in catalog:
        raise KeyError(key)
    if not isinstance(body, Mapping):
        raise ValueError("body must be a JSON object")
    merged = dict(catalog[key])
    merged.update(body)
    validated = validate_modem_preset_body(merged)
    return upsert_modem_preset(key, validated)


def get_environment_catalog_payload() -> dict[str, Any]:
    presets = load_environment_presets_catalog()
    return {"presets": presets}


def create_environment_preset(body: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(body, Mapping):
        raise ValueError("body must be a JSON object")
    if "name" not in body:
        raise ValueError("name is required")
    name = validate_preset_name(str(body["name"]))
    catalog = load_environment_presets_catalog()
    if name in catalog:
        raise ValueError(f"environment preset {name!r} already exists")
    fields = {k: v for k, v in body.items() if k != "name"}
    return upsert_environment_preset(name, fields)


def patch_environment_preset(name: str, body: Mapping[str, Any]) -> dict[str, Any]:
    key = validate_preset_name(name)
    catalog = load_environment_presets_catalog()
    if key not in catalog:
        raise KeyError(key)
    if not isinstance(body, Mapping):
        raise ValueError("body must be a JSON object")
    merged = dict(catalog[key])
    merged.update(body)
    validated = validate_environment_preset_body(merged)
    return upsert_environment_preset(key, validated)
