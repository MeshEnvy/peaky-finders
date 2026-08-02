"""Priority work queue for splatter coverage jobs."""

from __future__ import annotations

import heapq
import threading
from collections.abc import Callable, Iterable
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Any, TypeVar

from peaky_finders.serve.viewshed import resolve_serve_coverage_max_concurrent

_T = TypeVar("_T")


@dataclass(order=True)
class _HeapEntry:
    priority: int
    seq: int
    key: str = field(compare=False)
    cancelled: bool = field(default=False, compare=False)


@dataclass
class _JobState:
    key: str
    fn: Callable[[], Any]
    future: Future[Any]
    priority: int
    seq: int
    cancelled: bool = False


class CoverageWorkQueue:
    """Heap-backed queue with dedupe, bump, and fixed worker pool."""

    def __init__(self, *, workers: int | None = None) -> None:
        self._workers = max(1, workers or resolve_serve_coverage_max_concurrent())
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._seq = 0
        self._heap: list[_HeapEntry] = []
        self._jobs: dict[str, _JobState] = {}
        self._inflight: set[str] = set()
        self._started = False
        self._worker_threads: list[threading.Thread] = []

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def _ensure_workers(self) -> None:
        if self._started:
            return
        self._started = True
        for i in range(self._workers):
            thread = threading.Thread(
                target=self._worker_loop,
                name=f"coverage-queue-{i}",
                daemon=True,
            )
            thread.start()
            self._worker_threads.append(thread)

    def _push_job(self, state: _JobState) -> None:
        heapq.heappush(
            self._heap,
            _HeapEntry(state.priority, state.seq, state.key),
        )

    def _pop_job(self) -> _JobState | None:
        while self._heap:
            entry = heapq.heappop(self._heap)
            state = self._jobs.get(entry.key)
            if state is None or state.cancelled or state.seq != entry.seq:
                continue
            return state
        return None

    def _worker_loop(self) -> None:
        while True:
            with self._cond:
                while True:
                    state = self._pop_job()
                    if state is not None:
                        break
                    self._cond.wait()

            key = state.key
            with self._cond:
                self._inflight.add(key)

            try:
                if not state.cancelled and not state.future.cancelled():
                    result = state.fn()
                    if not state.future.done():
                        state.future.set_result(result)
            except BaseException as exc:
                if not state.future.done():
                    state.future.set_exception(exc)
            finally:
                with self._cond:
                    self._inflight.discard(key)
                    self._jobs.pop(key, None)
                    self._cond.notify_all()

    def submit(
        self,
        key: str,
        fn: Callable[[], _T],
        *,
        priority: int = 100,
    ) -> Future[_T]:
        """Enqueue *fn*; return existing future when *key* is already queued or running."""
        self._ensure_workers()
        with self._cond:
            existing = self._jobs.get(key)
            if existing is not None and not existing.cancelled:
                return existing.future
            if key in self._inflight:
                # Rare race: job popped but not yet in _inflight from worker view.
                # Wait briefly and retry via ensure_submitted pattern at call site.
                future: Future[_T] = Future()
                seq = self._next_seq()
                state = _JobState(key=key, fn=fn, future=future, priority=priority, seq=seq)
                self._jobs[key] = state
                self._push_job(state)
                self._cond.notify()
                return future

            future = Future()
            seq = self._next_seq()
            state = _JobState(key=key, fn=fn, future=future, priority=priority, seq=seq)
            self._jobs[key] = state
            self._push_job(state)
            self._cond.notify()
            return future

    def ensure_submitted(
        self,
        key: str,
        fn: Callable[[], _T],
        *,
        priority: int = 100,
    ) -> Future[_T]:
        """Submit only when *key* is not queued or inflight."""
        self._ensure_workers()
        with self._cond:
            if key in self._inflight or key in self._jobs:
                existing = self._jobs.get(key)
                if existing is not None:
                    return existing.future
                future: Future[_T] = Future()
                return future
        return self.submit(key, fn, priority=priority)

    def bump(self, key: str, priority: int) -> bool:
        """Raise priority of a queued job (lower number = sooner)."""
        with self._cond:
            state = self._jobs.get(key)
            if state is None or state.cancelled:
                return False
            if priority >= state.priority:
                return False
            state.priority = priority
            state.seq = self._next_seq()
            self._push_job(state)
            self._cond.notify()
            return True

    def bump_many(self, keys: Iterable[str], priority: int) -> int:
        count = 0
        for key in keys:
            if self.bump(key, priority):
                count += 1
        return count

    def cancel(self, key: str) -> bool:
        with self._cond:
            state = self._jobs.get(key)
            if state is None:
                return False
            state.cancelled = True
            if not state.future.done():
                state.future.cancel()
            self._jobs.pop(key, None)
            self._cond.notify_all()
            return True

    def is_active(self, key: str) -> bool:
        with self._cond:
            return key in self._jobs or key in self._inflight

    def reset_for_tests(self) -> None:
        with self._cond:
            for state in self._jobs.values():
                state.cancelled = True
                if not state.future.done():
                    state.future.cancel()
            self._jobs.clear()
            self._heap.clear()
            self._inflight.clear()
            self._seq = 0


_queue: CoverageWorkQueue | None = None
_queue_guard = threading.Lock()


def get_coverage_queue() -> CoverageWorkQueue:
    global _queue
    with _queue_guard:
        if _queue is None:
            _queue = CoverageWorkQueue()
        return _queue


def reset_coverage_queue_for_tests() -> CoverageWorkQueue:
    global _queue
    with _queue_guard:
        if _queue is not None:
            _queue.reset_for_tests()
        _queue = CoverageWorkQueue()
        return _queue
