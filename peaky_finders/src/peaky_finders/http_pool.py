"""Throttle concurrent HTTP work (Bottleneck-style)."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import TypeVar

_T = TypeVar("_T")


class HttpPool:
    """Limit concurrent HTTP jobs and minimum spacing between job starts."""

    def __init__(self, *, max_concurrent: int = 10, min_interval_s: float = 0.12) -> None:
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be >= 1")
        if min_interval_s < 0:
            raise ValueError("min_interval_s must be >= 0")
        self._max_concurrent = max_concurrent
        self._min_interval_s = min_interval_s
        self._sem = threading.BoundedSemaphore(max_concurrent)
        self._schedule_lock = threading.Lock()
        self._next_start_at = 0.0

    @property
    def max_concurrent(self) -> int:
        return self._max_concurrent

    @property
    def min_interval_s(self) -> float:
        return self._min_interval_s

    def _wait_for_slot(self) -> None:
        with self._schedule_lock:
            now = time.monotonic()
            wait = self._next_start_at - now
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
            self._next_start_at = now + self._min_interval_s

    def run(self, fn: Callable[[], _T]) -> _T:
        self._sem.acquire()
        try:
            self._wait_for_slot()
            return fn()
        finally:
            self._sem.release()


CADNSDI_HTTP_POOL = HttpPool(max_concurrent=10, min_interval_s=0.12)
NOMINATIM_HTTP_POOL = HttpPool(max_concurrent=1, min_interval_s=1.0)
ARCGIS_HTTP_POOL = HttpPool(max_concurrent=10, min_interval_s=0.12)
SKADI_HTTP_POOL = HttpPool(max_concurrent=16, min_interval_s=0.0)


def http_run(pool: HttpPool | None, default: HttpPool, fn: Callable[[], _T]) -> _T:
    return (pool or default).run(fn)
