"""ViewshedEngine coalescing."""

from __future__ import annotations

import threading
from shapely.geometry import box

from peaky_finders.serve.viewshed import ServeViewshedError
from peaky_finders.serve.viewshed_engine import ViewshedEngine, footprint_cache_key


def test_footprint_cache_key_is_stable() -> None:
    key_a = footprint_cache_key("/tmp/proj", "abc123")
    key_b = footprint_cache_key("/tmp/proj", "ABC123")
    assert key_a == key_b


def test_engine_coalesces_identical_requests() -> None:
    engine = ViewshedEngine()
    calls = {"n": 0}
    gate = threading.Event()
    fp = box(0, 0, 1, 1)

    def runner() -> object:
        calls["n"] += 1
        gate.wait(timeout=2.0)
        return fp

    key = "test:deadbeef"
    results: list[object] = []
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            results.append(engine._ensure(key, runner, verbose=False))
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for thread in threads:
        thread.start()

    gate.set()
    for thread in threads:
        thread.join(timeout=5.0)

    assert not errors
    assert calls["n"] == 1
    assert len(results) == 3
    assert all(r is fp for r in results)


def test_engine_propagates_runner_error_to_waiters() -> None:
    engine = ViewshedEngine()
    started = threading.Event()
    gate = threading.Event()

    def runner() -> object:
        started.set()
        gate.wait(timeout=2.0)
        raise RuntimeError("boom")

    key = "test:fail"
    owner_error: BaseException | None = None
    waiter_error: BaseException | None = None

    def owner() -> None:
        nonlocal owner_error
        try:
            engine._ensure(key, runner, verbose=False)
        except BaseException as exc:
            owner_error = exc

    def waiter() -> None:
        nonlocal waiter_error
        started.wait(timeout=2.0)
        try:
            engine._ensure(key, runner, verbose=False)
        except BaseException as exc:
            waiter_error = exc

    owner_thread = threading.Thread(target=owner)
    waiter_thread = threading.Thread(target=waiter)
    owner_thread.start()
    waiter_thread.start()
    gate.set()
    owner_thread.join(timeout=5.0)
    waiter_thread.join(timeout=5.0)

    assert isinstance(owner_error, RuntimeError)
    assert isinstance(waiter_error, ServeViewshedError)
    assert "boom" in str(waiter_error)
