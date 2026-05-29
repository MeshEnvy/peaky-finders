"""Simple Ollama chat for the Peaky web UI."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

from peaky_finders.site_suggestions.providers.mesh_grow_ai.ollama_client import (
    OllamaError,
    chat_completions,
    fetch_ollama_models,
    ollama_health_ok,
)
from peaky_finders.sites_job import AiConfig, load_preset, peaky_projects_dir


def extract_assistant_content(resp: dict[str, Any]) -> str:
    choices = resp.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content")
    return str(content).strip() if content else ""


def resolve_web_ai_config(
    project_slug: str | None,
    *,
    model_override: str | None = None,
) -> AiConfig:
    cfg = AiConfig()
    if project_slug:
        preset_path = peaky_projects_dir() / project_slug / "config.yaml"
        if preset_path.is_file():
            preset = load_preset(preset_path)
            cfg = preset.ai
    if model_override:
        cfg = cfg.model_copy(update={"model": model_override})
    return cfg


def resolve_web_chat_num_ctx_cap(
    project_slug: str | None,
    *,
    model_override: str | None = None,
) -> int | None:
    """Optional preset cap; ``None`` means use Ollama's discovered context size."""
    cap = resolve_web_ai_config(project_slug, model_override=model_override).num_ctx
    return int(cap) if cap is not None else None


def resolve_ollama_limits(
    project_slug: str | None,
    *,
    model_override: str | None = None,
):
    """Context limits from Ollama (``/api/ps``, ``/api/show``) with optional preset cap."""
    from peaky_finders.site_suggestions.providers.mesh_grow_ai.ollama_context import (
        OLLAMA_FALLBACK_NUM_CTX,
        OllamaModelLimits,
        fetch_ollama_model_limits,
    )

    ai = resolve_web_ai_config(project_slug, model_override=model_override)
    cap = resolve_web_chat_num_ctx_cap(project_slug, model_override=model_override)
    if not ollama_health_ok(ai.endpoint):
        fallback = cap if cap is not None else OLLAMA_FALLBACK_NUM_CTX
        return OllamaModelLimits(
            model=ai.model,
            num_ctx=fallback,
            model_context_length=None,
            modelfile_num_ctx=None,
            limit_source="offline",
            request_num_ctx=cap,
        )
    return fetch_ollama_model_limits(ai.endpoint, ai.model, cap=cap)


def resolve_allocated_num_ctx(
    project_slug: str | None,
    *,
    model_override: str | None = None,
) -> int:
    """Context window size for display and metering."""
    return resolve_ollama_limits(project_slug, model_override=model_override).num_ctx


def resolve_request_num_ctx(
    project_slug: str | None,
    *,
    model_override: str | None = None,
) -> int | None:
    """``options.num_ctx`` for chat requests; ``None`` lets Ollama choose."""
    return resolve_ollama_limits(project_slug, model_override=model_override).request_num_ctx


def list_chat_models(project_slug: str | None) -> dict[str, Any]:
    """Models available at the preset's Ollama endpoint."""
    ai = resolve_web_ai_config(project_slug)
    reachable = ollama_health_ok(ai.endpoint)
    models: list[str] = []
    if reachable:
        try:
            models = fetch_ollama_models(ai.endpoint)
        except OllamaError:
            reachable = False
    if ai.model and ai.model not in models:
        models = sorted(set(models) | {ai.model})
    return {
        "models": models,
        "default_model": ai.model,
        "endpoint": ai.endpoint,
        "reachable": reachable,
    }


def chat_messages(message: str) -> list[dict[str, str]]:
    return [
        {"role": "user", "content": message},
    ]


def simple_chat(
    message: str,
    *,
    project_slug: str | None = None,
    model: str | None = None,
) -> dict[str, str]:
    ai = resolve_web_ai_config(project_slug, model_override=model)
    if not ollama_health_ok(ai.endpoint):
        raise OllamaError(f"Ollama not reachable at {ai.endpoint!r}")

    resp = chat_completions(
        base_url=ai.endpoint,
        model=ai.model,
        messages=chat_messages(message),
        temperature=float(ai.temperature),
        num_ctx=resolve_request_num_ctx(project_slug, model_override=model),
        timeout_s=120.0,
    )
    reply = extract_assistant_content(resp)
    if not reply:
        raise OllamaError("Ollama returned empty reply")
    return {"reply": reply, "model": ai.model}


def stream_chat(
    message: str,
    *,
    project_slug: str | None = None,
    history: list | None = None,
    summary: str | None = None,
    map_pins: list | None = None,
    model: str | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> Iterator[dict[str, Any]]:
    from peaky_finders.web.chat_agent import stream_web_chat

    yield from stream_web_chat(
        message,
        project_slug=project_slug,
        history=history,
        summary=summary,
        map_pins=map_pins,
        model=model,
        should_cancel=should_cancel,
    )
