"""HttpPool concurrency and spacing."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from peaky_finders.http_pool import HttpPool


def test_http_pool_limits_concurrency_and_spacing() -> None:
    pool = HttpPool(max_concurrent=2, min_interval_s=0.05)
    lock = threading.Lock()
    active = 0
    peak_active = 0
    starts: list[float] = []

    def job() -> None:
        nonlocal active, peak_active
        with lock:
            starts.append(time.monotonic())
            active += 1
            peak_active = max(peak_active, active)
        time.sleep(0.03)
        with lock:
            active -= 1

    def run_job() -> None:
        pool.run(job)

    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = [ex.submit(run_job) for _ in range(6)]
        for fut in as_completed(futures):
            fut.result()

    assert peak_active <= 2
    assert len(starts) == 6
    starts.sort()
    for earlier, later in zip(starts, starts[1:], strict=False):
        assert later - earlier >= 0.04
