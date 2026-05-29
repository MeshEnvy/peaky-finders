"""Ollama OpenAI-compatible chat client."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Any

import httpx


class OllamaError(RuntimeError):
    pass


def _ollama_root_url(base_url: str) -> str:
    root = base_url.rstrip("/")
    if root.endswith("/v1"):
        root = root[:-3]
    return root


def ollama_health_ok(base_url: str, *, timeout_s: float = 5.0) -> bool:
    root = _ollama_root_url(base_url)
    try:
        with httpx.Client(timeout=timeout_s) as client:
            resp = client.get(f"{root}/api/tags")
            return resp.status_code == 200
    except httpx.HTTPError:
        return False


def fetch_ollama_models(base_url: str, *, timeout_s: float = 5.0) -> list[str]:
    """Return sorted model names from Ollama ``/api/tags``."""
    root = _ollama_root_url(base_url)
    try:
        with httpx.Client(timeout=timeout_s) as client:
            resp = client.get(f"{root}/api/tags")
    except httpx.HTTPError as exc:
        raise OllamaError(f"Ollama request failed: {exc}") from exc
    if resp.status_code >= 400:
        raise OllamaError(f"Ollama HTTP {resp.status_code}: {resp.text[:500]}")
    data = resp.json()
    if not isinstance(data, dict):
        raise OllamaError("Ollama returned non-object JSON from /api/tags")
    models = data.get("models") or []
    names: list[str] = []
    for entry in models:
        if isinstance(entry, dict) and entry.get("name"):
            names.append(str(entry["name"]))
    return sorted(set(names))


def _chat_body(
    *,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    temperature: float,
    stream: bool,
    num_ctx: int | None = None,
    include_usage: bool = False,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": float(temperature),
        "stream": stream,
    }
    if tools:
        body["tools"] = tools
    if num_ctx is not None:
        body["options"] = {"num_ctx": int(num_ctx)}
    if stream and include_usage:
        body["stream_options"] = {"include_usage": True}
    return body


def chat_completions(
    *,
    base_url: str,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    temperature: float = 0.2,
    num_ctx: int | None = None,
    timeout_s: float = 300.0,
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/chat/completions"
    body = _chat_body(
        model=model,
        messages=messages,
        tools=tools,
        temperature=temperature,
        stream=False,
        num_ctx=num_ctx,
    )
    try:
        with httpx.Client(timeout=timeout_s) as client:
            resp = client.post(url, json=body)
    except httpx.HTTPError as exc:
        raise OllamaError(f"Ollama request failed: {exc}") from exc
    if resp.status_code >= 400:
        raise OllamaError(f"Ollama HTTP {resp.status_code}: {resp.text[:500]}")
    data = resp.json()
    if not isinstance(data, dict):
        raise OllamaError("Ollama returned non-object JSON")
    return data


def chat_completions_stream(
    *,
    base_url: str,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    temperature: float = 0.2,
    num_ctx: int | None = None,
    timeout_s: float = 300.0,
    should_cancel: Callable[[], bool] | None = None,
) -> Iterator[str]:
    """Yield OpenAI-style SSE payload strings (without the ``data:`` prefix)."""
    url = f"{base_url.rstrip('/')}/chat/completions"
    body = _chat_body(
        model=model,
        messages=messages,
        tools=tools,
        temperature=temperature,
        stream=True,
        num_ctx=num_ctx,
        include_usage=True,
    )
    try:
        with httpx.Client(timeout=timeout_s) as client:
            with client.stream("POST", url, json=body) as resp:
                if resp.status_code >= 400:
                    detail = resp.read().decode("utf-8", errors="replace")[:500]
                    raise OllamaError(f"Ollama HTTP {resp.status_code}: {detail}")
                for line in resp.iter_lines():
                    if should_cancel and should_cancel():
                        break
                    if not line or not line.startswith("data: "):
                        continue
                    payload = line[6:].strip()
                    if payload == "[DONE]":
                        break
                    yield payload
    except httpx.HTTPError as exc:
        raise OllamaError(f"Ollama request failed: {exc}") from exc


def ollama_native_chat_stream(
    *,
    base_url: str,
    model: str,
    messages: list[dict[str, Any]],
    temperature: float = 0.2,
    think: bool = False,
    num_ctx: int = 8192,
    timeout_s: float = 300.0,
) -> Iterator[str]:
    """Stream assistant text tokens from Ollama's native ``/api/chat`` endpoint."""
    url = f"{_ollama_root_url(base_url)}/api/chat"
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
        "think": think,
        "options": {"num_ctx": int(num_ctx), "temperature": float(temperature)},
    }
    try:
        with httpx.Client(timeout=timeout_s) as client:
            with client.stream("POST", url, json=body) as resp:
                if resp.status_code >= 400:
                    detail = resp.read().decode("utf-8", errors="replace")[:500]
                    raise OllamaError(f"Ollama HTTP {resp.status_code}: {detail}")
                for line in resp.iter_lines():
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(data, dict):
                        continue
                    message = data.get("message") or {}
                    content = message.get("content")
                    if content:
                        yield str(content)
    except httpx.HTTPError as exc:
        raise OllamaError(f"Ollama request failed: {exc}") from exc


