"""SSE event hub for ``peaky serve``."""

from __future__ import annotations

import json
import queue
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

SERVE_SSE_KEEPALIVE_S = 15.0


@dataclass(frozen=True)
class ServeEvent:
    """One server-push event on a project SSE channel."""

    name: str
    data: dict[str, Any]


def format_sse_event(name: str, data: dict[str, Any]) -> bytes:
    payload = json.dumps(data, separators=(",", ":"))
    return f"event: {name}\ndata: {payload}\n\n".encode("utf-8")


def format_sse_comment(text: str) -> bytes:
    return f": {text}\n\n".encode("utf-8")


class ServeEventHub:
    """Per-project fan-out for SSE subscribers."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subs: dict[str, list[queue.Queue[ServeEvent | None]]] = {}

    def publish(self, project_slug: str, event: ServeEvent) -> None:
        with self._lock:
            queues = list(self._subs.get(project_slug, ()))
        for sub in queues:
            try:
                sub.put_nowait(event)
            except queue.Full:
                pass

    def subscribe(self, project_slug: str) -> Iterator[bytes]:
        sub: queue.Queue[ServeEvent | None] = queue.Queue(maxsize=256)
        with self._lock:
            self._subs.setdefault(project_slug, []).append(sub)
        try:
            yield format_sse_comment("connected")
            yield format_sse_event("hello", {"project": project_slug})
            while True:
                try:
                    event = sub.get(timeout=SERVE_SSE_KEEPALIVE_S)
                except queue.Empty:
                    yield format_sse_comment("keepalive")
                    continue
                if event is None:
                    break
                yield format_sse_event(event.name, event.data)
        finally:
            with self._lock:
                subs = self._subs.get(project_slug, [])
                if sub in subs:
                    subs.remove(sub)


_hub: ServeEventHub | None = None
_hub_guard = threading.Lock()


def get_serve_event_hub() -> ServeEventHub:
    global _hub
    with _hub_guard:
        if _hub is None:
            _hub = ServeEventHub()
        return _hub


def reset_serve_event_hub_for_tests() -> ServeEventHub:
    """Replace the process hub (tests only)."""
    global _hub
    with _hub_guard:
        _hub = ServeEventHub()
        return _hub
