"""Batch splatter coverage: one Docker run, shared DEM, per-digest outputs."""

from __future__ import annotations

import json
from pathlib import Path

from peaky_finders.build_configure import PlannedViewshedWorkspace
from peaky_finders.cli import (
    ensure_coverage_docker_image,
    resolved_coverage_docker_context,
    resolved_coverage_dockerfile,
    resolved_coverage_image,
)
from peaky_finders.models import SplatCoverageRequest
from peaky_finders.sites_job import (
    Preset,
    resolved_preset_dem_tile_cache_dir,
    resolved_viewshed_coverage_kml_style,
)
from peaky_finders.splat_pipeline import run_batch_container, vectorize_coverage_footprints_parallel


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def resolved_splatter_batch_jobs(
    *,
    preset: Preset,
    workspace_count: int,
    build_jobs: int | None = None,
) -> int:
    """Workers inside one ``run-batch`` Docker run (``PEAKY_SPLATTER_BATCH_JOBS``)."""
    cap = build_jobs if build_jobs is not None else preset.simulation.max_workers.los
    return max(1, min(cap, workspace_count))


def write_batch_request_json(
    *,
    viewshed_root: Path,
    workspaces: tuple[PlannedViewshedWorkspace, ...] | list[PlannedViewshedWorkspace],
) -> Path:
    """Write ``viewsheds/request.json`` as a JSON array for ``splatter run-batch``."""
    root = Path(viewshed_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    requests: list[dict] = []
    for ws in workspaces:
        p = Path(ws.request_json).expanduser().resolve()
        if not p.is_file():
            raise FileNotFoundError(f"Missing viewshed request: {p}")
        raw = json.loads(p.read_text(encoding="utf-8"))
        SplatCoverageRequest.model_validate(raw)
        requests.append(raw)
    out = root / "request.json"
    out.write_text(json.dumps(requests, indent=2), encoding="utf-8")
    return out


def run_viewshed_batch_docker(
    *,
    preset: Preset,
    preset_path: Path,
    viewshed_root: Path,
    workspaces: tuple[PlannedViewshedWorkspace, ...] | list[PlannedViewshedWorkspace],
    coverage_verbose: bool = False,
    build_jobs: int | None = None,
) -> int:
    """Run ``splatter run-batch`` with array ``request.json`` at *viewshed_root*."""
    if not workspaces:
        return 0

    preset_path_r = Path(preset_path).expanduser().resolve()
    viewshed_root_r = Path(viewshed_root).expanduser().resolve()
    batch_req = write_batch_request_json(viewshed_root=viewshed_root_r, workspaces=workspaces)

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
            return rc

    tile_cache = resolved_preset_dem_tile_cache_dir(preset_path_r)
    tile_cache.mkdir(parents=True, exist_ok=True)
    image = resolved_coverage_image(preset)
    n = len(workspaces)
    workers = resolved_splatter_batch_jobs(preset=preset, workspace_count=n, build_jobs=build_jobs)
    print(
        f"Coverage batch: {n} workspace(s) via splatter run-batch (workers={workers})",
        flush=True,
    )
    try:
        rc = run_batch_container(
            image=image,
            viewshed_root=viewshed_root_r,
            tile_cache_dir=tile_cache,
            batch_jobs=workers,
            coverage_verbose=coverage_verbose,
        )
        if rc == 0:
            kml_ov = preset.bundle.kml_overlay if preset.bundle else None
            style = resolved_viewshed_coverage_kml_style(kml_ov)
            vectorize_coverage_footprints_parallel(
                workdirs=[ws.workdir for ws in workspaces],
                polygon_style=style,
                jobs=workers,
            )
        return rc
    finally:
        if batch_req.is_file():
            batch_req.unlink()
