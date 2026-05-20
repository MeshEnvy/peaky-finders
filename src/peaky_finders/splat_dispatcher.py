"""Bounded queue of SPLAT jobs with background worker threads."""

from __future__ import annotations

import queue
import threading
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path

from peaky_finders.splat_pipeline import SplatSiteResult, run_splat_site_job
from peaky_finders.sites_job import BundleKmlLayerStyle, CoverageProvider


@dataclass(frozen=True)
class SplatSiteJob:
    site_name: str
    image: str
    provider: CoverageProvider
    data_dir: Path
    tile_cache_dir: Path
    force_splat: bool
    coverage_kml_style: BundleKmlLayerStyle
    coverage_verbose: bool = False


class SplatDispatcher:
    """Enqueue ``SplatSiteJob`` instances; workers run ``run_splat_site_job``."""

    def __init__(self, *, max_workers: int = 8, queue_maxsize: int = 0) -> None:
        self._max_workers = max(1, max_workers)
        self._q: queue.Queue[tuple[Future[SplatSiteResult], SplatSiteJob] | None] = queue.Queue(
            maxsize=max(0, queue_maxsize)
        )
        self._threads: list[threading.Thread] = []
        for _ in range(self._max_workers):
            t = threading.Thread(target=self._worker_loop, daemon=True)
            t.start()
            self._threads.append(t)

    def submit(self, job: SplatSiteJob) -> Future[SplatSiteResult]:
        fut: Future[SplatSiteResult] = Future()
        self._q.put((fut, job))
        return fut

    def close(self) -> None:
        """Unblock workers after all submitted jobs have finished (call after draining futures)."""
        for _ in range(self._max_workers):
            self._q.put(None)
        for t in self._threads:
            t.join()

    def _worker_loop(self) -> None:
        while True:
            item = self._q.get()
            try:
                if item is None:
                    return
                fut, job = item
                try:
                    result = run_splat_site_job(
                        site_name=job.site_name,
                        image=job.image,
                        provider=job.provider,
                        data_dir=job.data_dir,
                        tile_cache_dir=job.tile_cache_dir,
                        force_splat=job.force_splat,
                        coverage_kml_style=job.coverage_kml_style,
                        coverage_verbose=job.coverage_verbose,
                    )
                    fut.set_result(result)
                except Exception as exc:
                    fut.set_exception(exc)
            finally:
                self._q.task_done()
