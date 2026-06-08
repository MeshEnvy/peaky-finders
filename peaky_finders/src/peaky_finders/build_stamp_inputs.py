"""Filesystem paths that invalidate stamp artifacts (preset YAML + GDB trees)."""

from __future__ import annotations

from pathlib import Path

from peaky_finders.build_configure import BuildConfigurePlan, PlannedClipLayer


def sorted_unique_clip_input_paths(
    layers: tuple[PlannedClipLayer, ...],
    *,
    roles: tuple[str, ...] | None = None,
) -> tuple[Path, ...]:
    role_set = None if roles is None else set(roles)
    paths: set[Path] = set()
    for layer in layers:
        if role_set is not None and layer.role not in role_set:
            continue
        paths.update(layer.input_files)
    return tuple(sorted(paths, key=lambda p: p.as_posix()))


def stamp_input_paths(plan: BuildConfigurePlan, section: str) -> tuple[Path, ...]:
    """Input GDB files wired into land stamp prerequisites."""

    if section == "land_aoi":
        return sorted_unique_clip_input_paths(plan.clip_layers, roles=("aoi",))
    if section == "land_include":
        return sorted_unique_clip_input_paths(plan.clip_layers, roles=("include",))
    if section == "land_exclude":
        return sorted_unique_clip_input_paths(plan.clip_layers, roles=("exclude",))
    if section == "land_use":
        return sorted_unique_clip_input_paths(plan.clip_layers, roles=("include", "exclude"))
    return ()


def land_stamp_section_for_clip_role(role: str) -> str:
    mapping = {"aoi": "land_aoi", "include": "land_include", "exclude": "land_exclude"}
    return mapping[str(role)]
