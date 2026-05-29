"""Viewshed manager queue tests."""

from __future__ import annotations

import threading
import time

from peaky_finders.web.viewshed_manager import ViewshedManager


def test_manager_coalesces_same_key() -> None:
    mgr = ViewshedManager()
    calls: list[str] = []
    barrier = threading.Barrier(3)

    def work() -> None:
        calls.append("run")
        time.sleep(0.05)

    def waiter() -> None:
        barrier.wait()
        mgr.run(("demo", "abc"), work)

    threads = [threading.Thread(target=waiter) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert calls == ["run"]


def test_manager_runs_jobs_sequentially() -> None:
    mgr = ViewshedManager()
    order: list[int] = []

    def make(n: int):
        def work() -> None:
            order.append(n)
            time.sleep(0.02)

        return work

    t1 = threading.Thread(target=lambda: mgr.run(("a", "1"), make(1)))
    t2 = threading.Thread(target=lambda: mgr.run(("a", "2"), make(2)))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert order == [1, 2]
