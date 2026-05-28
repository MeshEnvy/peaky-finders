"""Shared verbose logging helpers for strategy providers."""

from __future__ import annotations

from peaky_finders.sites_job import SiteSuggestionCoverageTarget


def format_loc(lat: float, lon: float) -> str:
    return f"{lat:.6f}°N, {lon:.6f}°W" if lon < 0 else f"{lat:.6f}°, {lon:.6f}°"


def coverage_target_label(target: SiteSuggestionCoverageTarget) -> str:
    if target == SiteSuggestionCoverageTarget.ELIGIBLE:
        return "eligible"
    return "AOI"
