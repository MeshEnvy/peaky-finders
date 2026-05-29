"""FastAPI smoke tests (no Ollama / build)."""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from peaky_finders.web.app import create_app
from peaky_finders.web.stream import BuildStream


def test_projects_and_stream() -> None:
    app = create_app()
    client = TestClient(app)
    res = client.get("/api/projects")
    assert res.status_code == 200
    assert isinstance(res.json(), list)

    stream = BuildStream("test-job")
    stream.emit("build.started", project="demo")
    stream.emit("build.finished", ok=True, exit_code=0)
    stream.close()
    events = list(stream.events())
    assert len(events) == 2
    assert events[0]["op"] == "build.started"


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
