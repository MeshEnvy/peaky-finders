"""Append user-planned sites to preset YAML for ``peaky serve``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from peaky_finders.sites_job import (
    SiteType,
    _slugify_files_segment,
    update_preset_yaml_tree,
)


def unique_site_slug(existing: set[str], name: str) -> str:
    """Derive a unique site key from *name* (same rules as list-import in ``coerce_preset_sites``)."""
    slug = _slugify_files_segment(name)
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
) -> str:
    """Append a ``type: planned`` site under ``sites``; return the new slug."""
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
        site_slug = unique_site_slug(existing, name)
        sites_raw[site_slug] = {
            "type": SiteType.PLANNED.value,
            "name": name,
            "loc": [lat, lon],
        }
        return site_slug

    # Sites-only edit — do not require unrelated sections (e.g. suggest goals with site-slug
    # ``loc`` refs) to pass full :class:`Preset` validation; same as suggest append.
    return str(update_preset_yaml_tree(preset_path, mutator, validate=False))


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
