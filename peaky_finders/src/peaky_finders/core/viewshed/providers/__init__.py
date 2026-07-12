"""Pluggable RF viewshed coverage engines."""

from peaky_finders.core.viewshed.providers.protocol import ViewshedCoverageProvider
from peaky_finders.core.viewshed.providers.registry import (
    get_viewshed_coverage_provider,
    reset_viewshed_providers_for_tests,
)

__all__ = [
    "ViewshedCoverageProvider",
    "get_viewshed_coverage_provider",
    "reset_viewshed_providers_for_tests",
]
