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


def test_manager_runs_different_keys_in_parallel() -> None:
    mgr = ViewshedManager(max_workers=4)
    started: list[int] = []
    done: list[int] = []
    gate = threading.Barrier(2)

    def make(n: int):
        def work() -> None:
            started.append(n)
            gate.wait(timeout=1.0)
            time.sleep(0.02)
            done.append(n)

        return work

    t1 = threading.Thread(target=lambda: mgr.run(("a", "1"), make(1)))
    t2 = threading.Thread(target=lambda: mgr.run(("a", "2"), make(2)))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert started == [1, 2]
    assert sorted(done) == [1, 2]
