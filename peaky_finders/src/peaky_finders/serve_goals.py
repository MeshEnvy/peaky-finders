"""Append user-authored goals to preset YAML for ``peaky serve``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from peaky_finders.serve_sites import (
    _coerce_site_type,
    unique_site_slug,
    validate_site_coords,
    validate_site_name,
)
from peaky_finders.sites_job import update_preset_yaml_tree


def unique_goal_slug(existing_sites: set[str], existing_goals: set[str], name: str) -> str:
    """Derive a unique goal key from *name* (unique across sites and goals)."""
    taken = existing_sites | existing_goals
    slug = unique_site_slug(taken, name)
    return slug


def append_goal_to_preset(
    preset_path: Path,
    *,
    name: str,
    lat: float,
    lon: float,
) -> str:
    """Append a goal under ``goals``; return the new slug."""
    name = validate_site_name(name)
    validate_site_coords(lat, lon)

    def mutator(_yaml_rt: Any, root: dict[str, Any]) -> str:
        sites_raw = root.get("sites")
        if not isinstance(sites_raw, dict):
            sites_raw = {}
        goals_raw = root.get("goals")
        if goals_raw is None:
            goals_raw = {}
            root["goals"] = goals_raw
        if not isinstance(goals_raw, dict):
            raise ValueError("preset goals must be a mapping")

        site_slugs = {str(k) for k in sites_raw.keys()}
        goal_slugs = {str(k) for k in goals_raw.keys()}
        goal_slug = unique_goal_slug(site_slugs, goal_slugs, name)
        goals_raw[goal_slug] = {
            "name": name,
            "loc": [lat, lon],
        }
        return goal_slug

    return str(update_preset_yaml_tree(preset_path, mutator, validate=False))


def _copy_mapping_fields(dst: dict[str, Any], src: dict[str, Any]) -> None:
    """Merge *src* into *dst* without removing keys already on *dst*."""
    for key, val in src.items():
        dst[key] = val


def update_goal_in_preset(
    preset_path: Path,
    goal_slug: str,
    *,
    name: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    promote_site_type: str | None = None,
) -> str:
    """Update a goal; optionally promote it to ``sites`` with *promote_site_type*."""
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
        if site_type_value == "suggested":
            raise ValueError("goals may only be promoted to installed or planned")

    def mutator(_yaml_rt: Any, root: dict[str, Any]) -> str:
        goals_raw = root.get("goals")
        if not isinstance(goals_raw, dict) or slug not in goals_raw:
            raise ValueError(f"goal not found: {slug!r}")
        goal_ent = goals_raw[slug]
        if not isinstance(goal_ent, dict):
            raise ValueError(f"goal entry must be a mapping: {slug!r}")
        if name is not None:
            goal_ent["name"] = name
        if lat is not None and lon is not None:
            goal_ent["loc"] = [lat, lon]

        if site_type_value is not None:
            sites_raw = root.get("sites")
            if sites_raw is None:
                sites_raw = {}
                root["sites"] = sites_raw
            if not isinstance(sites_raw, dict):
                raise ValueError("preset sites must be a mapping")
            if slug in sites_raw:
                raise ValueError(f"site slug already exists: {slug!r}")
            site_ent: dict[str, Any] = {}
            sites_raw[slug] = site_ent
            _copy_mapping_fields(site_ent, goal_ent)
            site_ent["type"] = site_type_value
            del goals_raw[slug]
        return slug

    return str(update_preset_yaml_tree(preset_path, mutator, validate=False))


def delete_goal_from_preset(preset_path: Path, goal_slug: str) -> str:
    """Remove a goal from ``goals``; return deleted slug."""
    slug = str(goal_slug).strip()
    if not slug:
        raise ValueError("goal slug is required")

    def mutator(_yaml_rt: Any, root: dict[str, Any]) -> str:
        goals_raw = root.get("goals")
        if not isinstance(goals_raw, dict) or slug not in goals_raw:
            raise ValueError(f"goal not found: {slug!r}")
        del goals_raw[slug]
        return slug

    return str(update_preset_yaml_tree(preset_path, mutator, validate=False))
