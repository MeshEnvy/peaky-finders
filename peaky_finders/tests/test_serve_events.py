"""SSE event hub for ``peaky serve``."""

from __future__ import annotations

import json
import threading
import time

from peaky_finders.serve_events import (
    ServeEvent,
    format_sse_event,
    reset_serve_event_hub_for_tests,
)


def test_format_sse_event() -> None:
    chunk = format_sse_event("viewshed", {"slug": "hub", "status": "ready"})
    text = chunk.decode("utf-8")
    assert text.startswith("event: viewshed\n")
    assert "data: " in text
    payload = json.loads(text.split("data: ", 1)[1].strip())
    assert payload == {"slug": "hub", "status": "ready"}


def test_event_hub_publish_delivers_to_subscriber() -> None:
    hub = reset_serve_event_hub_for_tests()
    done = threading.Event()
    holder: dict[str, bytes] = {}

    def _consume() -> None:
        iterator = hub.subscribe("nevada")
        next(iterator)
        hello = next(iterator)
        assert hello.startswith(b"event: hello")
        holder["chunk"] = next(iterator)
        done.set()

    thread = threading.Thread(target=_consume, daemon=True)
    thread.start()
    time.sleep(0.05)
    hub.publish("nevada", ServeEvent("viewshed", {"slug": "hub", "status": "queued"}))
    assert done.wait(timeout=2.0)
    thread.join(timeout=2.0)
    text = holder["chunk"].decode("utf-8")
    assert "event: viewshed" in text
    assert "queued" in text
