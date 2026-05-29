"""Mesh-backbone site-suggestion strategy provider."""

from __future__ import annotations

from pathlib import Path

from peaky_finders.site_suggestions.context import SiteSuggestionContext
from peaky_finders.site_suggestions.corridor import CorridorGrowState, corridor_grow_planning_complete
from peaky_finders.site_suggestions.depth_grid import CoverageDepthGrid
from peaky_finders.site_suggestions.log import suggest_log, suggest_progress
from peaky_finders.site_suggestions.providers.config_logging import coverage_target_label, format_loc
from peaky_finders.site_suggestions.providers.mesh_backbone.candidates import (
    generate_corridor_grow_candidates,
    generate_mesh_grow_candidates,
)
from peaky_finders.site_suggestions.providers.mesh_backbone.completion import (
    all_backbone_sites,
    footprints_for_backbone_sites,
    hop_adjacency,
    hop_connected_components,
    mesh_connectivity_complete,
    mesh_grow_planning_complete,
)
from peaky_finders.site_suggestions.providers.mesh_backbone.goals import ordered_uncaptured_goal_keys
from peaky_finders.site_suggestions.providers.mesh_backbone.scoring import active_attractor_goal, grow_goals
from peaky_finders.site_suggestions.providers.protocol import (
    CandidateTrialMode,
    StrategyPlannerHooks,
    StrategyRefineSettings,
)
from peaky_finders.site_suggestions.mesh_connectivity import healing_context, mesh_connectivity_phase
from peaky_finders.site_suggestions.solver import SOLVE_UNTIL_COMPLETE
from peaky_finders.sites_job import BundleSiteSuggestionsConfig, MeshBackboneRouting, Preset


