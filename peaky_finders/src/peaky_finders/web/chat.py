"""Simple Ollama chat for the Peaky web UI."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

from peaky_finders.site_suggestions.providers.mesh_grow_ai.ollama_client import (
    OllamaError,
    chat_completions,
    ollama_health_ok,
)
from peaky_finders.sites_job import MeshGrowAiStrategyConfig, load_preset, peaky_projects_dir


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


def resolve_web_chat_num_ctx(project_slug: str | None) -> int:
    return int(resolve_ollama_config(project_slug).web_chat_num_ctx)


def chat_messages(message: str) -> list[dict[str, str]]:
    return [
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


def stream_chat(
    message: str,
    *,
    project_slug: str | None = None,
    history: list | None = None,
    summary: str | None = None,
    map_pins: list | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> Iterator[dict[str, Any]]:
    from peaky_finders.web.chat_agent import stream_web_chat

    yield from stream_web_chat(
        message,
        project_slug=project_slug,
        history=history,
        summary=summary,
        map_pins=map_pins,
        should_cancel=should_cancel,
    )
