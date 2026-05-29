"""FastAPI smoke tests (no Ollama)."""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

from fastapi.testclient import TestClient

from peaky_finders.web.app import create_app
from peaky_finders.web.stream import OpStream


def test_app_boot_enriches_all_projects() -> None:
    with patch("peaky_finders.web.app.enrich_all_project_sites", return_value=(2, 0, 3)) as enrich:
        with TestClient(create_app()) as client:
            res = client.get("/api/projects")
            assert res.status_code == 200
        deadline = time.monotonic() + 5.0
        while not enrich.called and time.monotonic() < deadline:
            time.sleep(0.05)
        enrich.assert_called_once_with(allow_network_plss=True)


def test_projects_and_stream() -> None:
    app = create_app()
    client = TestClient(app)
    res = client.get("/api/projects")
    assert res.status_code == 200
    assert isinstance(res.json(), list)

    stream = OpStream("test-job")
    stream.emit("job.started", project="demo")
    stream.emit("job.finished", ok=True, exit_code=0)
    stream.close()
    events = list(stream.events())
    assert len(events) == 2
    assert events[0]["op"] == "job.started"


def test_chat_models_endpoint() -> None:
    app = create_app()
    client = TestClient(app)
    with patch(
        "peaky_finders.web.app.list_chat_models",
        return_value={
            "models": ["qwen3.5:9b", "llama3:8b"],
            "default_model": "qwen3.5:9b",
            "endpoint": "http://ollama/v1",
            "reachable": True,
        },
    ):
        res = client.get("/api/chat/models", params={"project_slug": "nevada"})
    assert res.status_code == 200
    payload = res.json()
    assert payload["default_model"] == "qwen3.5:9b"
    assert "llama3:8b" in payload["models"]


def test_chat_stream() -> None:
    app = create_app()
    client = TestClient(app)

    def fake_stream(
        message: str,
        *,
        project_slug: str | None = None,
        history=None,
        summary=None,
        map_pins=None,
        model=None,
        should_cancel=None,
    ):
        assert message == "hello"
        assert history == []
        assert summary is None
        assert map_pins == []
        yield {"op": "chat.delta", "text": "Hel"}
        yield {"op": "chat.delta", "text": "lo!"}
        yield {"op": "chat.done", "model": "test"}

    with patch("peaky_finders.web.app.stream_chat", fake_stream):
        with client.stream("POST", "/api/chat", json={"message": "hello"}) as res:
            assert res.status_code == 200
            body = "".join(res.iter_text())

    assert '"op": "chat.delta"' in body
    assert "Hel" in body
    assert '"op": "chat.done"' in body


def test_chat_stream_does_not_block_other_requests() -> None:
    app = create_app()
    client = TestClient(app)
    gate = threading.Event()

    def slow_stream(
        message: str,
        *,
        project_slug: str | None = None,
        history=None,
        summary=None,
        map_pins=None,
        model=None,
        should_cancel=None,
    ):
        gate.wait(timeout=5.0)
        yield {"op": "chat.done", "model": "test"}

    errors: list[str] = []

    def run_chat() -> None:
        try:
            with client.stream("POST", "/api/chat", json={"message": "hello"}) as res:
                list(res.iter_text())
        except Exception as exc:
            errors.append(str(exc))

    chat_thread = threading.Thread(target=run_chat, daemon=True)
    chat_thread.start()
    time.sleep(0.05)
    try:
        res = client.get("/api/projects")
    finally:
        gate.set()
    chat_thread.join(timeout=5.0)

    assert not errors
    assert res.status_code == 200
    assert isinstance(res.json(), list)
