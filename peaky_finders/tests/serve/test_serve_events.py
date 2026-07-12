"""SSE event hub for ``peaky serve``."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from peaky_finders.core.project.scaffold import scaffold_project
from peaky_finders.serve.app import make_serve_wsgi_app
from peaky_finders.serve.events import (
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


def test_events_route_wsgi_streams_hello(tmp_path: Path) -> None:
    reset_serve_event_hub_for_tests()
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    scaffold_project("nevada", parent=projects_dir)
    app = make_serve_wsgi_app(projects_dir, verbose=False, request_log=False)

    status_holder: list[str] = []
    headers_holder: list[list[tuple[str, str]]] = []

    def start_response(status: str, headers: list[tuple[str, str]], exc_info=None):
        status_holder.append(status)
        headers_holder.append(headers)
        return lambda data: None

    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/api/p/nevada/events",
        "QUERY_STRING": "",
        "CONTENT_LENGTH": "0",
        "wsgi.input": None,
        "REMOTE_ADDR": "127.0.0.1",
    }
    body_iter = app(environ, start_response)
    assert status_holder == ["200 OK"]
    header_names = {name.lower() for name, _ in headers_holder[0]}
    assert "connection" not in header_names
    assert ("content-type", "text/event-stream; charset=utf-8") in [
        (k.lower(), v) for k, v in headers_holder[0]
    ]
    next(body_iter)
    hello = next(body_iter)
    assert hello.startswith(b"event: hello")
    assert b"nevada" in hello
    body_iter.close()
