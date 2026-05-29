"""Per-project SSE event bus (maps builds, catalog refresh, future ops)."""

from __future__ import annotations

import json
import queue
import threading
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from typing import Any

_lock = threading.Lock()
_hubs: dict[str, ProjectEventHub] = {}


class ProjectEventHub:
    """Fan-out event bus for one project slug (multiple SSE clients)."""

    def __init__(self, slug: str) -> None:
        self.slug = slug
        self._seq = 0
        self._lock = threading.Lock()
        self._queues: list[queue.Queue[dict[str, Any] | None]] = []

    def publish(self, op: str, **payload: Any) -> None:
        with self._lock:
            self._seq += 1
            msg: dict[str, Any] = {
                "v": 1,
                "seq": self._seq,
                "ts": datetime.now(timezone.utc).isoformat(),
                "op": op,
                "project": self.slug,
                **payload,
            }
            for q in self._queues:
                try:
                    q.put_nowait(msg)
                except queue.Full:
                    pass

    def subscribe(self, *, should_stop: Callable[[], bool] | None = None) -> Iterator[dict[str, Any]]:
        q: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=256)
        with self._lock:
            self._queues.append(q)
        try:
            while should_stop is None or not should_stop():
                try:
                    item = q.get(timeout=0.5)
                except queue.Empty:
                    continue
                if item is None:
                    break
                yield item
        finally:
            with self._lock:
                if q in self._queues:
                    self._queues.remove(q)

    def close_subscribers(self) -> None:
        with self._lock:
            for q in self._queues:
                q.put(None)
            self._queues.clear()


def project_event_hub(slug: str) -> ProjectEventHub:
    with _lock:
        hub = _hubs.get(slug)
        if hub is None:
            hub = ProjectEventHub(slug)
            _hubs[slug] = hub
        return hub


def publish_project_op(slug: str, op: str, **payload: Any) -> None:
    if not slug:
        return
    project_event_hub(slug).publish(op, **payload)


def publish_build_status(slug: str, *, clips: dict[str, Any], mesh: dict[str, Any]) -> None:
    publish_project_op(slug, "maps.build.status", clips=clips, mesh=mesh)


def publish_layer_phase(slug: str, *, layer_id: str, phase: str) -> None:
    publish_project_op(slug, "maps.layer.phase", layer_id=layer_id, phase=phase)


def publish_catalog_refresh(slug: str, *, reason: str = "") -> None:
    publish_project_op(slug, "maps.catalog.refresh", reason=reason)


def sse_encode(msg: dict[str, Any]) -> str:
    return f"data: {json.dumps(msg, default=str)}\n\n"
