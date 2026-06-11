"""Append coverage goals (``sites`` with ``type: goal``) for ``peaky serve``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from peaky_finders.serve_sites import (
    _coerce_site_type,
    unique_site_slug,
    validate_site_coords,
    validate_site_name,
)
from peaky_finders.sites_job import SiteType, update_preset_yaml_tree


def unique_goal_slug(existing_sites: set[str], name: str) -> str:
    """Derive a unique goal slug from *name* (unique across all ``sites``)."""
    return unique_site_slug(existing_sites, name)


def append_goal_to_preset(
    preset_path: Path,
    *,
    name: str,
    lat: float,
    lon: float,
) -> str:
    """Append a ``type: goal`` site; return the new slug."""
    name = validate_site_name(name)
    validate_site_coords(lat, lon)

    def mutator(_yaml_rt: Any, root: dict[str, Any]) -> str:
        sites_raw = root.get("sites")
        if sites_raw is None:
            sites_raw = {}
            root["sites"] = sites_raw
        if not isinstance(sites_raw, dict):
            raise ValueError("preset sites must be a mapping")

        existing = {str(k) for k in sites_raw.keys()}
        goal_slug = unique_goal_slug(existing, name)
        sites_raw[goal_slug] = {
            "type": SiteType.GOAL.value,
            "name": name,
            "loc": [lat, lon],
        }
        return goal_slug

    return str(update_preset_yaml_tree(preset_path, mutator, validate=False))


def update_goal_in_preset(
    preset_path: Path,
    goal_slug: str,
    *,
    name: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    promote_site_type: str | None = None,
) -> str:
    """Update a goal site; optionally promote by changing ``type`` to installed/planned."""
    slug = str(goal_slug).strip()
    if not slug:
        raise ValueError("goal slug is required")
    if name is not None:
        name = validate_site_name(name)
    if lat is not None or lon is not None:
        if lat is None or lon is None:
            raise ValueError("lat and lon must both be provided")
        validate_site_coords(lat, lon)
    site_type_value: str | None = None
    if promote_site_type is not None:
        site_type_value = _coerce_site_type(promote_site_type)
        if site_type_value == SiteType.SUGGESTED.value:
            raise ValueError("goals may only be promoted to installed or planned")
        if site_type_value == SiteType.GOAL.value:
            site_type_value = None

    def mutator(_yaml_rt: Any, root: dict[str, Any]) -> str:
        sites_raw = root.get("sites")
        if not isinstance(sites_raw, dict) or slug not in sites_raw:
            raise ValueError(f"goal not found: {slug!r}")
        ent = sites_raw[slug]
        if not isinstance(ent, dict):
            raise ValueError(f"goal entry must be a mapping: {slug!r}")
        if str(ent.get("type", SiteType.GOAL.value)).strip().lower() != SiteType.GOAL.value:
            raise ValueError(f"site is not a goal: {slug!r}")
        if name is not None:
            ent["name"] = name
        if lat is not None and lon is not None:
            ent["loc"] = [lat, lon]
        if site_type_value is not None:
            ent["type"] = site_type_value
        return slug

    return str(update_preset_yaml_tree(preset_path, mutator, validate=False))


def delete_goal_from_preset(preset_path: Path, goal_slug: str) -> str:
    """Remove a ``type: goal`` site; return deleted slug."""
    slug = str(goal_slug).strip()
    if not slug:
        raise ValueError("goal slug is required")

    def mutator(_yaml_rt: Any, root: dict[str, Any]) -> str:
        sites_raw = root.get("sites")
        if not isinstance(sites_raw, dict) or slug not in sites_raw:
            raise ValueError(f"goal not found: {slug!r}")
        ent = sites_raw[slug]
        if not isinstance(ent, dict):
            raise ValueError(f"goal entry must be a mapping: {slug!r}")
        if str(ent.get("type", SiteType.GOAL.value)).strip().lower() != SiteType.GOAL.value:
            raise ValueError(f"site is not a goal: {slug!r}")
        del sites_raw[slug]
        return slug

    return str(update_preset_yaml_tree(preset_path, mutator, validate=False))
