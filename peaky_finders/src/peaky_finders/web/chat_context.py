"""Web chat context window measurement and summarization."""

from __future__ import annotations

import json
from typing import Any, Literal

from peaky_finders.site_suggestions.providers.mesh_grow_ai.ollama_client import (
    OllamaError,
    chat_completions,
    ollama_health_ok,
)
from peaky_finders.site_suggestions.providers.mesh_grow_ai.ollama_context import (
    ollama_count_tokens,
    usage_prompt_tokens,
)
from peaky_finders.web.chat import resolve_ollama_limits, resolve_web_ai_config
from peaky_finders.web.chat_history import build_chat_messages, normalize_chat_history
from peaky_finders.web.chat_tools import WEB_TOOL_SCHEMAS, normalize_map_pins, web_chat_system_prompt

ContextStatus = Literal["ok", "warn", "full"]

WEB_CHAT_WARN_PCT = 80.0
WEB_CHAT_FULL_PCT = 92.0


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


def _context_status(usage_pct: float) -> ContextStatus:
    if usage_pct >= WEB_CHAT_FULL_PCT:
        return "full"
    if usage_pct >= WEB_CHAT_WARN_PCT:
        return "warn"
    return "ok"


def build_chat_context_payload(
    *,
    num_ctx: int,
    used_tokens: int,
    model_context_length: int | None = None,
    modelfile_num_ctx: int | None = None,
    token_count_source: str = "ollama_tokenize",
    limit_source: str = "ollama_show",
    measured_prompt_tokens: int | None = None,
) -> dict[str, Any]:
    available = max(0, num_ctx - used_tokens)
    usage_pct = min(100.0, (used_tokens / max(num_ctx, 1)) * 100.0)
    payload: dict[str, Any] = {
        "num_ctx": num_ctx,
        "used_tokens": used_tokens,
        "available_tokens": available,
        "usage_pct": round(usage_pct, 1),
        "status": _context_status(usage_pct),
        "warn_pct": WEB_CHAT_WARN_PCT,
        "full_pct": WEB_CHAT_FULL_PCT,
        "token_count_source": token_count_source,
        "limit_source": limit_source,
        "model_context_length": model_context_length,
        "modelfile_num_ctx": modelfile_num_ctx,
    }
    if measured_prompt_tokens is not None:
        payload["measured_prompt_tokens"] = measured_prompt_tokens
    return payload


def measure_chat_context(
    *,
    project_slug: str | None,
    history: list | None = None,
    summary: str | None = None,
    message: str = "",
    map_pins: list | None = None,
    messages: list[dict[str, Any]] | None = None,
    measured_prompt_tokens: int | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Measure context usage using Ollama model limits and tokenization."""
    ai = resolve_web_ai_config(project_slug, model_override=model)
    limits = resolve_ollama_limits(project_slug, model_override=model)

    if not ollama_health_ok(ai.endpoint):
        used = measured_prompt_tokens if measured_prompt_tokens is not None else _offline_used_tokens(
            project_slug=project_slug,
            history=history,
            summary=summary,
            message=message,
            map_pins=map_pins,
            messages=messages,
        )
        return build_chat_context_payload(
            num_ctx=limits.num_ctx,
            used_tokens=used,
            token_count_source="offline_estimate",
            limit_source=limits.limit_source,
        )

    if messages is None:
        system_prompt = web_chat_system_prompt(
            project_slug=project_slug,
            map_pins=normalize_map_pins(map_pins),
        )
        messages = build_context_messages(
            message=message or "(next message)",
            history=history,
            summary=summary,
            system_prompt=system_prompt,
            map_pins=map_pins,
        )

    if measured_prompt_tokens is not None:
        used = measured_prompt_tokens
        source = "ollama_usage"
    else:
        used, source = ollama_count_tokens(
            base_url=ai.endpoint,
            model=ai.model,
            messages=messages,
            tools=WEB_TOOL_SCHEMAS,
            num_ctx=limits.num_ctx,
        )

    return build_chat_context_payload(
        num_ctx=limits.num_ctx,
        used_tokens=used,
        model_context_length=limits.model_context_length,
        modelfile_num_ctx=limits.modelfile_num_ctx,
        token_count_source=source,
        limit_source=limits.limit_source,
        measured_prompt_tokens=measured_prompt_tokens,
    )


def _offline_used_tokens(
    *,
    project_slug: str | None,
    history: list | None,
    summary: str | None,
    message: str,
    map_pins: list | None,
    messages: list[dict[str, Any]] | None,
) -> int:
    if messages is None:
        system_prompt = web_chat_system_prompt(
            project_slug=project_slug,
            map_pins=normalize_map_pins(map_pins),
        )
        messages = build_context_messages(
            message=message or "(next message)",
            history=history,
            summary=summary,
            system_prompt=system_prompt,
            map_pins=map_pins,
        )
    from peaky_finders.site_suggestions.providers.mesh_grow_ai.ollama_context import (
        _estimate_messages_tokens,
    )

    return _estimate_messages_tokens(messages, WEB_TOOL_SCHEMAS)


SUMMARIZE_SYSTEM_PROMPT = (
    "You compress chat transcripts for a mesh radio site planning assistant. "
    "Produce a concise summary the assistant can use as memory in later turns. "
    "Preserve: place names, coordinates, map pins/viewshed actions, user preferences, "
    "open questions, and decisions. Use short paragraphs or bullets. No preamble."
)


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
    model: str | None = None,
) -> dict[str, Any]:
    turns = normalize_chat_history(history)
    if not turns and not (summary and summary.strip()):
        raise ValueError("nothing to summarize")

    ai = resolve_web_ai_config(project_slug, model_override=model)
    if not ollama_health_ok(ai.endpoint):
        raise OllamaError(f"Ollama not reachable at {ai.endpoint!r}")

    limits = resolve_ollama_limits(project_slug, model_override=model)

    transcript = _transcript_for_summary(history=turns, summary=summary)
    resp = chat_completions(
        base_url=ai.endpoint,
        model=ai.model,
        messages=[
            {"role": "system", "content": SUMMARIZE_SYSTEM_PROMPT},
            {"role": "user", "content": transcript},
        ],
        tools=None,
        temperature=0.1,
        num_ctx=limits.request_num_ctx,
        timeout_s=180.0,
    )
    choices = resp.get("choices") or []
    if not choices:
        raise OllamaError("Ollama returned empty summary")
    content = (choices[0].get("message") or {}).get("content")
    text = str(content).strip() if content else ""
    if not text:
        raise OllamaError("Ollama returned empty summary")

    ctx = measure_chat_context(project_slug=project_slug, history=[], summary=text, message="", model=model)
    return {
        "summary": text,
        "model": ai.model,
        "context": ctx,
    }
