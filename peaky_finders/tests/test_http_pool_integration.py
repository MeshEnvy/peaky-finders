"""HttpPool integration at geocode, ArcGIS, and Skadi call sites."""

from __future__ import annotations

import io
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from unittest.mock import MagicMock, patch

from peaky_finders.http_pool import HttpPool
from peaky_finders.skadi_dem import fetch_skadi_hgt_gzip_bytes
from peaky_finders.web.geocode import geocode_place
from peaky_finders.webmap_arcgis import fetch_json


def test_geocode_place_respects_http_pool_concurrency() -> None:
    pool = HttpPool(max_concurrent=1, min_interval_s=0.05)
    lock = threading.Lock()
    active = 0
    peak_active = 0
    payload = [
        {
            "display_name": "Test Peak",
            "lat": "39.0",
            "lon": "-119.0",
            "boundingbox": ["38.9", "39.1", "-119.1", "-118.9"],
        }
    ]

    def slow_get(*_args, **_kwargs):
        nonlocal active, peak_active
        with lock:
            active += 1
            peak_active = max(peak_active, active)
        time.sleep(0.03)
        with lock:
            active -= 1
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = payload
        return resp

    with patch("peaky_finders.web.geocode.httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        client.get.side_effect = slow_get

        with ThreadPoolExecutor(max_workers=4) as ex:
            futs = [
                ex.submit(geocode_place, f"peak-{i}", http_pool=pool)
                for i in range(4)
            ]
            for fut in as_completed(futs):
                assert len(fut.result()) == 1

    assert peak_active <= 1


def test_fetch_json_respects_http_pool_concurrency() -> None:
    pool = HttpPool(max_concurrent=1, min_interval_s=0.05)
    lock = threading.Lock()
    active = 0
    peak_active = 0

    def slow_urlopen(*_args, **_kwargs):
        nonlocal active, peak_active
        with lock:
            active += 1
            peak_active = max(peak_active, active)
        time.sleep(0.03)
        with lock:
            active -= 1
        body = json.dumps({"ok": True}).encode("utf-8")
        resp = MagicMock()
        resp.read.return_value = body
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    with patch("peaky_finders.webmap_arcgis.urllib.request.urlopen", side_effect=slow_urlopen):
        with ThreadPoolExecutor(max_workers=4) as ex:
            futs = [
                ex.submit(fetch_json, f"https://example.test/{i}", timeout_s=5.0, http_pool=pool)
                for i in range(4)
            ]
            for fut in as_completed(futs):
                assert fut.result() == {"ok": True}

    assert peak_active <= 1


def test_fetch_skadi_hgt_gzip_bytes_respects_http_pool_concurrency() -> None:
    pool = HttpPool(max_concurrent=1, min_interval_s=0.05)
    lock = threading.Lock()
    active = 0
    peak_active = 0

    def slow_get_object(*_args, **_kwargs):
        nonlocal active, peak_active
        with lock:
            active += 1
            peak_active = max(peak_active, active)
        time.sleep(0.03)
        with lock:
            active -= 1
        return {"Body": io.BytesIO(b"gz")}

    s3 = MagicMock()
    s3.get_object.side_effect = slow_get_object

    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = [
            ex.submit(
                fetch_skadi_hgt_gzip_bytes,
                s3,
                f"N36W11{i}.hgt.gz",
                http_pool=pool,
            )
            for i in range(4)
        ]
        for fut in as_completed(futs):
            assert fut.result() == b"gz"

    assert peak_active <= 1
