"""Chat history helpers."""

from __future__ import annotations

from unittest.mock import patch

from peaky_finders.web.chat_agent import stream_web_chat
from peaky_finders.web.chat_history import build_chat_messages, normalize_chat_history


def test_normalize_chat_history_filters_invalid() -> None:
    history = normalize_chat_history(
        [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
            {"role": "tool", "content": "ignored"},
            {"role": "user", "content": "   "},
        ]
    )
    assert history == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]


def test_build_chat_messages_includes_history() -> None:
    messages = build_chat_messages(
        message="follow up",
        history=[
            {"role": "user", "content": "first question"},
            {"role": "assistant", "content": "first answer"},
        ],
        system_prompt="system",
    )
    assert messages == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "first answer"},
        {"role": "user", "content": "follow up"},
    ]


def test_stream_web_chat_passes_history() -> None:
    captured: dict[str, object] = {}

    def fake_turn(**kwargs: object):
        captured["messages"] = kwargs.get("messages")
        yield "delta", "Sure."
        yield "done", {"role": "assistant", "content": "Sure."}

    with patch("peaky_finders.web.chat_agent.ollama_health_ok", return_value=True), patch(
        "peaky_finders.web.chat_agent.resolve_web_ai_config",
    ) as cfg, patch(
        "peaky_finders.web.chat_agent.stream_chat_completions_turn",
        side_effect=lambda **kwargs: fake_turn(**kwargs),
    ):
        cfg.return_value.endpoint = "http://ollama"
        cfg.return_value.model = "test"
        cfg.return_value.temperature = 0.2
        list(
            stream_web_chat(
                "second",
                project_slug="nevada",
                history=[{"role": "user", "content": "first"}, {"role": "assistant", "content": "one"}],
            )
        )

    messages = captured["messages"]
    assert isinstance(messages, list)
    assert messages[1] == {"role": "user", "content": "first"}
    assert messages[2] == {"role": "assistant", "content": "one"}
    assert messages[3] == {"role": "user", "content": "second"}
