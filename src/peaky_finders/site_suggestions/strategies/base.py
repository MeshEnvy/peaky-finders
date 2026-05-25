"""Strategy provider protocol for site suggestion."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from peaky_finders.site_suggestions.candidates import SiteCandidate
from peaky_finders.site_suggestions.context import SiteSuggestionContext
from peaky_finders.sites_job import BundleSiteSuggestionsConfig


@dataclass(frozen=True)
class StrategyRefineSettings:
    refine_enabled: bool
    refine_top_n: int
    refine_radius_m: float
    refine_spacing_m: float


class SiteSuggestionStrategyProvider(Protocol):
    @property
    def name(self) -> str: ...

    def goal_depth(self, cfg: BundleSiteSuggestionsConfig) -> int: ...

    def refine_settings(self, cfg: BundleSiteSuggestionsConfig) -> StrategyRefineSettings: ...

    def generate_candidates(
        self,
        ctx: SiteSuggestionContext,
        *,
        iteration: int,
    ) -> list[SiteCandidate]: ...
