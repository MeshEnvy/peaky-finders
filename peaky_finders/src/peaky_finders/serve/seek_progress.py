"""In-memory goal-seek scan progress for ``peaky serve`` polling."""

from __future__ import annotations

import threading
import time
from typing import Any

_lock = threading.Lock()
_state: dict[str, dict[str, Any]] = {}
_outcomes: dict[str, dict[str, Any]] = {}
_active_gen: dict[str, int] = {}


def seek_scan_begin(slug: str) -> int:
    """Start a new scan generation; superseded scans must not clear progress."""
    with _lock:
        gen = _active_gen.get(slug, 0) + 1
        _active_gen[slug] = gen
        _state.pop(slug, None)
        _outcomes.pop(slug, None)
        return gen


def seek_scan_update(
    slug: str,
    gen: int,
    *,
    phase: str,
    done: int = 0,
    total: int = 0,
    detail: str = "",
) -> None:
    with _lock:
        if _active_gen.get(slug) != gen:
            return
        _state[slug] = {
            "phase": phase,
            "done": int(done),
            "total": int(total),
            "detail": str(detail),
        }


def seek_scan_get(slug: str) -> dict[str, Any] | None:
    with _lock:
        row = _state.get(slug)
        return dict(row) if row else None


def seek_scan_poll(slug: str) -> dict[str, Any]:
    """Return progress, terminal status, and result payload for client polling."""
    with _lock:
        gen = _active_gen.get(slug)
        progress = dict(_state[slug]) if slug in _state else None
        outcome = _outcomes.get(slug)
        if outcome is not None:
            payload = dict(outcome)
            if progress is not None:
                payload["progress"] = progress
            elif "progress" not in payload:
                payload["progress"] = None
            return payload
        if gen is not None:
            return {"gen": gen, "status": "pending", "progress": progress}
        return {"gen": None, "status": "idle", "progress": None}


def seek_scan_finish(
    slug: str,
    gen: int,
    *,
    status: str,
    result: dict[str, Any] | None = None,
    error: str | None = None,
    error_status: int | None = None,
) -> None:
    with _lock:
        if _active_gen.get(slug) != gen:
            return
        _state.pop(slug, None)
        _outcomes[slug] = {
            "gen": gen,
            "status": status,
            "progress": None,
            "result": result,
            "error": error,
            "error_status": error_status,
        }


def seek_scan_active(slug: str, gen: int) -> bool:
    """Return whether ``gen`` is still the active scan for ``slug``."""
    with _lock:
        return _active_gen.get(slug) == gen


class SeekScanHeartbeat:
    """Refresh scan detail on an interval while a long-running phase blocks."""

    def __init__(
        self,
        slug: str,
        gen: int,
        *,
        phase: str,
        detail: str,
        interval_s: float = 2.0,
    ) -> None:
        self.slug = slug
        self.gen = gen
        self.phase = phase
        self._detail = detail
        self._interval_s = max(0.5, float(interval_s))
        self._detail_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def set_detail(self, detail: str) -> None:
        with self._detail_lock:
            self._detail = str(detail)
        seek_scan_update(self.slug, self.gen, phase=self.phase, detail=self._detail)

    def __enter__(self) -> SeekScanHeartbeat:
        seek_scan_update(self.slug, self.gen, phase=self.phase, detail=self._detail)
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="seek-scan-heartbeat", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        return False

    def _loop(self) -> None:
        t0 = time.monotonic()
        while True:
            if self._stop.is_set() or not seek_scan_active(self.slug, self.gen):
                return
            elapsed = int(time.monotonic() - t0)
            with self._detail_lock:
                base = self._detail
            seek_scan_update(
                self.slug,
                self.gen,
                phase=self.phase,
                detail=f"{base} — {elapsed}s elapsed",
            )
            if self._stop.wait(self._interval_s):
                return


def seek_scan_clear(slug: str, gen: int) -> None:
    with _lock:
        if _active_gen.get(slug) != gen:
            return
        _state.pop(slug, None)
        _outcomes.pop(slug, None)
        if _active_gen.get(slug) == gen:
            _active_gen.pop(slug, None)


def reset_seek_scan_for_tests() -> None:
    """Clear in-memory scan progress (tests only)."""
    with _lock:
        _state.clear()
        _outcomes.clear()
        _active_gen.clear()
