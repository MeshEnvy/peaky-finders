"""Persist goal-seek committed path under ``seek.plan`` in project YAML."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from peaky_finders.core.preset import load_preset, normalize_site_tags, update_preset_yaml_tree
from peaky_finders.core.preset.model import SeekPlan, SeekPlanHop
from peaky_finders.serve.sites import site_row_from_yaml_ent, unique_site_slug, validate_site_name


class ServeSeekPlanError(Exception):
    """Goal-seek plan could not be loaded or saved."""


def _plan_hop_to_yaml(hop: SeekPlanHop) -> dict[str, Any]:
    if hop.site:
        return {"site": hop.site}
    row: dict[str, Any] = {"loc": [hop.loc[0], hop.loc[1]]}  # type: ignore[index]
    if hop.height_m is not None:
        row["height_m"] = float(hop.height_m)
    return row


def seek_plan_to_api(plan: SeekPlan | None) -> dict[str, Any] | None:
    if plan is None:
        return None
    return {
        "start": plan.start,
        "goal": [plan.goal[0], plan.goal[1]],
        "complete": bool(plan.complete),
        "hops": [_plan_hop_to_yaml(hop) for hop in plan.hops],
    }


def _normalize_plan_patch(body: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(body, Mapping):
        raise ServeSeekPlanError("plan body must be an object")
    start = str(body.get("start", "")).strip()
    if not start:
        raise ServeSeekPlanError("start is required")
    goal_raw = body.get("goal")
    if not isinstance(goal_raw, (list, tuple)) or len(goal_raw) != 2:
        raise ServeSeekPlanError("goal must be [lat, lon]")
    hops_raw = body.get("hops")
    if not isinstance(hops_raw, list) or not hops_raw:
        raise ServeSeekPlanError("hops must be a non-empty list")
    hops: list[dict[str, Any]] = []
    for idx, item in enumerate(hops_raw):
        if not isinstance(item, Mapping):
            raise ServeSeekPlanError(f"hops[{idx}] must be an object")
        if item.get("site") is not None:
            site = str(item["site"]).strip()
            if not site:
                raise ServeSeekPlanError(f"hops[{idx}].site is required")
            hops.append({"site": site})
            continue
        loc = item.get("loc")
        if not isinstance(loc, (list, tuple)) or len(loc) != 2:
            raise ServeSeekPlanError(f"hops[{idx}] requires site or loc [lat, lon]")
        row: dict[str, Any] = {"loc": [float(loc[0]), float(loc[1])]}
        if item.get("height_m") is not None:
            row["height_m"] = float(item["height_m"])
        hops.append(row)
    return {
        "start": start,
        "goal": [float(goal_raw[0]), float(goal_raw[1])],
        "complete": bool(body.get("complete")),
        "hops": hops,
    }


def load_seek_plan_payload(preset_path: Path) -> dict[str, Any] | None:
    preset = load_preset(preset_path)
    return seek_plan_to_api(preset.seek.plan)


def patch_seek_plan(preset_path: Path, body: Mapping[str, Any]) -> dict[str, Any]:
    normalized = _normalize_plan_patch(body)
    plan = SeekPlan.model_validate(normalized)

    def mutator(_yaml_rt: Any, root: dict[str, Any]) -> dict[str, Any]:
        seek_raw = root.get("seek")
        if not isinstance(seek_raw, dict):
            seek_raw = {}
            root["seek"] = seek_raw
        seek_raw["plan"] = {
            "start": plan.start,
            "goal": [plan.goal[0], plan.goal[1]],
            "complete": plan.complete,
            "hops": [_plan_hop_to_yaml(hop) for hop in plan.hops],
        }
        return seek_raw["plan"]

    try:
        update_preset_yaml_tree(preset_path, mutator, validate=True)
    except ValidationError as e:
        raise ServeSeekPlanError(str(e)) from e
    except ValueError as e:
        raise ServeSeekPlanError(str(e)) from e
    api = seek_plan_to_api(plan)
    assert api is not None
    return api


def _loc_dedupe_key(loc: tuple[float, float], height_m: float | None) -> tuple[float, float, float | None]:
    hm = round(float(height_m), 1) if height_m is not None else None
    return (round(loc[0], 6), round(loc[1], 6), hm)


def _merge_tags_into_site_entry(
    sites_raw: dict[str, Any],
    slug: str,
    add_tags: list[str],
) -> dict[str, object] | None:
    """Merge add_tags onto an existing site; return API row when tags change."""
    ent = sites_raw.get(slug)
    if not isinstance(ent, dict):
        raise ValueError(f"site not found: {slug!r}")
    if "type" in ent:
        raise ValueError(f"sites.{slug}.type is removed; use tags")
    current = set(normalize_site_tags(ent.get("tags")))
    merged = current | set(add_tags)
    if merged == current:
        return None
    ent["tags"] = sorted(merged)
    return site_row_from_yaml_ent(slug, ent)


def _plan_path_site_slugs(start: str, hops: list[dict[str, Any]]) -> list[str]:
    slugs: list[str] = [start]
    seen = {start}
    for hop in hops:
        site = str(hop.get("site", "")).strip()
        if not site or site in seen:
            continue
        seen.add(site)
        slugs.append(site)
    return slugs


def convert_seek_plan_locs_to_sites(
    preset_path: Path,
    *,
    name_prefix: str,
    tags: list[str],
) -> dict[str, Any]:
    """Create preset sites from seek plan coordinate hops; rewrite hops as site refs."""
    prefix = validate_site_name(name_prefix)
    tags_value = normalize_site_tags(tags)
    if not tags_value:
        raise ServeSeekPlanError("tags must be a non-empty list")

    def mutator(_yaml_rt: Any, root: dict[str, Any]) -> dict[str, Any]:
        seek_raw = root.get("seek")
        if not isinstance(seek_raw, dict):
            raise ServeSeekPlanError("no seek plan")
        plan_raw = seek_raw.get("plan")
        if not isinstance(plan_raw, dict):
            raise ServeSeekPlanError("no seek plan")

        plan = SeekPlan.model_validate(plan_raw)
        if not any(hop.loc is not None for hop in plan.hops):
            raise ServeSeekPlanError("no coordinate hops to convert")

        sites_raw = root.get("sites")
        if sites_raw is None:
            sites_raw = {}
            root["sites"] = sites_raw
        if not isinstance(sites_raw, dict):
            raise ValueError("preset sites must be a mapping")

        existing = {str(k) for k in sites_raw.keys()}
        coord_to_slug: dict[tuple[float, float, float | None], str] = {}
        loc_counter = 0
        created_rows: list[dict[str, object]] = []
        new_hops: list[dict[str, Any]] = []

        for hop in plan.hops:
            if hop.site:
                new_hops.append({"site": hop.site})
                continue
            assert hop.loc is not None
            key = _loc_dedupe_key(hop.loc, hop.height_m)
            slug = coord_to_slug.get(key)
            if slug is None:
                loc_counter += 1
                name = f"{prefix} {loc_counter}"
                slug = unique_site_slug(existing, name)
                existing.add(slug)
                coord_to_slug[key] = slug
                entry: dict[str, Any] = {
                    "name": name,
                    "loc": [hop.loc[0], hop.loc[1]],
                    "tags": list(tags_value),
                }
                sites_raw[slug] = entry
                created_rows.append(site_row_from_yaml_ent(slug, entry))
            new_hops.append({"site": slug})

        seek_raw["plan"] = {
            "start": plan.start,
            "goal": [plan.goal[0], plan.goal[1]],
            "complete": plan.complete,
            "hops": new_hops,
        }
        api_plan = seek_plan_to_api(SeekPlan.model_validate(seek_raw["plan"]))
        assert api_plan is not None

        created_slugs = {str(row["slug"]) for row in created_rows}
        tagged_rows: list[dict[str, object]] = []
        for slug in _plan_path_site_slugs(plan.start, new_hops):
            if slug in created_slugs:
                continue
            row = _merge_tags_into_site_entry(sites_raw, slug, tags_value)
            if row is not None:
                tagged_rows.append(row)

        return {
            "sites": created_rows + tagged_rows,
            "plan": api_plan,
            "converted": len(created_rows),
            "tagged": len(tagged_rows),
        }

    try:
        result = update_preset_yaml_tree(preset_path, mutator, validate=True)
    except ValidationError as e:
        raise ServeSeekPlanError(str(e)) from e
    except ValueError as e:
        raise ServeSeekPlanError(str(e)) from e
    return dict(result)


def clear_seek_plan(preset_path: Path) -> None:
    def mutator(_yaml_rt: Any, root: dict[str, Any]) -> None:
        seek_raw = root.get("seek")
        if not isinstance(seek_raw, dict):
            return
        seek_raw.pop("plan", None)
        if not seek_raw:
            root.pop("seek", None)

    update_preset_yaml_tree(preset_path, mutator, validate=True, prune=False)
