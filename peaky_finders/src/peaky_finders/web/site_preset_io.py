"""Round-trip preset YAML edits for web site CRUD."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from peaky_finders.sites_job import (
    SiteType,
    _slugify_files_segment,
    load_preset,
    update_preset_yaml_tree,
)


class SitePresetConflictError(ValueError):
    """Slug collision or last-site delete."""


def _preset_path_for_project(slug: str) -> Path:
    from peaky_finders.sites_job import peaky_projects_dir

    cfg = peaky_projects_dir() / slug / "config.yaml"
    if not cfg.is_file():
        raise FileNotFoundError(f"project not found: {slug!r}")
    return cfg


def _sites_mapping(root: dict[str, Any]) -> dict[str, Any]:
    sites_raw = root.get("sites")
    if sites_raw is None:
        sites_raw = {}
        root["sites"] = sites_raw
    if not isinstance(sites_raw, dict):
        raise ValueError("preset sites must be a mapping")
    return sites_raw


def _rewrite_sees_refs(sites_raw: dict[str, Any], old_slug: str, new_slug: str) -> None:
    if old_slug == new_slug:
        return
    for ent in sites_raw.values():
        if not isinstance(ent, dict):
            continue
        sees = ent.get("sees")
        if not isinstance(sees, list):
            continue
        ent["sees"] = [new_slug if str(t).strip() == old_slug else t for t in sees]


def _scrub_sees_refs(sites_raw: dict[str, Any], removed_slug: str) -> None:
    for ent in sites_raw.values():
        if not isinstance(ent, dict):
            continue
        sees = ent.get("sees")
        if not isinstance(sees, list):
            continue
        ent["sees"] = [t for t in sees if str(t).strip() != removed_slug]


def _clear_preset_edit_caches() -> None:
    from peaky_finders.site_suggestions.rf_link import clear_rf_link_cache
    from peaky_finders.web.projects import clear_project_geocode_aoi_cache

    clear_rf_link_cache()
    clear_project_geocode_aoi_cache()


def _normalize_slug(raw: str) -> str:
    slug = _slugify_files_segment(str(raw or "").strip())
    if not slug:
        raise ValueError("site slug must be non-empty")
    return slug


def get_site_from_preset(preset_path: Path, site_slug: str) -> dict[str, Any]:
    """Load one site entry from preset YAML."""
    preset = load_preset(preset_path)
    if site_slug not in preset.sites:
        raise FileNotFoundError(f"unknown site {site_slug!r}")
    entry = preset.sites[site_slug]
    return {
        "slug": site_slug,
        "type": entry.type.value,
        "name": entry.name,
        "lat": entry.lat,
        "lon": entry.lon,
        "elevation_m": entry.elevation_m,
        "description": entry.description,
        "plss": entry.plss,
        "mlrs": entry.mlrs,
        "rationale": entry.rationale,
        "sees": list(entry.sees),
        "participates_in_rf": entry.participates_in_rf,
    }


def update_site_in_preset(
    preset_path: Path,
    site_slug: str,
    body: dict[str, Any],
    *,
    new_slug: str | None = None,
) -> str:
    """Merge site fields; optional slug rename. Return effective slug."""
    if not body and new_slug is None:
        raise ValueError("at least one field is required")

    def mutator(_yaml_rt: Any, root: dict[str, Any]) -> str:
        sites_raw = _sites_mapping(root)
        if site_slug not in sites_raw:
            raise FileNotFoundError(f"unknown site {site_slug!r}")

        ent = sites_raw[site_slug]
        if not isinstance(ent, dict):
            raise ValueError(f"sites.{site_slug!r} must be a mapping")

        if "name" in body and body["name"] is not None:
            ent["name"] = str(body["name"]).strip()
        if "type" in body and body["type"] is not None:
            ent["type"] = str(body["type"]).strip().lower()
        if "lat" in body and body["lat"] is not None and "lon" in body and body["lon"] is not None:
            ent["loc"] = [float(body["lat"]), float(body["lon"])]
        elif "lat" in body and body["lat"] is not None:
            loc = ent.get("loc")
            if isinstance(loc, (list, tuple)) and len(loc) == 2:
                ent["loc"] = [float(body["lat"]), float(loc[1])]
        elif "lon" in body and body["lon"] is not None:
            loc = ent.get("loc")
            if isinstance(loc, (list, tuple)) and len(loc) == 2:
                ent["loc"] = [float(loc[0]), float(body["lon"])]

        for key in ("description", "rationale"):
            if key not in body:
                continue
            val = body[key]
            if val is None or (isinstance(val, str) and not val.strip()):
                ent.pop(key, None)
            else:
                ent[key] = str(val).strip()

        site_type = str(ent.get("type", SiteType.INSTALLED.value)).strip().lower()
        if site_type == SiteType.GOAL.value:
            ent["sees"] = []
        elif "sees" in body and body["sees"] is not None:
            ent["sees"] = [str(s).strip() for s in body["sees"] if str(s).strip()]

        effective_slug = site_slug
        if new_slug is not None:
            target = _normalize_slug(new_slug)
            if target != site_slug and target in sites_raw:
                raise SitePresetConflictError(f"site slug already exists: {target!r}")
            if target != site_slug:
                sites_raw[target] = sites_raw.pop(site_slug)
                _rewrite_sees_refs(sites_raw, site_slug, target)
                effective_slug = target

        return effective_slug

    effective = update_preset_yaml_tree(preset_path, mutator)
    _clear_preset_edit_caches()
    return effective


def delete_site_from_preset(preset_path: Path, site_slug: str) -> None:
    """Remove a site; scrub ``sees`` refs. Reject if last site."""

    def mutator(_yaml_rt: Any, root: dict[str, Any]) -> None:
        sites_raw = _sites_mapping(root)
        if site_slug not in sites_raw:
            raise FileNotFoundError(f"unknown site {site_slug!r}")
        if len(sites_raw) <= 1:
            raise SitePresetConflictError("cannot delete the last site in preset")

        del sites_raw[site_slug]
        _scrub_sees_refs(sites_raw, site_slug)

    update_preset_yaml_tree(preset_path, mutator)
    _clear_preset_edit_caches()
