"""Orchestrate suggest pass during ``peaky build --suggest``."""

from __future__ import annotations

from pathlib import Path

from peaky_finders.build_configure import BuildConfigurePlan
from peaky_finders.site_suggestions.planner import (
    PlannedSuggestion,
    plan_greedy_site_suggestions,
    planned_to_preset_entries,
)
from peaky_finders.site_suggestions.preset_io import append_suggested_sites_to_preset, remove_suggested_sites_from_preset
from peaky_finders.site_suggestions.solver import SOLVE_UNTIL_COMPLETE
from peaky_finders.sites_job import Preset, load_preset, resolved_preset_build_dir


def resolved_site_suggest_dir(preset_path: Path) -> Path:
    return resolved_preset_build_dir(preset_path) / "suggest"


def run_site_suggestion_pass(
    *,
    preset: Preset,
    preset_path: Path,
    plan: BuildConfigurePlan,
    suggest_cli_n: int,
    replace_suggested: bool = False,
    verbose: bool = False,
    jobs: int = 1,
    footprint_runner=None,
) -> list[str]:
    """Plan sites, append to preset YAML, return new slugs."""
    preset_path_r = Path(preset_path).expanduser().resolve()
    if replace_suggested:
        n_removed = remove_suggested_sites_from_preset(preset_path_r)
        if n_removed:
            print(f"site suggest: removed {n_removed} prior suggested site(s)", flush=True)
        preset = load_preset(preset_path_r)

    suggest_root = resolved_site_suggest_dir(preset_path_r)
    budget_label = (
        "solve"
        if int(suggest_cli_n) == SOLVE_UNTIL_COMPLETE
        else f"{int(suggest_cli_n)} goal(s)"
    )
    if verbose:
        print(
            f"site suggest: starting {budget_label} pass "
            f"replace_suggested={replace_suggested}",
            flush=True,
        )
    runner = footprint_runner
    if runner is None and verbose:
        print("site suggest: using splatter run-batch for candidate viewsheds", flush=True)

    new_slugs: list[str] = []

    def _persist_pick(pick: PlannedSuggestion) -> str:
        slugs = append_suggested_sites_to_preset(
            preset_path_r,
            planned_to_preset_entries([pick]),
        )
        slug = slugs[0]
        new_slugs.append(slug)
        return slug

    plan_greedy_site_suggestions(
        preset=preset,
        preset_path=preset_path_r,
        plan=plan,
        suggest_cli_n=int(suggest_cli_n),
        suggest_root=suggest_root,
        footprint_runner=runner,
        verbose=verbose,
        jobs=jobs,
        on_pick=_persist_pick,
    )
    if new_slugs:
        print(f"site suggest: wrote {len(new_slugs)} site(s) to {preset_path_r}", flush=True)
    else:
        print("site suggest: no new sites written", flush=True)
    return new_slugs
