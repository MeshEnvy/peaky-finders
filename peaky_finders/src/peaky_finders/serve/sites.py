"""Append user sites to preset YAML for ``peaky serve``."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from peaky_finders.core.preset import (
    normalize_site_tags,
    slugify_files_segment,
    update_preset_yaml_tree,
)
from peaky_finders.serve.kml_import import KmlPointSite


def unique_site_slug(existing: set[str], name: str) -> str:
    """Derive a unique site key from *name* (same rules as list-import in ``coerce_preset_sites``)."""
    slug = slugify_files_segment(name)
    if slug not in existing:
        return slug
    n = 2
    while f"{slug}-{n}" in existing:
        n += 1
    return f"{slug}-{n}"


def validate_site_name(name: str) -> str:
    trimmed = str(name or "").strip()
    if not trimmed:
        raise ValueError("name is required")
    return trimmed


def validate_site_coords(lat: float, lon: float) -> None:
    if not (-90.0 <= lat <= 90.0):
        raise ValueError(f"lat out of bounds: {lat}")
    if not (-180.0 <= lon <= 180.0):
        raise ValueError(f"lon out of bounds: {lon}")


def append_planned_site_to_preset(
    preset_path: Path,
    *,
    name: str,
    lat: float,
    lon: float,
    tags: list[str] | None = None,
) -> str:
    """Append a site under ``sites``; return the new slug."""
    name = validate_site_name(name)
    validate_site_coords(lat, lon)
    tags_value = normalize_site_tags(tags) if tags is not None else []

    def mutator(_yaml_rt: Any, root: dict[str, Any]) -> str:
        sites_raw = root.get("sites")
        if sites_raw is None:
            sites_raw = {}
            root["sites"] = sites_raw
        if not isinstance(sites_raw, dict):
            raise ValueError("preset sites must be a mapping")

        existing = {str(k) for k in sites_raw.keys()}
        site_slug = unique_site_slug(existing, name)
        entry: dict[str, Any] = {
            "name": name,
            "loc": [lat, lon],
        }
        if tags_value:
            entry["tags"] = list(tags_value)
        sites_raw[site_slug] = entry
        return site_slug

    return str(update_preset_yaml_tree(preset_path, mutator, validate=False))


def import_sites_to_preset(
    preset_path: Path,
    *,
    sites: Sequence[KmlPointSite],
    tags: list[str],
) -> list[str]:
    """Append multiple sites under ``sites`` in one YAML write; return new slugs in order."""
    if not sites:
        raise ValueError("no sites to import")
    tags_value = normalize_site_tags(tags)
    if not tags_value:
        raise ValueError("tags required")

    def mutator(_yaml_rt: Any, root: dict[str, Any]) -> list[str]:
        sites_raw = root.get("sites")
        if sites_raw is None:
            sites_raw = {}
            root["sites"] = sites_raw
        if not isinstance(sites_raw, dict):
            raise ValueError("preset sites must be a mapping")

        existing = {str(k) for k in sites_raw.keys()}
        new_slugs: list[str] = []
        for site in sites:
            name = validate_site_name(site.name)
            validate_site_coords(site.lat, site.lon)
            site_slug = unique_site_slug(existing, name)
            existing.add(site_slug)
            entry: dict[str, Any] = {
                "name": name,
                "loc": [site.lat, site.lon],
                "tags": list(tags_value),
            }
            if site.elevation_m is not None:
                entry["elevation_m"] = float(site.elevation_m)
            sites_raw[site_slug] = entry
            new_slugs.append(site_slug)
        return new_slugs

    return list(update_preset_yaml_tree(preset_path, mutator, validate=False))


def _scrub_manual_links_for_site(links_raw: Any, site_slug: str) -> list[list[str]]:
    """Drop preset ``links`` pairs that reference *site_slug*."""
    if not isinstance(links_raw, list):
        return []
    kept: list[list[str]] = []
    for pair in links_raw:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            continue
        a, b = str(pair[0]).strip(), str(pair[1]).strip()
        if a == site_slug or b == site_slug:
            continue
        kept.append([a, b])
    return kept


def update_site_in_preset(
    preset_path: Path,
    site_slug: str,
    *,
    name: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    tags: list[str] | None = None,
) -> str:
    """Update an existing site in ``sites``; return slug."""
    slug = str(site_slug).strip()
    if not slug:
        raise ValueError("site slug is required")
    if name is not None:
        name = validate_site_name(name)
    if lat is not None or lon is not None:
        if lat is None or lon is None:
            raise ValueError("lat and lon must both be provided")
        validate_site_coords(lat, lon)
    tags_value: list[str] | None = None
    if tags is not None:
        tags_value = normalize_site_tags(tags)

    def mutator(_yaml_rt: Any, root: dict[str, Any]) -> str:
        sites_raw = root.get("sites")
        if not isinstance(sites_raw, dict) or slug not in sites_raw:
            raise ValueError(f"site not found: {slug!r}")
        ent = sites_raw[slug]
        if not isinstance(ent, dict):
            raise ValueError(f"site entry must be a mapping: {slug!r}")
        if "type" in ent:
            raise ValueError(f"sites.{slug}.type is removed; use tags")
        if name is not None:
            ent["name"] = name
        if lat is not None and lon is not None:
            ent["loc"] = [lat, lon]
        if tags_value is not None:
            if tags_value:
                ent["tags"] = list(tags_value)
            else:
                ent.pop("tags", None)
        return slug

    return str(update_preset_yaml_tree(preset_path, mutator, validate=False))


def bulk_merge_site_tags_in_preset(
    preset_path: Path,
    *,
    slugs: Sequence[str],
    add_tags: list[str] | None = None,
    remove_tags: list[str] | None = None,
) -> list[str]:
    """Merge tags on multiple existing sites in one YAML write; return slugs in request order."""
    slug_list = [str(s).strip() for s in slugs]
    if not slug_list or not any(slug_list):
        raise ValueError("slugs must be a non-empty list")
    add_value = normalize_site_tags(add_tags) if add_tags is not None else []
    remove_value = normalize_site_tags(remove_tags) if remove_tags is not None else []
    if not add_value and not remove_value:
        raise ValueError("add_tags or remove_tags required")
    add_set = set(add_value)
    remove_set = set(remove_value)

    def mutator(_yaml_rt: Any, root: dict[str, Any]) -> list[str]:
        sites_raw = root.get("sites")
        if not isinstance(sites_raw, dict):
            raise ValueError("preset sites must be a mapping")
        updated: list[str] = []
        for slug in slug_list:
            if not slug:
                raise ValueError("slug must not be empty")
            if slug not in sites_raw:
                raise ValueError(f"site not found: {slug!r}")
            ent = sites_raw[slug]
            if not isinstance(ent, dict):
                raise ValueError(f"site entry must be a mapping: {slug!r}")
            if "type" in ent:
                raise ValueError(f"sites.{slug}.type is removed; use tags")
            current = set(normalize_site_tags(ent.get("tags")))
            current |= add_set
            current -= remove_set
            if current:
                ent["tags"] = sorted(current)
            else:
                ent.pop("tags", None)
            updated.append(slug)
        return updated

    return list(update_preset_yaml_tree(preset_path, mutator, validate=False))


def delete_site_from_preset(preset_path: Path, site_slug: str) -> str:
    """Remove a site from ``sites`` and scrub manual ``links`` pairs; return deleted slug."""
    slug = str(site_slug).strip()
    if not slug:
        raise ValueError("site slug is required")

    def mutator(_yaml_rt: Any, root: dict[str, Any]) -> str:
        sites_raw = root.get("sites")
        if not isinstance(sites_raw, dict) or slug not in sites_raw:
            raise ValueError(f"site not found: {slug!r}")
        del sites_raw[slug]
        root["links"] = _scrub_manual_links_for_site(root.get("links"), slug)
        return slug

    return str(update_preset_yaml_tree(preset_path, mutator, validate=False))
