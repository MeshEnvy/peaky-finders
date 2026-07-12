"""Global preset defaults at ``$PEAKY_HOME/config.yaml``."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import yaml
from pydantic import ValidationError

from peaky_finders.core.home.bundled_templates import ensure_bundled_home_file
from peaky_finders.core.home.io import update_home_yaml_tree
from peaky_finders.core.preset.model import SimulationConfig
from peaky_finders.core.preset.paths import peaky_home
from peaky_finders.core.preset.io import yaml_plain_preset_value

CONFIG_FILENAME = "config.yaml"
_PROJECT_ONLY_KEYS = frozenset({"sites", "links"})


def peaky_home_config_path() -> Path:
    """``$PEAKY_HOME/config.yaml`` — shared defaults for all ``projects/<slug>/`` presets."""
    return peaky_home() / CONFIG_FILENAME


def ensure_peaky_home_config() -> None:
    """Seed ``$PEAKY_HOME/config.yaml`` from the bundled template when missing."""
    ensure_bundled_home_file(CONFIG_FILENAME)


def _read_yaml_mapping(path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path} root must be a mapping")
    return raw


def load_preset_defaults() -> dict[str, Any]:
    """Load merged global defaults from ``$PEAKY_HOME/config.yaml``."""
    ensure_peaky_home_config()
    return _read_yaml_mapping(peaky_home_config_path())


def deep_merge_preset_dict(
    base: Mapping[str, Any],
    override: Mapping[str, Any],
) -> dict[str, Any]:
    """Deep-merge *override* onto *base* (dict values recurse; scalars/lists replace)."""
    out: dict[str, Any] = dict(base)
    for key, val in override.items():
        base_val = out.get(key)
        if isinstance(base_val, dict) and isinstance(val, Mapping):
            out[key] = deep_merge_preset_dict(base_val, val)
        else:
            out[key] = val
    return out


def prune_project_preset_dict(
    project: Mapping[str, Any],
    defaults: Mapping[str, Any],
) -> dict[str, Any]:
    """Drop keys equal to *defaults*; keep ``sites`` / ``links`` verbatim."""
    out: dict[str, Any] = {}
    for key, val in project.items():
        if key in _PROJECT_ONLY_KEYS:
            out[key] = val
            continue
        def_val = defaults.get(key)
        if def_val is None:
            out[key] = val
            continue
        if isinstance(val, Mapping) and isinstance(def_val, Mapping):
            pruned = prune_project_preset_dict(val, def_val)
            if pruned:
                out[key] = pruned
        elif val != def_val:
            out[key] = val
    return out


def resolve_preset_raw(project: Mapping[str, Any]) -> dict[str, Any]:
    """Merge global defaults with a project preset mapping."""
    defaults = load_preset_defaults()
    return deep_merge_preset_dict(defaults, project)


def load_home_simulation() -> SimulationConfig:
    """Parse the global ``simulation`` block from ``$PEAKY_HOME/config.yaml``."""
    defaults = load_preset_defaults()
    sim_raw = defaults.get("simulation")
    if not isinstance(sim_raw, dict):
        sim_raw = {}
    return SimulationConfig.model_validate(sim_raw)


def _deep_merge_mapping(base: Mapping[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = dict(base)
    for key, val in patch.items():
        base_val = out.get(key)
        if isinstance(base_val, dict) and isinstance(val, Mapping):
            out[key] = _deep_merge_mapping(base_val, val)
        else:
            out[key] = val
    return out


def serialize_home_simulation(sim: SimulationConfig) -> dict[str, Any]:
    """JSON-serializable global simulation block for serve."""
    return {
        "modem": sim.modem,
        "environment": sim.environment,
        "radius_km": float(sim.radius_km),
        "raster_dimension": int(sim.raster_dimension),
        "transmitter": dict(sim.transmitter),
        "receiver": dict(sim.receiver),
        "max_workers": {"splatter": int(sim.max_workers.splatter)},
    }


def update_home_simulation(patch: Mapping[str, Any]) -> dict[str, Any]:
    """Merge *patch* into global ``simulation`` and validate via :class:`SimulationConfig`."""
    allowed = {
        "modem",
        "environment",
        "radius_km",
        "raster_dimension",
        "transmitter",
        "receiver",
        "max_workers",
    }
    unknown = set(patch.keys()) - allowed
    if unknown:
        raise ValueError(f"unsupported simulation field(s): {', '.join(sorted(unknown))}")

    def mutator(_yaml_rt: Any, root: dict[str, Any]) -> dict[str, Any]:
        sim_raw = root.get("simulation")
        if not isinstance(sim_raw, dict):
            sim_raw = {}
            root["simulation"] = sim_raw
        plain_sim = yaml_plain_preset_value(sim_raw)
        if not isinstance(plain_sim, dict):
            plain_sim = {}
        merged = _deep_merge_mapping(plain_sim, patch)
        validated = SimulationConfig.model_validate(merged)
        for key, val in merged.items():
            sim_raw[key] = yaml_plain_preset_value(val)
        return serialize_home_simulation(validated)

    try:
        return update_home_yaml_tree(peaky_home_config_path(), mutator)
    except ValidationError as e:
        raise ValueError(str(e)) from e
