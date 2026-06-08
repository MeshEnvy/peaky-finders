"""Site-suggestion strategy provider protocol and planner integration hooks."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

from peaky_finders.site_suggestions.candidates import SiteCandidate
from peaky_finders.site_suggestions.context import SiteSuggestionContext
from peaky_finders.site_suggestions.depth_grid import CoverageDepthGrid
from peaky_finders.sites_job import SuggestConfig, Preset


class CandidateTrialMode(str, Enum):
    """How the planner scores and refines candidate viewshed trials."""

    COVERAGE = "coverage"
    MESH_GROW = "mesh_grow"
    CORRIDOR_GROW = "corridor_grow"


@dataclass(frozen=True)
class StrategyRefineSettings:
    refine_enabled: bool
    refine_top_n: int
    refine_radius_m: float
    refine_spacing_m: float
    refine_peaks_enabled: bool = False
    refine_peak_radius_m: float = 400.0
    refine_peak_bin_size_m: float = 150.0
    refine_peaks_per_seed: int = 8


@dataclass(frozen=True)
class StrategyPlannerHooks:
    """Planner behavior flags — strategy-specific; do not branch on ``cfg.strategy`` elsewhere."""

    trial_mode: CandidateTrialMode
    retain_trial_footprints: bool
    corridor_kml: bool


class SiteSuggestionStrategyProvider(Protocol):
    @property
    def name(self) -> str: ...

    def planner_hooks(self, cfg: SuggestConfig) -> StrategyPlannerHooks: ...

    def validate_config(self, cfg: SuggestConfig, preset: Preset) -> None: ...

    def max_suggested_nodes(self, cfg: SuggestConfig) -> int | None:
        """Optional cap on backbone site count (installed + suggested); ``None`` = no cap."""

    def prepare_suggest_context(self, ctx: SiteSuggestionContext, *, suggest_root: Path) -> None: ...

    def log_planner_config(
        self,
        *,
        verbose: bool,
        cfg: SuggestConfig,
        goal: int,
        grid: CoverageDepthGrid,
        seed_paths: list[Path],
        preset: Preset,
        jobs: int,
    ) -> None: ...

    def goal_depth(self, cfg: SuggestConfig) -> int: ...

    def refine_settings(self, cfg: SuggestConfig) -> StrategyRefineSettings: ...

    def resolve_step_budget(self, cfg: SuggestConfig, cli_n: int) -> int | None:
        """Max goals to satisfy from CLI ``--suggest[=N]``; ``None`` = until ``planning_complete``."""

    def planning_complete(self, ctx: SiteSuggestionContext) -> bool:
        """Whether the strategy goal is already satisfied (no further picks needed)."""

    def generate_candidates(
        self,
        ctx: SiteSuggestionContext,
        *,
        iteration: int,
    ) -> list[SiteCandidate]: ...