def stream_delta_text(payload: str) -> str:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return ""
    if not isinstance(data, dict):
        return ""
    choices = data.get("choices") or []
    if not choices:
        return ""
    delta = choices[0].get("delta") or {}
    content = delta.get("content")
    if content:
        return str(content)
    reasoning = delta.get("reasoning")
    return str(reasoning) if reasoning else ""


def _merge_stream_tool_calls(
    tool_calls_by_index: dict[int, dict[str, Any]],
    delta_tool_calls: list[Any],
) -> None:
    for tc in delta_tool_calls:
        if not isinstance(tc, dict):
            continue
        idx = int(tc.get("index", 0))
        entry = tool_calls_by_index.setdefault(
            idx,
            {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
        )
        if tc.get("id"):
            entry["id"] = str(tc["id"])
        fn = tc.get("function") or {}
        if fn.get("name"):
            entry["function"]["name"] += str(fn["name"])
        if fn.get("arguments") is not None:
            entry["function"]["arguments"] += str(fn["arguments"])


def assemble_streamed_assistant_message(
    *,
    content_parts: list[str],
    tool_calls_by_index: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": "".join(content_parts)}
    if tool_calls_by_index:
        message["tool_calls"] = [
            tool_calls_by_index[idx] for idx in sorted(tool_calls_by_index)
        ]
    return message


def stream_chat_completions_turn(
    *,
    base_url: str,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    temperature: float = 0.2,
    num_ctx: int | None = None,
    timeout_s: float = 300.0,
    should_cancel: Callable[[], bool] | None = None,
) -> Iterator[tuple[str, str | dict[str, Any] | None]]:
    """Yield deltas, optional ``("usage", dict)``, then ``("done", assistant_message)``."""
    content_parts: list[str] = []
    tool_calls_by_index: dict[int, dict[str, Any]] = {}
    last_usage: dict[str, Any] | None = None

    for payload in chat_completions_stream(
        base_url=base_url,
        model=model,
        messages=messages,
        tools=tools,
        temperature=temperature,
        num_ctx=num_ctx,
        timeout_s=timeout_s,
        should_cancel=should_cancel,
    ):
        if should_cancel and should_cancel():
            return
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        usage = data.get("usage")
        if isinstance(usage, dict) and usage:
            last_usage = usage
        choices = data.get("choices") or []
        if not choices:
            continue
        delta = choices[0].get("delta") or {}
        content = delta.get("content")
        if content:
            text = str(content)
            content_parts.append(text)
            yield "delta", text
        reasoning = delta.get("reasoning")
        if reasoning:
            yield "reasoning", str(reasoning)
        _merge_stream_tool_calls(tool_calls_by_index, delta.get("tool_calls") or [])

    if should_cancel and should_cancel():
        return

    if last_usage:
        yield "usage", last_usage

    yield "done", assemble_streamed_assistant_message(
        content_parts=content_parts,
        tool_calls_by_index=tool_calls_by_index,
    )
