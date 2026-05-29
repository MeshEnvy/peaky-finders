"""Ollama OpenAI-compatible chat client."""

from __future__ import annotations

import json
from collections.abc import Iterator
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


def _chat_body(
    *,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    temperature: float,
    stream: bool,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": float(temperature),
        "stream": stream,
    }
    if tools:
        body["tools"] = tools
    return body


def chat_completions(
    *,
    base_url: str,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    temperature: float = 0.2,
    timeout_s: float = 300.0,
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/chat/completions"
    body = _chat_body(
        model=model,
        messages=messages,
        tools=tools,
        temperature=temperature,
        stream=False,
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
    timeout_s: float = 300.0,
) -> Iterator[str]:
    """Yield OpenAI-style SSE payload strings (without the ``data:`` prefix)."""
    url = f"{base_url.rstrip('/')}/chat/completions"
    body = _chat_body(
        model=model,
        messages=messages,
        tools=tools,
        temperature=temperature,
        stream=True,
    )
    try:
        with httpx.Client(timeout=timeout_s) as client:
            with client.stream("POST", url, json=body) as resp:
                if resp.status_code >= 400:
                    detail = resp.read().decode("utf-8", errors="replace")[:500]
                    raise OllamaError(f"Ollama HTTP {resp.status_code}: {detail}")
                for line in resp.iter_lines():
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
