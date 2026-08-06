"""Goal-seek scan progress registry."""

from __future__ import annotations

from peaky_finders.serve.seek_progress import (
    seek_scan_begin,
    seek_scan_clear,
    seek_scan_finish,
    seek_scan_get,
    seek_scan_poll,
    seek_scan_update,
)


def test_seek_scan_generation_isolated() -> None:
    slug = "test-project"
    gen_a = seek_scan_begin(slug)
    seek_scan_update(slug, gen_a, phase="peaks", done=2, total=10, detail="a")
    assert seek_scan_get(slug) == {
        "phase": "peaks",
        "done": 2,
        "total": 10,
        "detail": "a",
    }

    gen_b = seek_scan_begin(slug)
    assert gen_b > gen_a
    seek_scan_update(slug, gen_a, phase="peaks", done=9, total=10, detail="stale")
    row = seek_scan_get(slug)
    assert row is None or row.get("detail") != "stale"

    seek_scan_update(slug, gen_b, phase="rf", detail="active")
    assert seek_scan_get(slug) == {
        "phase": "rf",
        "done": 0,
        "total": 0,
        "detail": "active",
    }

    seek_scan_clear(slug, gen_a)
    assert seek_scan_get(slug)["detail"] == "active"

    seek_scan_clear(slug, gen_b)
    assert seek_scan_get(slug) is None
    assert seek_scan_poll(slug)["status"] == "idle"


def test_seek_scan_poll_returns_result() -> None:
    slug = "result-project"
    gen = seek_scan_begin(slug)
    seek_scan_update(slug, gen, phase="rf", detail="Checking RF links…")
    seek_scan_finish(slug, gen, status="done", result={"meta": {"n_candidates": 3}})
    poll = seek_scan_poll(slug)
    assert poll["status"] == "done"
    assert poll["gen"] == gen
    assert poll["result"]["meta"]["n_candidates"] == 3


def test_seek_scan_heartbeat_updates_detail() -> None:
    import time

    from peaky_finders.serve.seek_progress import SeekScanHeartbeat

    slug = "hb-project"
    gen = seek_scan_begin(slug)
    with SeekScanHeartbeat(slug, gen, phase="viewshed", detail="SPLAT coverage", interval_s=0.05):
        time.sleep(0.01)
    row = seek_scan_get(slug)
    assert row is not None
    assert row["phase"] == "viewshed"
    assert "elapsed" in row["detail"]
