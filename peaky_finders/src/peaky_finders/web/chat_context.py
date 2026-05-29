"""Web chat context window estimation and summarization."""

from __future__ import annotations

import json
from typing import Any, Literal

from peaky_finders.site_suggestions.providers.mesh_grow_ai.ollama_client import (
    OllamaError,
    chat_completions,
    ollama_health_ok,
)
from peaky_finders.web.chat import resolve_ollama_config, resolve_web_chat_num_ctx
from peaky_finders.web.chat_history import build_chat_messages, normalize_chat_history
from peaky_finders.web.chat_tools import WEB_TOOL_SCHEMAS, normalize_map_pins, web_chat_system_prompt

ContextStatus = Literal["ok", "warn", "full"]

WEB_CHAT_WARN_PCT = 80.0
WEB_CHAT_FULL_PCT = 92.0
WEB_CHAT_TOOLS_OVERHEAD_TOKENS = 2800
WEB_CHAT_REPLY_RESERVE_TOKENS = 2048

SUMMARIZE_SYSTEM_PROMPT = (
    "You compress chat transcripts for a mesh radio site planning assistant. "
    "Produce a concise summary the assistant can use as memory in later turns. "
    "Preserve: place names, coordinates, map pins/viewshed actions, user preferences, "
    "open questions, and decisions. Use short paragraphs or bullets. No preamble."
)


def estimate_text_tokens(text: str) -> int:
    stripped = str(text or "").strip()
    if not stripped:
        return 0
    return max(1, len(stripped) // 4)


def estimate_message_tokens(message: dict[str, Any]) -> int:
    tokens = 4
    content = message.get("content")
    if content:
        tokens += estimate_text_tokens(str(content))
    tool_calls = message.get("tool_calls")
    if tool_calls:
        tokens += estimate_text_tokens(json.dumps(tool_calls, default=str))
    name = message.get("name")
    if name:
        tokens += estimate_text_tokens(str(name))
    return tokens


def estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    return sum(estimate_message_tokens(m) for m in messages)


def estimate_tools_tokens() -> int:
    return WEB_CHAT_TOOLS_OVERHEAD_TOKENS + estimate_text_tokens(json.dumps(WEB_TOOL_SCHEMAS, default=str))


def build_context_messages(
    *,
    message: str,
    history: list | None,
    summary: str | None,
    system_prompt: str,
    map_pins: list | None = None,
) -> list[dict[str, Any]]:
    return build_chat_messages(
        message=message,
        history=history,
        summary=summary,
        system_prompt=system_prompt,
    )


def estimate_chat_context(
    *,
    project_slug: str | None,
    history: list | None,
    summary: str | None = None,
    message: str = "",
    map_pins: list | None = None,
) -> dict[str, Any]:
    num_ctx = resolve_web_chat_num_ctx(project_slug)
    system_prompt = web_chat_system_prompt(project_slug=project_slug, map_pins=normalize_map_pins(map_pins))
    messages = build_context_messages(
        message=message or "(next message)",
        history=history,
        summary=summary,
        system_prompt=system_prompt,
        map_pins=map_pins,
    )
    prompt_tokens = estimate_messages_tokens(messages)
    tool_tokens = estimate_tools_tokens()
    reserve_tokens = WEB_CHAT_REPLY_RESERVE_TOKENS
    estimated_tokens = prompt_tokens + tool_tokens + reserve_tokens
    usage_pct = min(100.0, (estimated_tokens / max(num_ctx, 1)) * 100.0)

    if usage_pct >= WEB_CHAT_FULL_PCT:
        status: ContextStatus = "full"
    elif usage_pct >= WEB_CHAT_WARN_PCT:
        status = "warn"
    else:
        status = "ok"

    return {
        "num_ctx": num_ctx,
        "estimated_tokens": estimated_tokens,
        "prompt_tokens": prompt_tokens,
        "tool_tokens": tool_tokens,
        "reserve_tokens": reserve_tokens,
        "usage_pct": round(usage_pct, 1),
        "status": status,
        "warn_pct": WEB_CHAT_WARN_PCT,
        "full_pct": WEB_CHAT_FULL_PCT,
    }


def _transcript_for_summary(*, history: list[dict[str, str]], summary: str | None) -> str:
    parts: list[str] = []
    if summary and summary.strip():
        parts.append(f"PRIOR SUMMARY:\n{summary.strip()}")
    for turn in history:
        role = turn["role"].upper()
        parts.append(f"{role}:\n{turn['content']}")
    return "\n\n---\n\n".join(parts)


def summarize_chat_history(
    *,
    project_slug: str | None,
    history: list | None,
    summary: str | None = None,
) -> dict[str, Any]:
    turns = normalize_chat_history(history)
    if not turns and not (summary and summary.strip()):
        raise ValueError("nothing to summarize")

    ai = resolve_ollama_config(project_slug)
    if not ollama_health_ok(ai.ollama_base_url):
        raise OllamaError(f"Ollama not reachable at {ai.ollama_base_url!r}")

    transcript = _transcript_for_summary(history=turns, summary=summary)
    resp = chat_completions(
        base_url=ai.ollama_base_url,
        model=ai.ollama_model,
        messages=[
            {"role": "system", "content": SUMMARIZE_SYSTEM_PROMPT},
            {"role": "user", "content": transcript},
        ],
        tools=None,
        temperature=0.1,
        timeout_s=180.0,
    )
    choices = resp.get("choices") or []
    if not choices:
        raise OllamaError("Ollama returned empty summary")
    content = (choices[0].get("message") or {}).get("content")
    text = str(content).strip() if content else ""
    if not text:
        raise OllamaError("Ollama returned empty summary")

    ctx = estimate_chat_context(project_slug=project_slug, history=[], summary=text, message="")
    return {
        "summary": text,
        "model": ai.ollama_model,
        "context": ctx,
    }
