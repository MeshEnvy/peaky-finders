"""Ephemeral viewshed workspace for suggest candidate evaluation."""

from __future__ import annotations

from pathlib import Path

from shapely.geometry.base import BaseGeometry

from peaky_finders.build_fresh_checks import viewshed_request_digest_matches
from peaky_finders.cli import ensure_coverage_docker_image, resolved_coverage_docker_context, resolved_coverage_dockerfile, resolved_coverage_image
from peaky_finders.coverage_footprint import read_coverage_footprint
from peaky_finders.models import SplatCoverageRequest
from peaky_finders.preset_mapping import preset_to_request
from peaky_finders.sites_job import Preset, resolved_preset_dem_tile_cache_dir, resolved_viewshed_coverage_kml_style
from peaky_finders.splat_pipeline import run_viewshed_docker_only, write_coverage_footprints
from peaky_finders.viewshed_workspace import (
    resolved_viewshed_workdir_for_coords,
    viewshed_workspace_digest,
)


def _repo_root() -> Path:
    from peaky_finders.sites_job import repo_root

    return repo_root()


def run_ephemeral_viewshed_footprint(
    *,
    preset: Preset,
    preset_path: Path,
    lat: float,
    lon: float,
    workdir: Path,
    verbose: bool = False,
) -> BaseGeometry | None:
    """Run request → Docker → footprint in ``workdir``; return WGS-84 footprint union."""
    from peaky_finders.splat_polygonize import SPLAT_GPKG_NAME

    wd = Path(workdir).expanduser().resolve()
    wd.mkdir(parents=True, exist_ok=True)

    req: SplatCoverageRequest = preset_to_request(preset, float(lat), float(lon))
    digest = viewshed_workspace_digest(request=req)
    (wd / "request.json").write_text(req.model_dump_json(indent=2, exclude_none=True), encoding="utf-8")

    cov_gpkg = wd / SPLAT_GPKG_NAME
    if cov_gpkg.is_file() and viewshed_request_digest_matches(wd, expected_workspace_digest=digest):
        return read_coverage_footprint(cov_gpkg)

    if preset.build_docker:
        repo = _repo_root()
        image = resolved_coverage_image(preset)
        rc = ensure_coverage_docker_image(
            repo,
            dockerfile_name=resolved_coverage_dockerfile(preset),
            image=image,
            context=resolved_coverage_docker_context(preset),
        )
        if rc != 0:
            raise RuntimeError(f"Docker image build failed with exit code {rc}")

    tile_cache = resolved_preset_dem_tile_cache_dir(preset_path)
    tile_cache.mkdir(parents=True, exist_ok=True)
    rc = run_viewshed_docker_only(
        site_name=f"suggest {lat:.5f},{lon:.5f}",
        image=resolved_coverage_image(preset),
        provider=preset.simulation.provider,
        data_dir=wd,
        tile_cache_dir=tile_cache,
        coverage_verbose=verbose,
    )
    if rc != 0:
        raise RuntimeError(f"Ephemeral viewshed docker failed with exit code {rc}")

    kml_ov = preset.bundle.kml_overlay if preset.bundle else None
    style = resolved_viewshed_coverage_kml_style(kml_ov)
    if not write_coverage_footprints(data_dir=wd, polygon_style=style):
        return None
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
