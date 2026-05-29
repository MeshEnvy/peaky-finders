"""Mesh-grow-ai site-suggestion strategy provider."""

from __future__ import annotations

from pathlib import Path

from peaky_finders.site_suggestions.context import SiteSuggestionContext
from peaky_finders.site_suggestions.depth_grid import CoverageDepthGrid
from peaky_finders.site_suggestions.log import suggest_log
from peaky_finders.site_suggestions.providers.config_logging import coverage_target_label, format_loc
from peaky_finders.site_suggestions.providers.mesh_backbone.completion import (
    all_backbone_sites,
    mesh_grow_planning_complete,
)
from peaky_finders.site_suggestions.providers.mesh_backbone.goals import ordered_uncaptured_goal_keys
from peaky_finders.site_suggestions.providers.mesh_backbone.scoring import grow_goals
from peaky_finders.site_suggestions.providers.mesh_grow_ai.agent import run_mesh_grow_agent
from peaky_finders.site_suggestions.providers.protocol import (
    CandidateTrialMode,
    StrategyPlannerHooks,
    StrategyRefineSettings,
)
from peaky_finders.site_suggestions.solver import SOLVE_UNTIL_COMPLETE
from peaky_finders.sites_job import BundleSiteSuggestionsConfig, Preset


class MeshGrowAiStrategy:
    def __init__(self) -> None:
        self._preset_path: Path | None = None
        self._emit = None

    @property
    def name(self) -> str:
        return "mesh-grow-ai"

    def planner_hooks(self, cfg: BundleSiteSuggestionsConfig) -> StrategyPlannerHooks:
        del cfg
        return StrategyPlannerHooks(
            trial_mode=CandidateTrialMode.MESH_GROW,
            retain_trial_footprints=True,
            corridor_kml=False,
        )

    def validate_config(self, cfg: BundleSiteSuggestionsConfig) -> None:
        if not cfg.mesh_grow_ai.goals:
            raise ValueError("mesh-grow-ai strategy requires mesh_grow_ai.goals")

    def max_suggested_nodes(self, cfg: BundleSiteSuggestionsConfig) -> int | None:
        cap = cfg.mesh_grow_ai.max_nodes
        return int(cap) if cap is not None else None

    def prepare_suggest_context(self, ctx: SiteSuggestionContext, *, suggest_root: Path) -> None:
        del ctx, suggest_root

    def set_runtime(self, *, preset_path: Path, emit=None) -> None:
        self._preset_path = preset_path
        self._emit = emit

    def log_planner_config(
        self,
        *,
        verbose: bool,
        cfg: BundleSiteSuggestionsConfig,
        goal: int,
        grid: CoverageDepthGrid,
        seed_paths: list[Path],
        preset: Preset,
        jobs: int,
    ) -> None:
        if not verbose:
            return
        mesh_ai = cfg.mesh_grow_ai
        ollama = preset.ai
        uncovered = grid.uncovered_fraction(goal_depth=goal)
        uncovered_cells = int(grid.uncovered_mask(goal_depth=goal).sum())
        by_type: dict[str, int] = {}
        for ent in preset.sites.values():
            by_type[ent.type.value] = by_type.get(ent.type.value, 0) + 1

        suggest_log(verbose, "site suggest: ── planner configuration ──")
        suggest_log(verbose, f"  strategy: {self.name}")
        suggest_log(verbose, f"  ollama_model: {ollama.model}")
        suggest_log(verbose, f"  ollama_endpoint: {ollama.endpoint}")
        suggest_log(verbose, f"  max_agent_steps: {mesh_ai.max_agent_steps}")
        suggest_log(verbose, f"  max_viewshed_evals_per_episode: {mesh_ai.max_viewshed_evals_per_episode}")
        suggest_log(verbose, f"  max_candidates_per_round: {mesh_ai.max_candidates_per_round}")
        suggest_log(verbose, f"  configured goals: {len(mesh_ai.goals)}")
        if mesh_ai.max_nodes is not None:
            suggest_log(verbose, f"  max_nodes: {mesh_ai.max_nodes}")
        suggest_log(verbose, f"  suggest_parallelism: {max(1, int(jobs))}")
        suggest_log(verbose, "site suggest: ── seed sites ──")
        for slug, ent in sorted(preset.sites.items()):
            suggest_log(verbose, f"  {slug}: type={ent.type.value} {format_loc(ent.lat, ent.lon)}")
        suggest_log(verbose, f"  totals: {by_type}")
        target_label = coverage_target_label(cfg.coverage_target)
        suggest_log(verbose, "site suggest: ── initial coverage grid ──")
        suggest_log(verbose, f"  grid: {grid.cols}×{grid.rows} px  {target_label} cells: {grid.target_cell_count}")
        suggest_log(
            verbose,
            f"  uncovered vs depth≥{goal}: {100.0 * uncovered:.2f}% ({uncovered_cells} cells)",
        )
        suggest_log(verbose, f"  seed footprints loaded: {len(seed_paths)}")

    def goal_depth(self, cfg: BundleSiteSuggestionsConfig) -> int:
        del cfg
        return 1

    def refine_settings(self, cfg: BundleSiteSuggestionsConfig) -> StrategyRefineSettings:
        ai = cfg.mesh_grow_ai
        return StrategyRefineSettings(
            refine_enabled=bool(ai.refine_enabled),
            refine_top_n=int(ai.refine_top_n),
            refine_radius_m=float(ai.refine_radius_m),
            refine_spacing_m=float(ai.refine_spacing_m),
            refine_peaks_enabled=bool(ai.refine_peaks_enabled),
            refine_peak_radius_m=float(ai.refine_peak_radius_m),
            refine_peak_bin_size_m=float(ai.refine_peak_bin_size_m),
            refine_peaks_per_seed=int(ai.refine_peaks_per_seed),
        )

    def resolve_step_budget(self, cfg: BundleSiteSuggestionsConfig, cli_n: int) -> int | None:
        del cfg
        if int(cli_n) == SOLVE_UNTIL_COMPLETE:
            return None
        return max(1, int(cli_n))

    def planning_complete(self, ctx: SiteSuggestionContext) -> bool:
        cap = self.max_suggested_nodes(ctx.cfg)
        if cap is not None and len(all_backbone_sites(ctx)) >= cap:
            return True
        return mesh_grow_planning_complete(ctx)

    def generate_candidates(
        self,
        ctx: SiteSuggestionContext,
        *,
        iteration: int,
    ) -> list:
        if self._preset_path is None:
            raise RuntimeError("mesh-grow-ai provider missing preset_path (set_runtime not called)")
        remaining = grow_goals(ctx)
        if ctx.verbose:
            pending = ordered_uncaptured_goal_keys(ctx)
            suggest_log(ctx.verbose, "site suggest: ── mesh-grow-ai status ──")
            suggest_log(
                ctx.verbose,
                f"     uncaptured goals={len(remaining)} pending_order={len(pending)} "
                f"iteration={iteration}",
            )
        return run_mesh_grow_agent(
            ctx,
            preset_path=self._preset_path,
            iteration=iteration,
            emit=self._emit,
        )
