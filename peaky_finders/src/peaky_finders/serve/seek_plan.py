"""Persist goal-seek committed path under ``seek.plan`` in project YAML."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from peaky_finders.core.preset.model import SeekPlan, SeekPlanHop
from peaky_finders.core.preset import load_preset, update_preset_yaml_tree


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


def clear_seek_plan(preset_path: Path) -> None:
    def mutator(_yaml_rt: Any, root: dict[str, Any]) -> None:
        seek_raw = root.get("seek")
        if not isinstance(seek_raw, dict):
            return
        seek_raw.pop("plan", None)
        if not seek_raw:
            root.pop("seek", None)

    update_preset_yaml_tree(preset_path, mutator, validate=True, prune=False)
