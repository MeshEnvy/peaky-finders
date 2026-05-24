"""Round-trip preset YAML edits for suggested sites."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from peaky_finders.sites_job import SiteType, _slugify_files_segment, dump_preset_yaml_document, read_preset_yaml_tree


def remove_suggested_sites_from_preset(preset_path: Path) -> int:
    """Drop ``type: suggested`` entries from ``sites``; return count removed."""
    yaml_rt, root = read_preset_yaml_tree(preset_path)
    sites_raw = root.get("sites")
    if not isinstance(sites_raw, dict):
        return 0
    removed = 0
    for slug in list(sites_raw.keys()):
        ent = sites_raw.get(slug)
        if not isinstance(ent, dict):
            continue
        if str(ent.get("type", "installed")).strip().lower() == SiteType.SUGGESTED.value:
            del sites_raw[slug]
            removed += 1
    if removed:
        dump_preset_yaml_document(yaml_rt, root, preset_path)
    return removed


def _unique_suggest_slug(existing: set[str], *, iteration: int, name: str) -> str:
    base = f"suggest-{iteration:02d}-{_slugify_files_segment(name)}"
    slug = base
    n = 2
    while slug in existing:
        slug = f"{base}-{n}"
        n += 1
    existing.add(slug)
    return slug


def append_suggested_sites_to_preset(
    preset_path: Path,
    entries: list[dict[str, Any]],
) -> list[str]:
    """Append suggested site dicts under ``sites``; return new slugs."""
    if not entries:
        return []
    yaml_rt, root = read_preset_yaml_tree(preset_path)
    sites_raw = root.get("sites")
    if sites_raw is None:
        sites_raw = {}
        root["sites"] = sites_raw
    if not isinstance(sites_raw, dict):
        raise ValueError("preset sites must be a mapping")

    existing = set(str(k) for k in sites_raw.keys())
    new_slugs: list[str] = []
    for ent in entries:
        iteration = int(ent.get("_suggest_iteration", len(new_slugs) + 1))
        name = str(ent.get("name", f"Suggested {iteration}"))
        slug = _unique_suggest_slug(existing, iteration=iteration, name=name)
        body = {k: v for k, v in ent.items() if not str(k).startswith("_")}
        body["type"] = SiteType.SUGGESTED.value
        sites_raw[slug] = body
        new_slugs.append(slug)
    dump_preset_yaml_document(yaml_rt, root, preset_path)
    return new_slugs
