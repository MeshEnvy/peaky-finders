"""Resolve site-suggestion strategy providers from preset config."""

from __future__ import annotations

from peaky_finders.site_suggestions.providers.land_grab.provider import LandGrabStrategy
from peaky_finders.site_suggestions.providers.mesh_backbone.provider import MeshBackboneStrategy
from peaky_finders.site_suggestions.providers.mesh_grow_ai.provider import MeshGrowAiStrategy
from peaky_finders.site_suggestions.providers.protocol import SiteSuggestionStrategyProvider
from peaky_finders.sites_job import BundleSiteSuggestionsConfig, SiteSuggestionStrategy

_PROVIDERS: dict[SiteSuggestionStrategy, SiteSuggestionStrategyProvider] = {
    SiteSuggestionStrategy.LAND_GRAB: LandGrabStrategy(),
    SiteSuggestionStrategy.MESH_BACKBONE: MeshBackboneStrategy(),
    SiteSuggestionStrategy.MESH_GROW_AI: MeshGrowAiStrategy(),
}


def resolve_site_suggestion_strategy(
    cfg: BundleSiteSuggestionsConfig,
) -> SiteSuggestionStrategyProvider:
    provider = _PROVIDERS.get(cfg.strategy)
    if provider is None:
        raise ValueError(f"unsupported site suggestion strategy: {cfg.strategy.value!r}")
    return provider
