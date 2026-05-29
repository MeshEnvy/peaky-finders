"""Normalize prior turns for the web chat API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8000)


def normalize_chat_history(history: list[ChatTurn] | list[dict[str, Any]] | None) -> list[dict[str, str]]:
    """Keep user/assistant turns with non-empty content (no truncation)."""
    if not history:
        return []

    out: list[dict[str, str]] = []
    for item in history:
        if isinstance(item, ChatTurn):
            role = item.role
            content = item.content.strip()
        elif isinstance(item, dict):
            role = str(item.get("role") or "")
            raw = item.get("content")
            content = str(raw).strip() if raw is not None else ""
        else:
            continue
        if role not in {"user", "assistant"} or not content:
            continue
        out.append({"role": role, "content": content})
    return out


def build_chat_messages(
    *,
    message: str,
    history: list[ChatTurn] | list[dict[str, Any]] | None,
    system_prompt: str,
    summary: str | None = None,
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    summary_text = str(summary or "").strip()
    if summary_text:
        messages.append(
            {
                "role": "user",
                "content": f"Conversation summary from earlier in this session:\n{summary_text}",
            }
        )
        messages.append(
            {
                "role": "assistant",
                "content": "Understood — I'll treat that summary as our prior context.",
            }
        )
    messages.extend(normalize_chat_history(history))
    messages.append({"role": "user", "content": message.strip()})
    return messages
