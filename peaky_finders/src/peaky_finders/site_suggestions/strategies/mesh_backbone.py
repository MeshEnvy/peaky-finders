"""Mesh-backbone strategy: grow mesh toward configured goals (mycelium)."""

from __future__ import annotations

from peaky_finders.site_suggestions.context import SiteSuggestionContext
from peaky_finders.site_suggestions.log import suggest_log
from peaky_finders.site_suggestions.mesh_backbone_candidates import generate_mesh_grow_candidates
from peaky_finders.site_suggestions.mesh_backbone_completion import all_backbone_sites, mesh_grow_planning_complete
from peaky_finders.site_suggestions.mesh_grow import active_attractor_goal, uncaptured_goals
from peaky_finders.site_suggestions.solver import SOLVE_UNTIL_COMPLETE
from peaky_finders.site_suggestions.strategies.base import StrategyRefineSettings
from peaky_finders.sites_job import BundleSiteSuggestionsConfig


class MeshBackboneStrategy:
    @property
    def name(self) -> str:
        return "mesh-backbone"

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
        )

    def resolve_step_budget(self, cfg: BundleSiteSuggestionsConfig, cli_n: int) -> int | None:
        del cfg
        if int(cli_n) == SOLVE_UNTIL_COMPLETE:
            return None
        return max(1, int(cli_n))

    def planning_complete(self, ctx: SiteSuggestionContext) -> bool:
        mb = ctx.cfg.mesh_backbone
        cap = mb.max_nodes
        if cap is not None and len(all_backbone_sites(ctx)) >= int(cap):
            return True
        return mesh_grow_planning_complete(ctx)

    def generate_candidates(
        self,
        ctx: SiteSuggestionContext,
        *,
        iteration: int,
    ) -> list:
        del iteration
        remaining = uncaptured_goals(ctx)
        if ctx.verbose:
            suggest_log(ctx.verbose, "site suggest: ── mesh-grow status ──")
            if not remaining:
                suggest_log(ctx.verbose, "     all configured goals captured and connected")
            else:
                nearest = active_attractor_goal(ctx)
                nearest_label = nearest.key if nearest is not None else "?"
                dist_m = (
                    ctx.grid.min_distance_coverage_to_point_m(
                        nearest.lon, nearest.lat, min_depth=1
                    )
                    if nearest is not None
                    else 0.0
                )
                suggest_log(
                    ctx.verbose,
                    f"     uncaptured: {len(remaining)} goal(s); nearest={nearest_label} "
                    f"({dist_m / 1000.0:.1f} km); selection by best hop-valid Δdist",
                )
        return generate_mesh_grow_candidates(ctx)
