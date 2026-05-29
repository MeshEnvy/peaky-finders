"""Sequential viewshed job queue — one in-flight build per viewshed key."""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
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


class ViewshedManager:
    """Run viewshed builds one at a time; coalesce waiters on the same key."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[tuple[Any, ...], _ViewshedJob] = {}
        self._queue: queue.Queue[tuple[Any, ...]] = queue.Queue()
        self._worker = threading.Thread(target=self._worker_loop, name="viewshed-manager", daemon=True)
        self._worker.start()

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
            self._queue.put(key)
            return job

    def _worker_loop(self) -> None:
        while True:
            key = self._queue.get()
            try:
                with self._lock:
                    job = self._jobs.get(key)
                if job is None:
                    continue
                try:
                    job.fn()
                except BaseException as exc:
                    job.error = exc
                finally:
                    job.event.set()
                    with self._lock:
                        if self._jobs.get(key) is job:
                            del self._jobs[key]
            finally:
                self._queue.task_done()


VIEWSHED_MANAGER = ViewshedManager()
