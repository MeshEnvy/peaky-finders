"""Mesh-backbone strategy: grow mesh toward configured goals (mycelium)."""

from __future__ import annotations

from peaky_finders.site_suggestions.context import SiteSuggestionContext
from peaky_finders.site_suggestions.corridor import (
    corridor_grow_planning_complete,
)
from peaky_finders.site_suggestions.log import suggest_log, suggest_progress
from peaky_finders.site_suggestions.mesh_backbone_candidates import (
    generate_corridor_grow_candidates,
    generate_mesh_grow_candidates,
)
from peaky_finders.site_suggestions.mesh_backbone_completion import (
    all_backbone_sites,
    footprints_for_backbone_sites,
    hop_adjacency,
    hop_connected_components,
    mesh_connectivity_complete,
    mesh_grow_planning_complete,
)
from peaky_finders.site_suggestions.mesh_connectivity import healing_context, mesh_connectivity_phase
from peaky_finders.site_suggestions.mesh_goals import ordered_uncaptured_goal_keys
from peaky_finders.site_suggestions.mesh_grow import active_attractor_goal, grow_goals
from peaky_finders.site_suggestions.solver import SOLVE_UNTIL_COMPLETE
from peaky_finders.site_suggestions.strategies.base import StrategyRefineSettings
from peaky_finders.sites_job import BundleSiteSuggestionsConfig, MeshBackboneRouting


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
            refine_peaks_enabled=bool(mb.refine_peaks_enabled),
            refine_peak_radius_m=float(mb.refine_peak_radius_m),
            refine_peak_bin_size_m=float(mb.refine_peak_bin_size_m),
            refine_peaks_per_seed=int(mb.refine_peaks_per_seed),
        )

    def resolve_step_budget(self, cfg: BundleSiteSuggestionsConfig, cli_n: int) -> int | None:
        del cfg
        if int(cli_n) == SOLVE_UNTIL_COMPLETE:
            return None
        return max(1, int(cli_n))

    def uses_corridor_routing(self, cfg: BundleSiteSuggestionsConfig) -> bool:
        return cfg.mesh_backbone.routing == MeshBackboneRouting.CORRIDOR

    def planning_complete(self, ctx: SiteSuggestionContext) -> bool:
        mb = ctx.cfg.mesh_backbone
        cap = mb.max_nodes
        if cap is not None and len(all_backbone_sites(ctx)) >= int(cap):
            return True
        if self.uses_corridor_routing(ctx.cfg):
            return corridor_grow_planning_complete(ctx)
        return mesh_grow_planning_complete(ctx)

    def generate_candidates(
        self,
        ctx: SiteSuggestionContext,
        *,
        iteration: int,
    ) -> list:
        del iteration
        if self.uses_corridor_routing(ctx.cfg):
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
