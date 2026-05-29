"""Ephemeral viewshed workspace for suggest candidate evaluation."""

from __future__ import annotations

from pathlib import Path

from shapely.geometry.base import BaseGeometry

from peaky_finders.build_fresh_checks import viewshed_request_digest_matches
from peaky_finders.coverage_footprint import read_coverage_footprint
from peaky_finders.preset_mapping import preset_to_request
from peaky_finders.sites_job import Preset
from peaky_finders.splat_pipeline import run_viewshed_workspace
from peaky_finders.splat_polygonize import SPLAT_GPKG_NAME
from peaky_finders.viewshed_workspace import (
    resolved_viewshed_workdir_for_coords,
    viewshed_workspace_digest,
)


def run_ephemeral_viewshed_footprint(
    *,
    preset: Preset,
    preset_path: Path,
    lat: float,
    lon: float,
    workdir: Path,
    verbose: bool = False,
) -> BaseGeometry | None:
    """Run request → coverage → footprint in ``workdir``; return WGS-84 footprint union."""
    _ = preset_path
    wd = Path(workdir).expanduser().resolve()
    wd.mkdir(parents=True, exist_ok=True)

    req = preset_to_request(preset, float(lat), float(lon))
    digest = viewshed_workspace_digest(request=req)
    cov_gpkg = wd / SPLAT_GPKG_NAME
    if cov_gpkg.is_file() and viewshed_request_digest_matches(wd, expected_workspace_digest=digest):
        return read_coverage_footprint(cov_gpkg)

    run_viewshed_workspace(
        preset=preset,
        lat=float(lat),
        lon=float(lon),
        workdir=wd,
        site_label=f"suggest {lat:.5f},{lon:.5f}",
        coverage_verbose=verbose,
        raster=False,
        footprint=True,
    )
    return read_coverage_footprint(cov_gpkg)


def candidate_viewshed_workdir(
    *,
    preset: Preset,
    viewshed_root: Path,
    lat: float,
    lon: float,
) -> Path:
    """Shared viewshed cache path for a candidate coordinate (same layout as ``peaky build``)."""
    return resolved_viewshed_workdir_for_coords(
        preset=preset,
        viewshed_root=viewshed_root,
        lat=lat,
        lon=lon,
    )
