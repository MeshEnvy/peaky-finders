"""Viewshed build queue — parallel across keys, coalesced per key."""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from peaky_finders.sites_job import load_preset
from peaky_finders.viewshed_workspace import resolved_viewshed_workdir_for_coords
from peaky_finders.web.viewshed_rasters import (
    _viewsheds_root_for_preset,
    normalize_point_coords,
    preset_path_for_project,
)


def site_job_key(project_slug: str, site_slug: str) -> tuple[str, str]:
    digest = site_viewshed_digest(project_slug=project_slug, site_slug=site_slug)
    return (project_slug, digest)


def point_job_key(project_slug: str, lat: float, lon: float) -> tuple[str, str, str]:
    lat_n, lon_n = normalize_point_coords(lat, lon)
    digest = point_viewshed_digest(project_slug=project_slug, lat=lat_n, lon=lon_n)
    return (project_slug, "point", digest)


def site_viewshed_digest(*, project_slug: str, site_slug: str) -> str:
    cfg = preset_path_for_project(project_slug)
    preset = load_preset(cfg)
    if site_slug not in preset.sites:
        raise FileNotFoundError(f"unknown site {site_slug!r} in project {project_slug!r}")
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
    return workdir.name


def point_viewshed_digest(*, project_slug: str, lat: float, lon: float) -> str:
    lat_n, lon_n = normalize_point_coords(lat, lon)
    cfg = preset_path_for_project(project_slug)
    preset = load_preset(cfg)
    viewsheds_root = _viewsheds_root_for_preset(cfg)
    if viewsheds_root is None:
        raise FileNotFoundError("viewshed root unavailable")
    workdir = resolved_viewshed_workdir_for_coords(
        preset=preset,
        viewshed_root=viewsheds_root,
        lat=lat_n,
        lon=lon_n,
    )
    return workdir.name


@dataclass
class _ViewshedJob:
    key: tuple[Any, ...]
    fn: Callable[[], None]
    event: threading.Event = field(default_factory=threading.Event)
    error: BaseException | None = None


def _default_max_workers() -> int:
    try:
        n = len(os.sched_getaffinity(0))  # type: ignore[attr-defined]
    except (AttributeError, NotImplementedError):
        n = os.cpu_count() or 4
    return max(1, min(8, n))


class ViewshedManager:
    """Run viewshed builds in parallel; coalesce waiters on the same key."""

    def __init__(self, *, max_workers: int | None = None) -> None:
        mx = max_workers if max_workers is not None else _default_max_workers()
        self._lock = threading.Lock()
        self._jobs: dict[tuple[Any, ...], _ViewshedJob] = {}
        self._executor = ThreadPoolExecutor(
            max_workers=mx,
            thread_name_prefix="viewshed-build",
        )

    def run(self, key: tuple[Any, ...], fn: Callable[[], None]) -> None:
        job = self._enqueue(key, fn)
        job.event.wait()
        if job.error is not None:
            raise job.error

    def _enqueue(self, key: tuple[Any, ...], fn: Callable[[], None]) -> _ViewshedJob:
        with self._lock:
            job = self._jobs.get(key)
            if job is not None:
                return job
            job = _ViewshedJob(key=key, fn=fn)
            self._jobs[key] = job
            self._executor.submit(self._run_job, job)
            return job

    def _run_job(self, job: _ViewshedJob) -> None:
        try:
            job.fn()
        except BaseException as exc:
            job.error = exc
        finally:
            job.event.set()
            with self._lock:
                if self._jobs.get(job.key) is job:
                    del self._jobs[job.key]


VIEWSHED_MANAGER = ViewshedManager()
