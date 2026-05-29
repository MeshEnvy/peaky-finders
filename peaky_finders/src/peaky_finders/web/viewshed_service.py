"""On-demand viewshed ensure + metadata for the web API."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from peaky_finders.build_executor import run_incremental_build
from peaky_finders.sites_job import load_preset
from peaky_finders.viewshed_workspace import resolved_viewshed_workdir_for_coords
from peaky_finders.web.viewshed_rasters import (
    _bounds_for_workdir,
    _raster_opacity,
    _viewsheds_root_for_preset,
    preset_path_for_project,
    resolve_splat_png_path,
)
from peaky_finders.web.viewshed_tiles import tile_layer_metadata

_ensure_locks_guard = threading.Lock()
_ensure_locks: dict[tuple[str, str], threading.Lock] = {}


def _ensure_lock(project_slug: str, site_slug: str) -> threading.Lock:
    key = (project_slug, site_slug)
    with _ensure_locks_guard:
        lock = _ensure_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _ensure_locks[key] = lock
        return lock


def viewshed_is_cached(*, project_slug: str, site_slug: str) -> bool:
    try:
        resolve_splat_png_path(project_slug=project_slug, site_slug=site_slug)
    except FileNotFoundError:
        return False
    return True


def viewshed_raster_record(
    *,
    project_slug: str,
    site_slug: str,
    computed: bool = False,
) -> dict[str, Any]:
    cfg = preset_path_for_project(project_slug)
    preset = load_preset(cfg)
    if site_slug not in preset.sites:
        raise FileNotFoundError(f"unknown site {site_slug!r} in project {project_slug!r}")

    resolve_splat_png_path(project_slug=project_slug, site_slug=site_slug)
    viewsheds_root = _viewsheds_root_for_preset(cfg)
    if viewsheds_root is None:
        raise FileNotFoundError("viewshed root unavailable")

    site = preset.sites[site_slug]
    workdir = resolved_viewshed_workdir_for_coords(
        preset=preset,
        viewshed_root=viewsheds_root,
        lat=float(site.lat),
        lon=float(site.lon),
    )
    bounds = _bounds_for_workdir(workdir)
    if bounds is None:
        raise FileNotFoundError(f"missing viewshed bounds for site {site_slug!r}")

    tiles = tile_layer_metadata(
        project_slug=project_slug,
        site_slug=site_slug,
        workdir=workdir,
        bounds=bounds,
    )
    return {
        "slug": site_slug,
        "digest": workdir.name,
        "url": f"/api/projects/{project_slug}/viewsheds/{site_slug}/splat.png",
        "opacity": _raster_opacity(preset),
        "cached": not computed,
        "computed": computed,
        **tiles,
    }


def ensure_site_viewshed(
    *,
    project_slug: str,
    site_slug: str,
    force: bool = False,
    jobs: int = 1,
) -> dict[str, Any]:
    """Ensure ``splat.png`` exists for a preset site; compute via incremental build on miss."""
    cfg = preset_path_for_project(project_slug)
    preset = load_preset(cfg)
    if site_slug not in preset.sites:
        raise FileNotFoundError(f"unknown site {site_slug!r} in project {project_slug!r}")

    computed = False
    lock = _ensure_lock(project_slug, site_slug)
    with lock:
        if force or not viewshed_is_cached(project_slug=project_slug, site_slug=site_slug):
            rc = run_incremental_build(
                preset_path=cfg,
                data_dir_arg=None,
                selection=f"viewshed/{site_slug}",
                force=force,
                dry_run=False,
                jobs=jobs,
                verbose=False,
                suggest_n=None,
            )
            if rc != 0:
                raise RuntimeError(f"viewshed build failed for {site_slug!r} (exit {rc})")
            computed = True

    return viewshed_raster_record(
        project_slug=project_slug,
        site_slug=site_slug,
        computed=computed,
    )


def get_site_viewshed(
    *,
    project_slug: str,
    site_slug: str,
    ensure: bool = True,
    force: bool = False,
    jobs: int = 1,
) -> dict[str, Any]:
    if ensure:
        return ensure_site_viewshed(
            project_slug=project_slug,
            site_slug=site_slug,
            force=force,
            jobs=jobs,
        )
    if not viewshed_is_cached(project_slug=project_slug, site_slug=site_slug):
        raise FileNotFoundError(f"viewshed not cached for site {site_slug!r}")
    return viewshed_raster_record(project_slug=project_slug, site_slug=site_slug, computed=False)
