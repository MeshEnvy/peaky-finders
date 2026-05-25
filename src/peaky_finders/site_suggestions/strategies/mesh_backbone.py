"""Mesh-backbone strategy: connect anchor↔anchor links via hop chains."""

from __future__ import annotations

from peaky_finders.site_suggestions.context import SiteSuggestionContext
from peaky_finders.site_suggestions.log import suggest_log
from peaky_finders.site_suggestions.mesh_backbone_candidates import (
    generate_mesh_backbone_candidates,
    incomplete_link_results,
)
from peaky_finders.site_suggestions.mesh_backbone_completion import mesh_backbone_planning_complete
from peaky_finders.site_suggestions.solver import SOLVE_UNTIL_COMPLETE
from peaky_finders.site_suggestions.strategies.base import StrategyRefineSettings
from peaky_finders.sites_job import BundleSiteSuggestionsConfig


class MeshBackboneStrategy:
    @property
    def name(self) -> str:
        return "mesh-backbone"

    def goal_depth(self, cfg: BundleSiteSuggestionsConfig) -> int:
        return int(cfg.mesh_backbone.site_goal_depth)

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
        return mesh_backbone_planning_complete(ctx)

    def generate_candidates(
        self,
        ctx: SiteSuggestionContext,
        *,
        iteration: int,
    ) -> list:
        del iteration
        incomplete = incomplete_link_results(ctx)
        if ctx.verbose:
            suggest_log(ctx.verbose, "site suggest: ── mesh-backbone link status ──")
            if not incomplete:
                suggest_log(ctx.verbose, "     all configured links complete")
            else:
                for result in incomplete:
                    suggest_log(ctx.verbose, f"     incomplete: {result.detail}")
                suggest_log(
                    ctx.verbose,
                    f"     sampling incomplete link strip(s): {len(incomplete)} link(s)",
                )
        goal = self.goal_depth(ctx.cfg)
        return generate_mesh_backbone_candidates(ctx, goal_depth=goal)
