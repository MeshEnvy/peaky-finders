"""Simple Ollama chat for the Peaky web UI."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from peaky_finders.site_suggestions.providers.mesh_grow_ai.ollama_client import (
    OllamaError,
    chat_completions,
    ollama_health_ok,
    ollama_native_chat_stream,
)
from peaky_finders.sites_job import MeshGrowAiStrategyConfig, load_preset, peaky_projects_dir

CHAT_SYSTEM_PROMPT = (
    "You are a helpful assistant for Peaky, a mesh radio site planning tool. "
    "Keep replies concise and friendly."
)
WEB_CHAT_NUM_CTX = 8192


def extract_assistant_content(resp: dict[str, Any]) -> str:
    choices = resp.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content")
    return str(content).strip() if content else ""


def resolve_ollama_config(project_slug: str | None) -> MeshGrowAiStrategyConfig:
    cfg = MeshGrowAiStrategyConfig()
    if not project_slug:
        return cfg
    preset_path = peaky_projects_dir() / project_slug / "config.yaml"
    if not preset_path.is_file():
        return cfg
    preset = load_preset(preset_path)
    bundle = preset.bundle
    if bundle and bundle.site_suggestions:
        return bundle.site_suggestions.mesh_grow_ai
    return cfg


def chat_messages(message: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": CHAT_SYSTEM_PROMPT},
        {"role": "user", "content": message},
    ]


def simple_chat(message: str, *, project_slug: str | None = None) -> dict[str, str]:
    ai = resolve_ollama_config(project_slug)
    if not ollama_health_ok(ai.ollama_base_url):
        raise OllamaError(f"Ollama not reachable at {ai.ollama_base_url!r}")

    resp = chat_completions(
        base_url=ai.ollama_base_url,
        model=ai.ollama_model,
        messages=chat_messages(message),
        temperature=float(ai.temperature),
        timeout_s=120.0,
    )
    reply = extract_assistant_content(resp)
    if not reply:
        raise OllamaError("Ollama returned empty reply")
    return {"reply": reply, "model": ai.ollama_model}


def stream_chat(message: str, *, project_slug: str | None = None) -> Iterator[dict[str, Any]]:
    ai = resolve_ollama_config(project_slug)
    if not ollama_health_ok(ai.ollama_base_url):
        yield {"op": "chat.error", "message": f"Ollama not reachable at {ai.ollama_base_url!r}"}
        return

    yield {"op": "chat.started", "model": ai.ollama_model}

    got_content = False
    try:
        for text in ollama_native_chat_stream(
            base_url=ai.ollama_base_url,
            model=ai.ollama_model,
            messages=chat_messages(message),
            temperature=float(ai.temperature),
            think=False,
            num_ctx=WEB_CHAT_NUM_CTX,
            timeout_s=300.0,
        ):
            got_content = True
            yield {"op": "chat.delta", "text": text}
    except OllamaError as exc:
        yield {"op": "chat.error", "message": str(exc)}
        return

    if not got_content:
        yield {"op": "chat.error", "message": "Ollama returned empty reply"}
        return
    yield {"op": "chat.done", "model": ai.ollama_model}
