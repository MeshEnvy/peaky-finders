"""Tool-calling Ollama loop for the Peaky web chat."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Any

from peaky_finders.site_suggestions.providers.mesh_grow_ai.ollama_client import (
    OllamaError,
    ollama_health_ok,
    stream_chat_completions_turn,
)
from peaky_finders.web.chat import resolve_ollama_config, resolve_ollama_limits, resolve_request_num_ctx
from peaky_finders.web.chat_context import measure_chat_context
from peaky_finders.site_suggestions.providers.mesh_grow_ai.ollama_context import usage_prompt_tokens
from peaky_finders.web.chat_history import build_chat_messages
from peaky_finders.web.chat_tools import (
    WEB_TOOL_SCHEMAS,
    WebChatContext,
    dispatch_web_tool,
    truncate_tool_result_for_stream,
    web_chat_context_for_project,
    web_chat_system_prompt,
)

WEB_CHAT_MAX_STEPS = 12

_TOOL_STATUS: dict[str, str] = {
    "query_project_dem_highest": "Scanning Skadi DEM for highest point in project AOI…",
    "show_on_map": "Placing pin and computing viewshed…",
    "geocode_place": "Geocoding place name…",
}


def _run_model_turn(
    *,
    ai: Any,
    messages: list[dict[str, Any]],
    request_num_ctx: int | None,
    chat_client: Callable[..., dict[str, Any]] | None,
    should_cancel: Callable[[], bool] | None = None,
) -> Iterator[tuple[str, Any]]:
    """Yield chat SSE ops for one model turn; final yield is ``("_assistant", message)``."""
    if should_cancel and should_cancel():
        return

    if chat_client is not None:
        try:
            resp = chat_client(
                base_url=ai.ollama_base_url,
                model=ai.ollama_model,
                messages=messages,
                tools=WEB_TOOL_SCHEMAS,
                temperature=float(ai.temperature),
                num_ctx=request_num_ctx,
            )
        except OllamaError as exc:
            yield "error", str(exc)
            return

        choices = resp.get("choices") or []
        if not choices:
            yield "_assistant", None
            return

        assistant = choices[0].get("message") or {}
        content = assistant.get("content")
        if content:
            yield "delta", str(content)
        reasoning = assistant.get("reasoning") or assistant.get("thinking")
        if reasoning:
            yield "reasoning", str(reasoning)
        yield "_assistant", assistant
        return

    try:
        for kind, payload in stream_chat_completions_turn(
            base_url=ai.ollama_base_url,
            model=ai.ollama_model,
            messages=messages,
            tools=WEB_TOOL_SCHEMAS,
            temperature=float(ai.temperature),
            num_ctx=request_num_ctx,
            should_cancel=should_cancel,
        ):
            if should_cancel and should_cancel():
                return
            if kind == "delta":
                yield "delta", payload
            elif kind == "reasoning":
                yield "reasoning", payload
            elif kind == "usage":
                yield "usage", payload
            elif kind == "done":
                yield "_assistant", payload
    except OllamaError as exc:
        yield "error", str(exc)


def stream_web_chat(
    message: str,
    *,
    project_slug: str | None = None,
    history: list | None = None,
    summary: str | None = None,
    map_pins: list | None = None,
    chat_client: Callable[..., dict[str, Any]] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> Iterator[dict[str, Any]]:
    """Run a map-aware tool loop; yield SSE ops (chat + map)."""
    ai = resolve_ollama_config(project_slug)
    if not ollama_health_ok(ai.ollama_base_url):
        yield {"op": "chat.error", "message": f"Ollama not reachable at {ai.ollama_base_url!r}"}
        return

    request_num_ctx = resolve_request_num_ctx(project_slug)
    context = measure_chat_context(
        project_slug=project_slug,
        history=history,
        summary=summary,
        message=message,
        map_pins=map_pins,
    )
    yield {"op": "chat.started", "model": ai.ollama_model, "context": context}

    pending: list[dict[str, Any]] = []

    def emit(op: str, **payload: Any) -> None:
        pending.append({"op": op, **payload})

    ctx = web_chat_context_for_project(project_slug, map_pins=map_pins)
    ctx.emit = emit

    messages: list[dict[str, Any]] = build_chat_messages(
        message=message,
        history=history,
        summary=summary,
        system_prompt=web_chat_system_prompt(project_slug=project_slug, map_pins=ctx.map_pins),
    )

    got_assistant_text = False

    for _step in range(WEB_CHAT_MAX_STEPS):
        if should_cancel and should_cancel():
            yield {"op": "chat.cancelled"}
            return

        pending.clear()
        assistant: dict[str, Any] | None = None

        for kind, payload in _run_model_turn(
            ai=ai,
            messages=messages,
            request_num_ctx=request_num_ctx,
            chat_client=chat_client,
            should_cancel=should_cancel,
        ):
            if should_cancel and should_cancel():
                yield {"op": "chat.cancelled"}
                return
            if kind == "error":
                yield {"op": "chat.error", "message": str(payload)}
                return
            if kind == "delta":
                got_assistant_text = True
                yield {"op": "chat.delta", "text": str(payload)}
            elif kind == "reasoning":
                yield {"op": "chat.thinking.delta", "text": str(payload)}
            elif kind == "usage":
                prompt_tokens = usage_prompt_tokens(payload if isinstance(payload, dict) else None)
                if prompt_tokens is not None:
                    yield {
                        "op": "chat.context",
                        "context": measure_chat_context(
                            project_slug=project_slug,
                            messages=list(messages),
                            measured_prompt_tokens=prompt_tokens,
                        ),
                    }
            elif kind == "_assistant":
                assistant = payload if isinstance(payload, dict) else None

        if should_cancel and should_cancel():
            yield {"op": "chat.cancelled"}
            return

        if assistant is None:
            break

        messages.append(assistant)

        tool_calls = assistant.get("tool_calls") or []
        if not tool_calls:
            break

        for call in tool_calls:
            if should_cancel and should_cancel():
                yield {"op": "chat.cancelled"}
                return
            fn = call.get("function") or {}
            name = str(fn.get("name") or "")
            raw_args = fn.get("arguments") or "{}"
            if isinstance(raw_args, str):
                try:
                    args = json.loads(raw_args)
                except json.JSONDecodeError:
                    args = {}
            else:
                args = dict(raw_args)
            call_id = str(call.get("id") or name)

            yield {"op": "chat.tool_call", "call_id": call_id, "name": name, "arguments": args}

            status = _TOOL_STATUS.get(name)
            if status:
                yield {"op": "chat.status", "text": status}

            result = dispatch_web_tool(name, args, ctx=ctx)
            yield {
                "op": "chat.tool_result",
                "call_id": call_id,
                "name": name,
                "result": truncate_tool_result_for_stream(result),
            }
            for msg in pending:
                yield msg

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": name,
                    "content": json.dumps(result, default=str),
                }
            )

        yield {
            "op": "chat.context",
            "context": measure_chat_context(
                project_slug=project_slug,
                messages=list(messages),
            ),
        }

    if not got_assistant_text:
        yield {
            "op": "chat.delta",
            "text": "Done — check the map for the pin and viewshed.",
        }

    yield {
        "op": "chat.context",
        "context": measure_chat_context(project_slug=project_slug, messages=list(messages)),
    }
    yield {"op": "chat.done", "model": ai.ollama_model}
