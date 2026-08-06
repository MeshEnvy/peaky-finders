"""Background goal-seek worker for ``peaky serve``."""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from dataclasses import dataclass

_SEEK_WORKERS = max(1, int(os.environ.get("PEAKY_SERVE_SEEK_WORKERS", "1") or "1"))


@dataclass
class _SeekJob:
    slug: str
    gen: int
    run: Callable[[], None]


class SeekJobQueue:
    """Coalesce pending jobs per project slug; run on a dedicated worker thread."""

    def __init__(self, *, workers: int = 1) -> None:
        self._workers = max(1, int(workers))
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._pending: dict[str, _SeekJob] = {}
        self._started = False
        self._worker_threads: list[threading.Thread] = []

    def _ensure_workers(self) -> None:
        if self._started:
            return
        self._started = True
        for i in range(self._workers):
            thread = threading.Thread(
                target=self._worker_loop,
                name=f"seek-worker-{i}",
                daemon=True,
            )
            thread.start()
            self._worker_threads.append(thread)

    def submit(self, job: _SeekJob) -> None:
        self._ensure_workers()
        with self._cond:
            self._pending[job.slug] = job
            self._cond.notify_all()

    def _take_job(self) -> _SeekJob | None:
        with self._cond:
            while not self._pending:
                self._cond.wait()
            slug = next(iter(self._pending))
            return self._pending.pop(slug)

    def _worker_loop(self) -> None:
        while True:
            job = self._take_job()
            try:
                job.run()
            except BaseException:
                # ``run`` should record failures on the scan registry; never crash the worker.
                pass

    def reset_for_tests(self) -> None:
        with self._cond:
            self._pending.clear()
            self._cond.notify_all()


_queue: SeekJobQueue | None = None
_queue_guard = threading.Lock()


def get_seek_queue() -> SeekJobQueue:
    global _queue
    with _queue_guard:
        if _queue is None:
            _queue = SeekJobQueue(workers=_SEEK_WORKERS)
        return _queue


def reset_seek_queue_for_tests() -> SeekJobQueue:
    global _queue
    with _queue_guard:
        if _queue is not None:
            _queue.reset_for_tests()
        _queue = SeekJobQueue(workers=_SEEK_WORKERS)
        return _queue
