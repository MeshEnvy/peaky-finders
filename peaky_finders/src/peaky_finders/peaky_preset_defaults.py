"""Global preset defaults at ``$PEAKY_HOME/config.yaml``."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import yaml

from peaky_finders.bundled_templates import ensure_bundled_home_file
from peaky_finders.sites_job import peaky_home

CONFIG_FILENAME = "config.yaml"
_PROJECT_ONLY_KEYS = frozenset({"land", "sites", "links"})


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
    """Drop keys equal to *defaults*; keep ``land`` / ``sites`` / ``links`` verbatim."""
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
