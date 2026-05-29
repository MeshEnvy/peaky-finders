"""Build SSE stream: imperative UI ops for web GUI and replay."""

from __future__ import annotations

import contextvars
import json
import queue
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

_stream_ctx: contextvars.ContextVar[BuildStream | None] = contextvars.ContextVar(
    "build_stream",
    default=None,
)


class BuildStream:
    """Thread-safe per-job event queue."""

    def __init__(self, job_id: str, *, jsonl_path: Path | None = None) -> None:
        self.job_id = job_id
        self._queue: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._seq = 0
        self._lock = threading.Lock()
        self._jsonl_path = jsonl_path
        self._closed = False

    def emit(self, op: str, **payload: Any) -> None:
        if self._closed:
            return
        with self._lock:
            self._seq += 1
            seq = self._seq
        msg: dict[str, Any] = {
            "v": 1,
            "seq": seq,
            "ts": datetime.now(timezone.utc).isoformat(),
            "op": op,
            "job_id": self.job_id,
            **payload,
        }
        if self._jsonl_path is not None:
            self._jsonl_path.parent.mkdir(parents=True, exist_ok=True)
            with self._jsonl_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(msg, default=str) + "\n")
        self._queue.put(msg)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._queue.put(None)

    def events(self) -> Iterator[dict[str, Any]]:
        while True:
            item = self._queue.get()
            if item is None:
                break
            yield item


def bind_stream(stream: BuildStream | None) -> contextvars.Token:
    return _stream_ctx.set(stream)


def current_stream() -> BuildStream | None:
    return _stream_ctx.get()


def emit_op(op: str, **payload: Any) -> None:
    stream = current_stream()
    if stream is not None:
        stream.emit(op, **payload)


def stream_emit_fn():
    """Return ``emit(op, **payload)`` bound to the active stream (or no-op)."""

    def _emit(op: str, **payload: Any) -> None:
        emit_op(op, **payload)

    return _emit
