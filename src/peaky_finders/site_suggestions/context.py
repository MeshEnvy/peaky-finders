"""Shared runtime context for site-suggestion strategy providers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from shapely.geometry.base import BaseGeometry

from peaky_finders.build_configure import BuildConfigurePlan
from peaky_finders.site_suggestions.depth_grid import CoverageDepthGrid
from peaky_finders.sites_job import BundleSiteSuggestionsConfig, Preset


@dataclass(frozen=True)
class BackboneSite:
    slug: str
    lat: float
    lon: float


@dataclass
class SiteSuggestionContext:
    preset: Preset
    plan: BuildConfigurePlan
    grid: CoverageDepthGrid
    eligible_ll: BaseGeometry
    aoi_ll: BaseGeometry
    target_ll: BaseGeometry
    suggest_root: Path
    cfg: BundleSiteSuggestionsConfig
    dem_mirror_root: Path
    eligible_sha: str
    jobs: int
    verbose: bool
    session_sites: list[BackboneSite] = field(default_factory=list)
    session_footprints: dict[str, BaseGeometry] = field(default_factory=dict)
