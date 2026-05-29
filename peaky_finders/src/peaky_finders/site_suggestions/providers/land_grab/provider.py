"""Land-grab site-suggestion strategy provider."""

from __future__ import annotations

from pathlib import Path

from peaky_finders.site_suggestions.candidates import SiteCandidate, _dedupe_candidates
from peaky_finders.site_suggestions.context import SiteSuggestionContext
from peaky_finders.site_suggestions.depth_grid import CoverageDepthGrid
from peaky_finders.site_suggestions.log import suggest_log
from peaky_finders.site_suggestions.providers.land_grab.candidates import (
    _eligible_peak_candidates,
    _gap_fill_candidate,
)
from peaky_finders.site_suggestions.providers.protocol import (
    CandidateTrialMode,
    SiteSuggestionStrategyProvider,
    StrategyPlannerHooks,
    StrategyRefineSettings,
)
from peaky_finders.site_suggestions.solver import SOLVE_UNTIL_COMPLETE
from peaky_finders.sites_job import BundleSiteSuggestionsConfig, Preset


class LandGrabStrategy:
    @property
    def name(self) -> str:
        return "land-grab"

    def planner_hooks(self, cfg: BundleSiteSuggestionsConfig) -> StrategyPlannerHooks:
        del cfg
        return StrategyPlannerHooks(
            trial_mode=CandidateTrialMode.COVERAGE,
            retain_trial_footprints=False,
            corridor_kml=False,
        )

    def validate_config(self, cfg: BundleSiteSuggestionsConfig) -> None:
        del cfg

    def max_suggested_nodes(self, cfg: BundleSiteSuggestionsConfig) -> int | None:
        del cfg
        return None

    def prepare_suggest_context(self, ctx: SiteSuggestionContext, *, suggest_root: Path) -> None:
        del ctx, suggest_root

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
        from peaky_finders.site_suggestions.log import suggest_log as _log
        from peaky_finders.site_suggestions.providers.config_logging import (
            coverage_target_label,
            format_loc,
        )

        lg = cfg.land_grab
        uncovered = grid.uncovered_fraction(goal_depth=goal)
        uncovered_cells = int(grid.uncovered_mask(goal_depth=goal).sum())
        by_type: dict[str, int] = {}
        for ent in preset.sites.values():
            by_type[ent.type.value] = by_type.get(ent.type.value, 0) + 1

        _log(verbose, "site suggest: ── planner configuration ──")
        _log(verbose, f"  strategy: {self.name}")
        _log(verbose, f"  coverage_target: {cfg.coverage_target.value}")
        _log(verbose, f"  coverage_goal_depth: {goal}")
        _log(verbose, f"  planner_raster_dimension: {cfg.planner_raster_dimension}")
        _log(verbose, f"  max_candidates_per_round: {lg.max_candidates_per_round}")
        _log(verbose, f"  max_clusters_per_round: {lg.max_clusters_per_round}")
        _log(verbose, f"  peak_cluster_radius_m: {lg.peak_cluster_radius_m}")
        _log(verbose, f"  cluster_sample_spacing_m: {lg.cluster_sample_spacing_m}")
        _log(verbose, f"  cluster_sample_radius_m: {lg.cluster_sample_radius_m}")
        _log(verbose, f"  refine_enabled: {lg.refine_enabled}")
        _log(verbose, f"  refine_top_n: {lg.refine_top_n}")
        _log(verbose, f"  refine_radius_m: {lg.refine_radius_m}")
        _log(verbose, f"  refine_spacing_m: {lg.refine_spacing_m}")
        _log(verbose, f"  uncovered_stop_pct: {cfg.uncovered_stop_pct}")
        _log(verbose, f"  suggest_parallelism: {max(1, int(jobs))}")
        _log(verbose, "site suggest: ── seed sites ──")
        for slug, ent in sorted(preset.sites.items()):
            _log(verbose, f"  {slug}: type={ent.type.value} {format_loc(ent.lat, ent.lon)}")
        _log(verbose, f"  totals: {by_type}")
        target_label = coverage_target_label(cfg.coverage_target)
        _log(verbose, "site suggest: ── initial coverage grid ──")
        _log(verbose, f"  grid: {grid.cols}×{grid.rows} px  {target_label} cells: {grid.target_cell_count}")
        _log(
            verbose,
            f"  uncovered vs depth≥{goal}: {100.0 * uncovered:.2f}% ({uncovered_cells} cells)",
        )
        _log(verbose, f"  seed footprints loaded: {len(seed_paths)}")

    def goal_depth(self, cfg: BundleSiteSuggestionsConfig) -> int:
        return int(cfg.land_grab.coverage_goal_depth)

    def refine_settings(self, cfg: BundleSiteSuggestionsConfig) -> StrategyRefineSettings:
        lg = cfg.land_grab
        return StrategyRefineSettings(
            refine_enabled=bool(lg.refine_enabled),
            refine_top_n=int(lg.refine_top_n),
            refine_radius_m=float(lg.refine_radius_m),
            refine_spacing_m=float(lg.refine_spacing_m),
        )

    def resolve_step_budget(self, cfg: BundleSiteSuggestionsConfig, goal_budget: int) -> int | None:
        del cfg
        if int(goal_budget) == SOLVE_UNTIL_COMPLETE:
            return None
        return max(1, int(goal_budget))

    def planning_complete(self, ctx: SiteSuggestionContext) -> bool:
        goal = self.goal_depth(ctx.cfg)
        stop_frac = float(ctx.cfg.uncovered_stop_pct) / 100.0
        return ctx.grid.uncovered_fraction(goal_depth=goal) <= stop_frac

    def generate_candidates(
        self,
        ctx: SiteSuggestionContext,
        *,
        iteration: int,
    ) -> list[SiteCandidate]:
        lg = ctx.cfg.land_grab
        goal_depth = self.goal_depth(ctx.cfg)
        out: list[SiteCandidate] = []
        peaks, peak_stats = _eligible_peak_candidates(
            eligible_ll=ctx.eligible_ll,
            grid=ctx.grid,
            goal_depth=goal_depth,
            dem_mirror_root=ctx.dem_mirror_root,
            suggest_root=ctx.suggest_root,
            eligible_sha=ctx.eligible_sha,
            cfg=lg,
            jobs=ctx.jobs,
            verbose=ctx.verbose,
            return_stats=ctx.verbose,
        )
        if ctx.verbose and peak_stats is not None:
            suggest_log(ctx.verbose, "site suggest: ── iteration peak shortlist summary ──")
            suggest_log(ctx.verbose, f"     eligible peaks (cached index): {peak_stats['eligible_peaks_total']}")
            suggest_log(ctx.verbose, f"     still uncovered on grid: {peak_stats['uncovered_peaks']}")
            suggest_log(ctx.verbose, f"     elevation prefilter kept: {peak_stats['prefilter_peaks']}")
            suggest_log(
                ctx.verbose,
                f"     after cluster (radius={lg.peak_cluster_radius_m} m): {peak_stats['clustered_count']}",
            )
            suggest_log(ctx.verbose, f"     cluster grid samples: {peak_stats['grid_samples']}")
            suggest_log(ctx.verbose, f"     shortlist cap: {lg.max_candidates_per_round}  kept: {len(peaks)}")
        out.extend(peaks)
        if iteration > 0 or not peaks:
            gap = _gap_fill_candidate(
                grid=ctx.grid,
                goal_depth=goal_depth,
                eligible_ll=ctx.eligible_ll,
                verbose=ctx.verbose,
            )
            if gap is not None:
                if ctx.verbose:
                    suggest_log(
                        ctx.verbose,
                        f"site suggest:   gap-fill candidate @ {gap.lat:.6f}, {gap.lon:.6f}",
                    )
                out.append(gap)
            elif ctx.verbose:
                suggest_log(ctx.verbose, "site suggest:   gap-fill: no uncovered patch centroid")
        return _dedupe_candidates(out)[: max(1, int(lg.max_candidates_per_round))]
