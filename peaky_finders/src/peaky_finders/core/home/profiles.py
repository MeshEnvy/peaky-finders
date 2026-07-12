"""Global RF profile catalogs under ``$PEAKY_HOME/modems.yaml`` and ``environments.yaml``."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Mapping

import yaml

from peaky_finders.core.home.bundled_templates import ensure_bundled_home_file
from peaky_finders.core.home.io import update_home_yaml_tree
from peaky_finders.core.home.preset_defaults import load_preset_defaults
from peaky_finders.core.preset.paths import peaky_home, peaky_projects_dir
from peaky_finders.core.preset.io import yaml_plain_preset_value

MODEMS_FILENAME = "modems.yaml"
ENVIRONMENTS_FILENAME = "environments.yaml"
MODEM_CATALOG_KEY = "modem_presets"
ENVIRONMENT_CATALOG_KEY = "environment_presets"
_PRESET_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]*$")
_POLARIZATIONS = frozenset({"vertical", "horizontal"})
_CLIMATES = frozenset(
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


def peaky_home_modems_path() -> Path:
    return peaky_home() / MODEMS_FILENAME


def peaky_home_environments_path() -> Path:
    return peaky_home() / ENVIRONMENTS_FILENAME


def ensure_peaky_home_profiles() -> None:
    """Seed ``$PEAKY_HOME`` profile files from bundled templates when missing."""
    for filename in (MODEMS_FILENAME, ENVIRONMENTS_FILENAME):
        ensure_bundled_home_file(filename)


def _read_yaml_mapping(path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path} root must be a mapping")
    return raw


def _catalog_from_file(path: Path, catalog_key: str, *, label: str) -> dict[str, dict[str, Any]]:
    root = _read_yaml_mapping(path)
    block = root.get(catalog_key, {})
    if block is None:
        return {}
    if not isinstance(block, dict):
        raise ValueError(f"{path}: {catalog_key} must be a mapping")
    out: dict[str, dict[str, Any]] = {}
    for name, body in block.items():
        key = str(name).strip()
        if not key:
            raise ValueError(f"{path}: {catalog_key} entry name must be non-empty")
        if not isinstance(body, dict):
            raise ValueError(f"{path}: {catalog_key}.{key} must be a mapping")
        out[key] = dict(body)
    return out


def load_modem_presets_catalog() -> dict[str, dict[str, Any]]:
    ensure_peaky_home_profiles()
    return _catalog_from_file(peaky_home_modems_path(), MODEM_CATALOG_KEY, label="modem")


def load_environment_presets_catalog() -> dict[str, dict[str, Any]]:
    ensure_peaky_home_profiles()
    return _catalog_from_file(
        peaky_home_environments_path(), ENVIRONMENT_CATALOG_KEY, label="environment"
    )


def validate_preset_name(name: str) -> str:
    key = str(name or "").strip()
    if not key:
        raise ValueError("preset name is required")
    if not _PRESET_NAME_RE.match(key):
        raise ValueError(
            "preset name must start with a letter and contain only letters, digits, hyphens, underscores"
        )
    return key


def validate_modem_preset_body(body: Mapping[str, Any]) -> dict[str, Any]:
    required = ("frequency_mhz", "bandwidth_khz", "spreading_factor", "coding_rate")
    missing = [k for k in required if k not in body]
    if missing:
        raise ValueError(f"modem preset missing required field(s): {', '.join(missing)}")
    freq = float(body["frequency_mhz"])
    if not (20.0 <= freq <= 30000.0):
        raise ValueError("modem.frequency_mhz must be between 20 and 30000")
    bw = float(body["bandwidth_khz"])
    if bw <= 0:
        raise ValueError("modem.bandwidth_khz must be positive")
    sf = int(body["spreading_factor"])
    if sf not in range(6, 13):
        raise ValueError("modem.spreading_factor must be between 6 and 12")
    cr = int(body["coding_rate"])
    if cr not in range(5, 9):
        raise ValueError("modem.coding_rate must be between 5 and 8")
    out: dict[str, Any] = {
        "frequency_mhz": freq,
        "bandwidth_khz": bw,
        "spreading_factor": sf,
        "coding_rate": cr,
        "implementation_margin_db": float(body.get("implementation_margin_db", 0.0)),
        "power_dbm": float(body.get("power_dbm", 22.0)),
    }
    if "sensitivity_dbm" in body and body["sensitivity_dbm"] is not None:
        out["sensitivity_dbm"] = float(body["sensitivity_dbm"])
    if float(out["implementation_margin_db"]) < 0:
        raise ValueError("modem.implementation_margin_db must be >= 0")
    return out


def validate_environment_preset_body(body: Mapping[str, Any]) -> dict[str, Any]:
    climate = str(body.get("climate", "")).strip()
    if climate not in _CLIMATES:
        raise ValueError(f"environment.climate must be one of: {', '.join(sorted(_CLIMATES))}")
    polar = str(body.get("polarization", "vertical")).strip().lower()
    if polar not in _POLARIZATIONS:
        raise ValueError("environment.polarization must be vertical or horizontal")
    clutter = float(body.get("clutter_height_m", 0.0))
    if clutter < 0:
        raise ValueError("environment.clutter_height_m must be >= 0")
    fresnel = float(body.get("fresnel_clearance_fraction", 0.6))
    if not (0.0 <= fresnel <= 1.0):
        raise ValueError("environment.fresnel_clearance_fraction must be between 0 and 1")
    pessimism = float(body.get("coverage_pessimism_db", 0.0))
    if pessimism < 0:
        raise ValueError("environment.coverage_pessimism_db must be >= 0")
    situation = float(body.get("situation_pct", 95.0))
    time_pct = float(body.get("time_pct", 95.0))
    if not (1.0 <= situation <= 100.0):
        raise ValueError("environment.situation_pct must be between 1 and 100")
    if not (1.0 <= time_pct <= 100.0):
        raise ValueError("environment.time_pct must be between 1 and 100")
    dielectric = float(body.get("ground_dielectric_v_m", 15.0))
    conductivity = float(body.get("ground_conductivity_s_m", 0.005))
    bending = float(body.get("atmosphere_bending_n", 301.0))
    if dielectric < 1:
        raise ValueError("environment.ground_dielectric_v_m must be >= 1")
    if conductivity < 0:
        raise ValueError("environment.ground_conductivity_s_m must be >= 0")
    if bending < 0:
        raise ValueError("environment.atmosphere_bending_n must be >= 0")
    out: dict[str, Any] = {
        "climate": climate,
        "polarization": polar,
        "clutter_height_m": clutter,
        "fresnel_clearance_fraction": fresnel,
        "coverage_pessimism_db": pessimism,
        "situation_pct": situation,
        "time_pct": time_pct,
        "ground_dielectric_v_m": dielectric,
        "ground_conductivity_s_m": conductivity,
        "atmosphere_bending_n": bending,
    }
    if "description" in body and body["description"] is not None:
        desc = str(body["description"]).strip()
        if desc:
            out["description"] = desc
    return out


def _simulation_preset_ref(selected: Any) -> str | None:
    if isinstance(selected, str):
        key = selected.strip()
        return key or None
    if isinstance(selected, dict):
        preset = selected.get("preset")
        if preset is not None:
            key = str(preset).strip()
            return key or None
    return None


def _collect_simulation_refs_from_mapping(raw: Mapping[str, Any], field: str) -> str | None:
    sim = raw.get("simulation")
    if not isinstance(sim, dict):
        return None
    return _simulation_preset_ref(sim.get(field))


def _discover_project_slugs() -> list[str]:
    root = peaky_projects_dir()
    if not root.is_dir():
        return []
    return sorted(
        child.name
        for child in root.iterdir()
        if child.is_dir() and (child / "config.yaml").is_file()
    )


def find_modem_preset_references(name: str) -> list[str]:
    """Return locations referencing *name* as ``simulation.modem`` (``global`` or ``project:<slug>``)."""
    key = validate_preset_name(name)
    refs: list[str] = []
    global_ref = _collect_simulation_refs_from_mapping(load_preset_defaults(), "modem")
    if global_ref == key:
        refs.append("global")
    for slug in _discover_project_slugs():
        project_path = peaky_projects_dir() / slug / "config.yaml"
        if not project_path.is_file():
            continue
        raw = _read_yaml_mapping(project_path)
        if _collect_simulation_refs_from_mapping(raw, "modem") == key:
            refs.append(f"project:{slug}")
    return refs


def find_environment_preset_references(name: str) -> list[str]:
    """Return locations referencing *name* as ``simulation.environment``."""
    key = validate_preset_name(name)
    refs: list[str] = []
    global_ref = _collect_simulation_refs_from_mapping(load_preset_defaults(), "environment")
    if global_ref == key:
        refs.append("global")
    for slug in _discover_project_slugs():
        project_path = peaky_projects_dir() / slug / "config.yaml"
        if not project_path.is_file():
            continue
        raw = _read_yaml_mapping(project_path)
        if _collect_simulation_refs_from_mapping(raw, "environment") == key:
            refs.append(f"project:{slug}")
    return refs


def upsert_modem_preset(name: str, body: Mapping[str, Any]) -> dict[str, Any]:
    key = validate_preset_name(name)
    validated = validate_modem_preset_body(body)

    def mutator(_yaml_rt: Any, root: dict[str, Any]) -> dict[str, Any]:
        block = root.get(MODEM_CATALOG_KEY)
        if not isinstance(block, dict):
            block = {}
            root[MODEM_CATALOG_KEY] = block
        block[key] = yaml_plain_preset_value(validated)
        return dict(validated)

    update_home_yaml_tree(peaky_home_modems_path(), mutator)
    return {"name": key, "preset": validated}


def delete_modem_preset(name: str) -> None:
    key = validate_preset_name(name)
    refs = find_modem_preset_references(key)
    if refs:
        raise ReferenceError(f"modem preset {key!r} is referenced by: {', '.join(refs)}")

    def mutator(_yaml_rt: Any, root: dict[str, Any]) -> None:
        block = root.get(MODEM_CATALOG_KEY)
        if not isinstance(block, dict) or key not in block:
            raise KeyError(key)
        del block[key]

    update_home_yaml_tree(peaky_home_modems_path(), mutator)


def upsert_environment_preset(name: str, body: Mapping[str, Any]) -> dict[str, Any]:
    key = validate_preset_name(name)
    validated = validate_environment_preset_body(body)

    def mutator(_yaml_rt: Any, root: dict[str, Any]) -> dict[str, Any]:
        block = root.get(ENVIRONMENT_CATALOG_KEY)
        if not isinstance(block, dict):
            block = {}
            root[ENVIRONMENT_CATALOG_KEY] = block
        block[key] = yaml_plain_preset_value(validated)
        return dict(validated)

    update_home_yaml_tree(peaky_home_environments_path(), mutator)
    return {"name": key, "preset": validated}


def delete_environment_preset(name: str) -> None:
    key = validate_preset_name(name)
    refs = find_environment_preset_references(key)
    if refs:
        raise ReferenceError(f"environment preset {key!r} is referenced by: {', '.join(refs)}")

    def mutator(_yaml_rt: Any, root: dict[str, Any]) -> None:
        block = root.get(ENVIRONMENT_CATALOG_KEY)
        if not isinstance(block, dict) or key not in block:
            raise KeyError(key)
        del block[key]

    update_home_yaml_tree(peaky_home_environments_path(), mutator)


def profile_catalog_fingerprint_body() -> str:
    """Stable body for build staleness when global profile libraries change."""
    ensure_peaky_home_profiles()
    parts: list[str] = ["peaky_profile_catalog/v1"]
    for path in (peaky_home_modems_path(), peaky_home_environments_path()):
        text = path.read_text(encoding="utf-8")
        parts.append(f"{path.name}\n{text}")
    return "\n---\n".join(parts)


def profile_catalog_fingerprint_hex() -> str:
    return hashlib.sha256(profile_catalog_fingerprint_body().encode("utf-8")).hexdigest()
