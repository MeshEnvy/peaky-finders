"""Resolve site-suggestion strategy providers from preset config."""

from __future__ import annotations

from peaky_finders.site_suggestions.strategies.base import SiteSuggestionStrategyProvider
from peaky_finders.site_suggestions.strategies.land_grab import LandGrabStrategy
from peaky_finders.sites_job import BundleSiteSuggestionsConfig, SiteSuggestionStrategy

_REGISTRY: dict[SiteSuggestionStrategy, SiteSuggestionStrategyProvider] = {
    SiteSuggestionStrategy.LAND_GRAB: LandGrabStrategy(),
}


def resolve_site_suggestion_strategy(
    cfg: BundleSiteSuggestionsConfig,
) -> SiteSuggestionStrategyProvider:
    provider = _REGISTRY.get(cfg.strategy)
    if provider is None:
        raise ValueError(f"unsupported site suggestion strategy: {cfg.strategy.value!r}")
    return provider
