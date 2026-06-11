"""Global RF profile catalogs under ``$PEAKY_HOME/modems.yaml`` and ``environments.yaml``."""

from __future__ import annotations

import hashlib
from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml

from peaky_finders.sites_job import peaky_home

MODEMS_FILENAME = "modems.yaml"
ENVIRONMENTS_FILENAME = "environments.yaml"
MODEM_CATALOG_KEY = "modem_presets"
ENVIRONMENT_CATALOG_KEY = "environment_presets"


def _bundled_profile_path(filename: str) -> Path:
    return Path(str(files("peaky_finders.data").joinpath(filename)))


def peaky_home_modems_path() -> Path:
    return peaky_home() / MODEMS_FILENAME


def peaky_home_environments_path() -> Path:
    return peaky_home() / ENVIRONMENTS_FILENAME


def ensure_peaky_home_profiles() -> None:
    """Seed ``$PEAKY_HOME`` profile files from bundled templates when missing."""
    home = peaky_home()
    home.mkdir(parents=True, exist_ok=True)
    for filename in (MODEMS_FILENAME, ENVIRONMENTS_FILENAME):
        dest = home / filename
        if dest.is_file():
            continue
        dest.write_text(_bundled_profile_path(filename).read_text(encoding="utf-8"), encoding="utf-8")


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
