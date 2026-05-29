"""Chat context estimation and summarization tests."""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from peaky_finders.web.app import create_app
from peaky_finders.web.chat_context import (
    WEB_CHAT_FULL_PCT,
    WEB_CHAT_WARN_PCT,
    estimate_chat_context,
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


def test_estimate_chat_context_status_transitions() -> None:
    with patch(
        "peaky_finders.web.chat_context.resolve_web_chat_num_ctx",
        return_value=1000,
    ), patch(
        "peaky_finders.web.chat_context.estimate_tools_tokens",
        return_value=100,
    ), patch(
        "peaky_finders.web.chat_context.WEB_CHAT_REPLY_RESERVE_TOKENS",
        100,
    ):
        ok = estimate_chat_context(
            project_slug="nevada",
            history=[{"role": "user", "content": "x" * 40}],
            message="y",
        )
        assert ok["status"] == "ok"

        long_text = "word " * 5000
        warn = estimate_chat_context(
            project_slug="nevada",
            history=[{"role": "user", "content": long_text}],
            message=long_text,
        )
        assert warn["usage_pct"] >= WEB_CHAT_WARN_PCT
        assert warn["status"] in {"warn", "full"}


def test_summarize_chat_history() -> None:
    fake_resp = {
        "choices": [
            {"message": {"content": "User asked about Peavine Mountain and placed a pin."}},
        ]
    }
    with patch("peaky_finders.web.chat_context.ollama_health_ok", return_value=True), patch(
        "peaky_finders.web.chat_context.resolve_ollama_config",
    ) as cfg, patch(
        "peaky_finders.web.chat_context.chat_completions",
        return_value=fake_resp,
    ), patch(
        "peaky_finders.web.chat_context.resolve_web_chat_num_ctx",
        return_value=32768,
    ):
        cfg.return_value.ollama_base_url = "http://ollama"
        cfg.return_value.ollama_model = "test"
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
        "peaky_finders.web.app.estimate_chat_context",
        return_value={
            "num_ctx": 32768,
            "estimated_tokens": 100,
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


def test_summarize_endpoint_empty_history() -> None:
    app = create_app()
    client = TestClient(app)
    res = client.post("/api/chat/summarize", json={"project_slug": "nevada", "history": []})
    assert res.status_code == 400
