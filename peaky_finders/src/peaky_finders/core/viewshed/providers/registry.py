"""Viewshed coverage provider registry."""

from __future__ import annotations

from peaky_finders.core.preset import CoverageProvider
from peaky_finders.core.viewshed.providers.protocol import ViewshedCoverageProvider
from peaky_finders.core.viewshed.providers.splat import splat_provider
from peaky_finders.core.viewshed.providers.splatter import splatter_provider

_PROVIDERS: dict[CoverageProvider, ViewshedCoverageProvider] = {
    CoverageProvider.SPLATTER: splatter_provider(),
    CoverageProvider.SPLAT: splat_provider(),
}


def get_viewshed_coverage_provider(provider: CoverageProvider) -> ViewshedCoverageProvider:
    try:
        return _PROVIDERS[provider]
    except KeyError as exc:
        raise ValueError(f"unsupported coverage provider: {provider!r}") from exc


def reset_viewshed_providers_for_tests() -> None:
    _PROVIDERS[CoverageProvider.SPLATTER] = splatter_provider()
    _PROVIDERS[CoverageProvider.SPLAT] = splat_provider()