class MeshBackboneStrategy:
    @property
    def name(self) -> str:
        return "mesh-backbone"

    def planner_hooks(self, cfg: BundleSiteSuggestionsConfig) -> StrategyPlannerHooks:
        corridor = cfg.mesh_backbone.routing == MeshBackboneRouting.CORRIDOR
        return StrategyPlannerHooks(
            trial_mode=CandidateTrialMode.CORRIDOR_GROW if corridor else CandidateTrialMode.MESH_GROW,
            retain_trial_footprints=True,
            corridor_kml=corridor,
        )

    def validate_config(self, cfg: BundleSiteSuggestionsConfig) -> None:
        if not cfg.mesh_backbone.goals:
            raise ValueError("mesh-backbone strategy requires mesh_backbone.goals")

    def max_suggested_nodes(self, cfg: BundleSiteSuggestionsConfig) -> int | None:
        cap = cfg.mesh_backbone.max_nodes
        return int(cap) if cap is not None else None

    def prepare_suggest_context(self, ctx: SiteSuggestionContext, *, suggest_root: Path) -> None:
        if not self.planner_hooks(ctx.cfg).corridor_kml:
            return
        ctx.corridor_state = CorridorGrowState()
        from peaky_finders.site_suggestions.corridor_kml import corridor_kml_dir

        corridor_kml_dir(suggest_root).mkdir(parents=True, exist_ok=True)

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
        mb = cfg.mesh_backbone
        uncovered = grid.uncovered_fraction(goal_depth=goal)
        uncovered_cells = int(grid.uncovered_mask(goal_depth=goal).sum())
        by_type: dict[str, int] = {}
        for ent in preset.sites.values():
            by_type[ent.type.value] = by_type.get(ent.type.value, 0) + 1

        suggest_log(verbose, "site suggest: ── planner configuration ──")
        suggest_log(verbose, f"  strategy: {self.name}")
        suggest_log(verbose, f"  coverage_target: {cfg.coverage_target.value}")
        suggest_log(verbose, f"  coverage_goal_depth: {goal}")
        suggest_log(verbose, f"  planner_raster_dimension: {cfg.planner_raster_dimension}")
        suggest_log(verbose, f"  max_candidates_per_round: {mb.max_candidates_per_round}")
        suggest_log(verbose, f"  frontier_sample_spacing_m: {mb.frontier_sample_spacing_m}")
        suggest_log(verbose, f"  configured goals: {len(mb.goals)}")
        if mb.max_nodes is not None:
            suggest_log(verbose, f"  max_nodes: {mb.max_nodes}")
        suggest_log(verbose, f"  routing: {mb.routing.value}")
        if mb.routing == MeshBackboneRouting.CORRIDOR:
            suggest_log(verbose, f"  corridor_k: {mb.corridor_k}")
            suggest_log(verbose, f"  corridor_grid_cell_m: {mb.corridor_grid_cell_m}")
            suggest_log(verbose, f"  corridor_buffer_m: {mb.corridor_buffer_m}")
            suggest_log(verbose, f"  corridor_lookahead_m: {mb.corridor_lookahead_m}")
            suggest_log(verbose, f"  stall_rounds: {mb.stall_rounds}")
            if mb.goal_order:
                suggest_log(verbose, f"  goal_order: {', '.join(mb.goal_order)}")
        suggest_log(verbose, f"  refine_enabled: {mb.refine_enabled}")
        suggest_log(verbose, f"  refine_top_n: {mb.refine_top_n}")
        suggest_log(verbose, f"  refine_radius_m: {mb.refine_radius_m}")
        suggest_log(verbose, f"  refine_spacing_m: {mb.refine_spacing_m}")
        suggest_log(verbose, f"  refine_peaks_enabled: {mb.refine_peaks_enabled}")
        if mb.refine_peaks_enabled:
            suggest_log(verbose, f"  refine_peak_radius_m: {mb.refine_peak_radius_m}")
            suggest_log(verbose, f"  refine_peak_bin_size_m: {mb.refine_peak_bin_size_m}")
            suggest_log(verbose, f"  refine_peaks_per_seed: {mb.refine_peaks_per_seed}")
        suggest_log(verbose, f"  coarse_peaks_enabled: {mb.coarse_peaks_enabled}")
        if mb.coarse_peaks_enabled:
            suggest_log(verbose, f"  coarse_peak_radius_m: {mb.coarse_peak_radius_m}")
            suggest_log(verbose, f"  coarse_peaks_per_sample: {mb.coarse_peaks_per_sample}")
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
        mb = cfg.mesh_backbone
        return StrategyRefineSettings(
            refine_enabled=bool(mb.refine_enabled),
            refine_top_n=int(mb.refine_top_n),
            refine_radius_m=float(mb.refine_radius_m),
            refine_spacing_m=float(mb.refine_spacing_m),
            refine_peaks_enabled=bool(mb.refine_peaks_enabled),
            refine_peak_radius_m=float(mb.refine_peak_radius_m),
            refine_peak_bin_size_m=float(mb.refine_peak_bin_size_m),
            refine_peaks_per_seed=int(mb.refine_peaks_per_seed),
        )

    def resolve_step_budget(self, cfg: BundleSiteSuggestionsConfig, goal_budget: int) -> int | None:
        del cfg
        if int(goal_budget) == SOLVE_UNTIL_COMPLETE:
            return None
        return max(1, int(goal_budget))

    def _uses_corridor_routing(self, cfg: BundleSiteSuggestionsConfig) -> bool:
        return cfg.mesh_backbone.routing == MeshBackboneRouting.CORRIDOR

    def planning_complete(self, ctx: SiteSuggestionContext) -> bool:
        cap = self.max_suggested_nodes(ctx.cfg)
        if cap is not None and len(all_backbone_sites(ctx)) >= cap:
            return True
        if self._uses_corridor_routing(ctx.cfg):
            return corridor_grow_planning_complete(ctx)
        return mesh_grow_planning_complete(ctx)

    def generate_candidates(
        self,
        ctx: SiteSuggestionContext,
        *,
        iteration: int,
    ) -> list:
        del iteration
        if self._uses_corridor_routing(ctx.cfg):
            return self._generate_corridor_candidates(ctx)
        return self._generate_greedy_candidates(ctx)

    def _generate_greedy_candidates(self, ctx: SiteSuggestionContext) -> list:
        remaining = grow_goals(ctx)
        if ctx.verbose:
            self._log_grow_status(ctx, remaining, greedy=True)
        return generate_mesh_grow_candidates(ctx)

    def _generate_corridor_candidates(self, ctx: SiteSuggestionContext) -> list:
        if ctx.verbose:
            suggest_progress(ctx.verbose, "corridor-grow: gather goal status…")
            state = ctx.corridor_state
            active = state.active_goal_key if state else None
            blocked = len(state.blocked_goal_keys) if state else 0
            pending = ordered_uncaptured_goal_keys(ctx)
            phase = "bridge" if not mesh_connectivity_complete(ctx) else "grow"
            suggest_log(ctx.verbose, "site suggest: ── corridor-grow status ──")
            suggest_log(
                ctx.verbose,
                f"     phase={phase}  active={active or '?'}  pending={len(pending)}  "
                f"blocked={blocked}  selection by hop-valid Δs along corridor",
            )
        candidates = generate_corridor_grow_candidates(ctx)
        if ctx.verbose:
            suggest_log(
                ctx.verbose,
                f"site suggest:     corridor-grow done: {len(candidates)} candidate(s)",
            )
        return candidates

    def _log_grow_status(self, ctx: SiteSuggestionContext, remaining, *, greedy: bool) -> None:
        phase = mesh_connectivity_phase(ctx)
        nearest = active_attractor_goal(ctx)
        nearest_label = nearest.key if nearest is not None else "?"
        dist_m = (
            ctx.grid.min_distance_coverage_to_point_m(nearest.lon, nearest.lat, min_depth=1)
            if nearest is not None
            else 0.0
        )
        if phase == "heal":
            healing = healing_context(ctx)
            sites = all_backbone_sites(ctx)
            footprints = footprints_for_backbone_sites(ctx.plan, ctx.session_footprints)
            adj = hop_adjacency(sites, footprints)
            n_components = len(hop_connected_components(adj, [s.slug for s in sites]))
            main_count = len(healing.main_slugs) if healing is not None else 0
            suggest_log(ctx.verbose, "site suggest: ── mesh-heal status ──")
            suggest_log(
                ctx.verbose,
                f"     components={n_components} main={main_count} site(s) "
                f"bridge goals={len(remaining)} nearest={nearest_label} "
                f"({dist_m / 1000.0:.1f} km); selection by best hop-valid Δdist",
            )
        else:
            suggest_log(ctx.verbose, "site suggest: ── mesh-grow status ──")
            if not remaining:
                suggest_log(ctx.verbose, "     all configured goals captured and connected")
            else:
                mode = "best hop-valid Δdist" if greedy else "Δs along corridor"
                suggest_log(
                    ctx.verbose,
                    f"     uncaptured: {len(remaining)} goal(s); nearest={nearest_label} "
                    f"({dist_m / 1000.0:.1f} km); selection by {mode}",
                )
