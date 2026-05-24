"""Orchestrate suggest pass during ``peaky build --suggest``."""

from __future__ import annotations

from pathlib import Path

from peaky_finders.build_configure import BuildConfigurePlan
from peaky_finders.site_suggestions.planner import (
    plan_greedy_site_suggestions,
    planned_to_preset_entries,
    write_suggest_run_manifest,
)
from peaky_finders.site_suggestions.preset_io import append_suggested_sites_to_preset, remove_suggested_sites_from_preset
from peaky_finders.sites_job import Preset, resolved_preset_build_dir


def resolved_site_suggest_dir(preset_path: Path) -> Path:
    return resolved_preset_build_dir(preset_path) / "suggest"


def run_site_suggestion_pass(
    *,
    preset: Preset,
    preset_path: Path,
    plan: BuildConfigurePlan,
    n_suggestions: int,
    replace_suggested: bool = False,
    verbose: bool = False,
    footprint_runner=None,
) -> list[str]:
    """Plan sites, append to preset YAML, return new slugs."""
    preset_path_r = Path(preset_path).expanduser().resolve()
    if replace_suggested:
        n_removed = remove_suggested_sites_from_preset(preset_path_r)
        if n_removed:
            print(f"site suggest: removed {n_removed} prior suggested site(s)", flush=True)

    suggest_root = resolved_site_suggest_dir(preset_path_r)
    if verbose:
        print(
            f"site suggest: starting greedy pass n={n_suggestions} "
            f"replace_suggested={replace_suggested}",
            flush=True,
        )
    runner = footprint_runner
    if runner is None:
        from peaky_finders.site_suggestions.ephemeral_viewshed import run_ephemeral_viewshed_footprint

        runner = run_ephemeral_viewshed_footprint

    winners = plan_greedy_site_suggestions(
        preset=preset,
        preset_path=preset_path_r,
        plan=plan,
        n_suggestions=n_suggestions,
        suggest_root=suggest_root,
        footprint_runner=runner,
        verbose=verbose,
    )
    entries = planned_to_preset_entries(winners)
    new_slugs = append_suggested_sites_to_preset(preset_path_r, entries)
    write_suggest_run_manifest(
        suggest_root,
        preset_path=preset_path_r,
        n_requested=n_suggestions,
        winners=winners,
        new_slugs=new_slugs,
    )
    if new_slugs:
        print(f"site suggest: wrote {len(new_slugs)} site(s) to {preset_path_r}", flush=True)
    else:
        print("site suggest: no new sites written", flush=True)
    return new_slugs
