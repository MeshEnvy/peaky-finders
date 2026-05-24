"""Ephemeral viewshed workspace for suggest candidate evaluation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from shapely.geometry.base import BaseGeometry

from peaky_finders.cli import ensure_coverage_docker_image, resolved_coverage_docker_context, resolved_coverage_dockerfile, resolved_coverage_image
from peaky_finders.coverage_footprint import read_coverage_footprint
from peaky_finders.models import SplatCoverageRequest
from peaky_finders.preset_mapping import preset_to_request
from peaky_finders.sites_job import Preset, resolved_preset_dem_tile_cache_dir, resolved_viewshed_coverage_kml_style
from peaky_finders.splat_pipeline import run_viewshed_docker_only, write_coverage_footprints


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def candidate_workdir(suggest_root: Path, lat: float, lon: float) -> Path:
    body = json.dumps({"lat": lat, "lon": lon}, sort_keys=True)
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
    return Path(suggest_root).expanduser().resolve() / "candidates" / digest


def run_ephemeral_viewshed_footprint(
    *,
    preset: Preset,
    preset_path: Path,
    lat: float,
    lon: float,
    workdir: Path,
) -> BaseGeometry | None:
    """Run request → Docker → footprint in ``workdir``; return WGS-84 footprint union."""
    from peaky_finders.splat_polygonize import COVERAGE_GPKG_NAME

    wd = Path(workdir).expanduser().resolve()
    wd.mkdir(parents=True, exist_ok=True)

    req: SplatCoverageRequest = preset_to_request(preset, float(lat), float(lon))
    (wd / "request.json").write_text(req.model_dump_json(indent=2, exclude_none=True), encoding="utf-8")

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
        coverage_verbose=preset.simulation.verbose,
    )
    if rc != 0:
        raise RuntimeError(f"Ephemeral viewshed docker failed with exit code {rc}")

    kml_ov = preset.bundle.kml_overlay if preset.bundle else None
    style = resolved_viewshed_coverage_kml_style(kml_ov)
    if not write_coverage_footprints(data_dir=wd, polygon_style=style):
        return None
    return read_coverage_footprint(wd / COVERAGE_GPKG_NAME)
