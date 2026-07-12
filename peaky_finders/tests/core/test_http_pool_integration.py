"""HttpPool concurrency behavior."""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from unittest.mock import MagicMock, patch

from peaky_finders.core.http import HttpPool


def test_http_run_respects_pool_concurrency() -> None:
    pool = HttpPool(max_concurrent=1, min_interval_s=0.05)
    lock = threading.Lock()
    active = 0
    peak_active = 0

    def slow_fetch() -> bytes:
        nonlocal active, peak_active
        with lock:
            active += 1
            peak_active = max(peak_active, active)
        time.sleep(0.03)
        with lock:
            active -= 1
        return json.dumps({"ok": True}).encode("utf-8")

    with patch("urllib.request.urlopen") as mock_open:
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"ok": True}).encode("utf-8")
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_open.return_value = mock_resp

        with ThreadPoolExecutor(max_workers=4) as ex:
            futs = [ex.submit(pool.run, slow_fetch) for _ in range(4)]
            for fut in as_completed(futs):
                assert json.loads(fut.result()) == {"ok": True}

    assert peak_active <= 1
