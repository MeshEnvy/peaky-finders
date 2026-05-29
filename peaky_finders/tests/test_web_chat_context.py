"""Chat context measurement and summarization tests."""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from peaky_finders.site_suggestions.providers.mesh_grow_ai.ollama_context import OllamaModelLimits
from peaky_finders.web.app import create_app
from peaky_finders.web.chat_context import (
    WEB_CHAT_FULL_PCT,
    WEB_CHAT_WARN_PCT,
    build_chat_context_payload,
    measure_chat_context,
    summarize_chat_history,
)
from peaky_finders.web.chat_history import build_chat_messages


def test_build_chat_messages_with_summary() -> None:
    messages = build_chat_messages(
        message="next",
        history=[{"role": "user", "content": "hi"}],
        summary="User greeted earlier.",
        system_prompt="system",
    )
    assert messages[0]["role"] == "system"
    assert "summary" in messages[1]["content"].lower()
    assert messages[-1] == {"role": "user", "content": "next"}


def test_build_chat_context_payload_status() -> None:
    full = build_chat_context_payload(num_ctx=1000, used_tokens=950)
    assert full["status"] == "full"
    assert full["available_tokens"] == 50

    warn = build_chat_context_payload(num_ctx=1000, used_tokens=850)
    assert warn["status"] == "warn"

    ok = build_chat_context_payload(num_ctx=1000, used_tokens=100)
    assert ok["status"] == "ok"


def test_measure_chat_context_status_transitions() -> None:
    limits = OllamaModelLimits(
        model="test",
        num_ctx=1000,
        model_context_length=8192,
        modelfile_num_ctx=2048,
        limit_source="ollama_ps",
        request_num_ctx=None,
    )
    with patch("peaky_finders.web.chat_context.ollama_health_ok", return_value=True), patch(
        "peaky_finders.web.chat_context.resolve_ollama_limits",
        return_value=limits,
    ), patch(
        "peaky_finders.web.chat_context.ollama_count_tokens",
        side_effect=[(200, "ollama_tokenize"), (950, "ollama_tokenize")],
    ):
        ok = measure_chat_context(
            project_slug="nevada",
            history=[{"role": "user", "content": "x" * 40}],
            message="y",
        )
        assert ok["status"] == "ok"
        assert ok["used_tokens"] == 200
        assert ok["num_ctx"] == 1000

        warn = measure_chat_context(
            project_slug="nevada",
            history=[{"role": "user", "content": "long"}],
            message="msg",
        )
        assert warn["usage_pct"] >= WEB_CHAT_WARN_PCT
        assert warn["status"] in {"warn", "full"}


def test_summarize_chat_history() -> None:
    fake_resp = {
        "choices": [
            {"message": {"content": "User asked about Peavine Mountain and placed a pin."}},
        ]
    }
    limits = OllamaModelLimits(
        model="test",
        num_ctx=32768,
        model_context_length=None,
        modelfile_num_ctx=None,
        limit_source="ollama_ps",
        request_num_ctx=None,
    )
    with patch("peaky_finders.web.chat_context.ollama_health_ok", return_value=True), patch(
        "peaky_finders.web.chat_context.resolve_web_ai_config",
    ) as cfg, patch(
        "peaky_finders.web.chat_context.chat_completions",
        return_value=fake_resp,
    ), patch(
        "peaky_finders.web.chat_context.resolve_ollama_limits",
        return_value=limits,
    ), patch(
        "peaky_finders.web.chat_context.measure_chat_context",
        return_value={"status": "ok", "used_tokens": 10, "num_ctx": 32768},
    ):
        cfg.return_value.endpoint = "http://ollama"
        cfg.return_value.model = "test"
        out = summarize_chat_history(
            project_slug="nevada",
            history=[{"role": "user", "content": "pin peavine"}],
            summary=None,
        )

    assert "Peavine" in out["summary"]
    assert out["context"]["status"] == "ok"


def test_chat_context_endpoint() -> None:
    app = create_app()
    client = TestClient(app)
    with patch(
        "peaky_finders.web.app.measure_chat_context",
        return_value={
            "num_ctx": 32768,
            "used_tokens": 100,
            "available_tokens": 32668,
            "usage_pct": 0.3,
            "status": "ok",
            "warn_pct": WEB_CHAT_WARN_PCT,
            "full_pct": WEB_CHAT_FULL_PCT,
        },
    ):
        res = client.post(
            "/api/chat/context",
            json={"project_slug": "nevada", "history": [], "summary": None, "message": "hi"},
        )
    assert res.status_code == 200
    assert res.json()["status"] == "ok"
    assert res.json()["used_tokens"] == 100


def test_summarize_endpoint_empty_history() -> None:
    app = create_app()
    client = TestClient(app)
    res = client.post("/api/chat/summarize", json={"project_slug": "nevada", "history": []})
    assert res.status_code == 400
