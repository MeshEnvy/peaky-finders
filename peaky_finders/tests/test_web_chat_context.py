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
    build_web_chat_llm_export,
    measure_chat_context,
    summarize_chat_history,
)
from peaky_finders.web.chat_history import build_chat_messages
from peaky_finders.web.chat_tools import WEB_TOOL_SCHEMAS


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


def test_build_web_chat_llm_export_includes_preamble_and_turns() -> None:
    limits = OllamaModelLimits(
        model="test",
        num_ctx=8192,
        model_context_length=None,
        modelfile_num_ctx=None,
        limit_source="ollama_ps",
        request_num_ctx=None,
    )
    llm_turn = [
        {"role": "user", "content": "pin peavine"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c1", "function": {"name": "geocode_place", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "c1", "name": "geocode_place", "content": "{}"},
        {"role": "assistant", "content": "Placed Peavine Mountain."},
    ]
    with patch("peaky_finders.web.chat_context.resolve_web_ai_config") as cfg, patch(
        "peaky_finders.web.chat_context.resolve_ollama_limits",
        return_value=limits,
    ):
        cfg.return_value.endpoint = "http://ollama"
        cfg.return_value.model = "test"
        cfg.return_value.temperature = 0.2
        out = build_web_chat_llm_export(
            project_slug="nevada",
            summary="User asked about peaks earlier.",
            llm_messages=llm_turn,
            message="what next?",
        )

    assert out["endpoint"] == "http://ollama"
    assert out["model"] == "test"
    assert out["temperature"] == 0.2
    assert out["tools"] == WEB_TOOL_SCHEMAS
    assert "query_project_dem_highest" in out["system_prompt"]
    assert out["messages"][0]["role"] == "system"
    assert "summary" in out["messages"][1]["content"].lower()
    assert out["messages"][-1] == {"role": "user", "content": "what next?"}
    assert any(m.get("role") == "tool" for m in out["messages"])


def test_chat_export_endpoint() -> None:
    app = create_app()
    client = TestClient(app)
    fake_llm = {
        "endpoint": "http://ollama",
        "model": "test",
        "temperature": 0.2,
        "num_ctx": 8192,
        "request_num_ctx": None,
        "system_prompt": "system",
        "tools": WEB_TOOL_SCHEMAS,
        "messages": [{"role": "system", "content": "system"}],
    }
    with patch(
        "peaky_finders.web.app.export_chat_context",
        return_value={
            "llm": fake_llm,
            "context": {
                "num_ctx": 8192,
                "used_tokens": 100,
                "available_tokens": 8092,
                "usage_pct": 1.2,
                "status": "ok",
                "warn_pct": WEB_CHAT_WARN_PCT,
                "full_pct": WEB_CHAT_FULL_PCT,
            },
        },
    ):
        res = client.post(
            "/api/chat/export",
            json={"project_slug": "nevada", "llm_messages": [], "message": "hi"},
        )
    assert res.status_code == 200
    body = res.json()
    assert body["llm"]["system_prompt"] == "system"
    assert body["context"]["status"] == "ok"


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
