"""Coverage work queue ordering and bump."""

from __future__ import annotations

import threading

from peaky_finders.serve.coverage_queue import reset_coverage_queue_for_tests


def test_submit_dedupes_by_key() -> None:
    queue = reset_coverage_queue_for_tests()
    started = threading.Event()
    proceed = threading.Event()
    calls: list[str] = []

    def _fn_a() -> str:
        calls.append("a")
        started.set()
        assert proceed.wait(timeout=2.0)
        return "a"

    f1 = queue.submit("key", _fn_a, priority=100)
    f2 = queue.submit("key", _fn_a, priority=100)
    assert f1 is f2

    proceed.set()
    assert f1.result(timeout=2.0) == "a"
    assert calls == ["a"]


def test_bump_reorders_before_lower_priority() -> None:
    from peaky_finders.serve.coverage_queue import CoverageWorkQueue

    queue = CoverageWorkQueue(workers=1)
    order: list[str] = []
    started = threading.Event()
    release = threading.Event()

    def _slow() -> str:
        started.set()
        assert release.wait(timeout=2.0)
        order.append("slow")
        return "slow"

    def _fast() -> str:
        order.append("fast")
        return "fast"

    def _medium() -> str:
        order.append("medium")
        return "medium"

    slow_future = queue.submit("slow", _slow, priority=100)
    assert started.wait(timeout=2.0)
    medium_future = queue.submit("medium", _medium, priority=100)
    fast_future = queue.submit("fast", _fast, priority=100)
    assert queue.bump("fast", 0) is True
    release.set()
    slow_future.result(timeout=2.0)
    fast_future.result(timeout=2.0)
    medium_future.result(timeout=2.0)
    assert order == ["slow", "fast", "medium"]


def test_bump_many_counts() -> None:
    queue = reset_coverage_queue_for_tests()
    for slug in ("a", "b", "c"):
        queue.submit(slug, lambda s=slug: s, priority=100)
    bumped = queue.bump_many(["a", "b", "missing"], 10)
    assert bumped == 2
