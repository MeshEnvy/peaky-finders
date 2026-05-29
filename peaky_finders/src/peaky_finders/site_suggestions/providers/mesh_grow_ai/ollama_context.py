"""Ollama model context limits and token counting for chat metering."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import httpx

from peaky_finders.site_suggestions.providers.mesh_grow_ai.ollama_client import (
    OllamaError,
    _ollama_root_url,
)

OLLAMA_FALLBACK_NUM_CTX = 4096
_CONTEXT_LENGTH_SUFFIX = ".context_length"


@dataclass(frozen=True)
class OllamaModelLimits:
    model: str
    num_ctx: int
    model_context_length: int | None
    modelfile_num_ctx: int | None
    limit_source: str
    request_num_ctx: int | None


def parse_modelfile_parameters(parameters: str | None) -> dict[str, str]:
    out: dict[str, str] = {}
    if not parameters:
        return out
    for line in str(parameters).splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) == 2:
            out[parts[0]] = parts[1]
    return out


def model_context_length_from_info(model_info: dict[str, Any] | None) -> int | None:
    if not model_info:
        return None
    lengths: list[int] = []
    for key, value in model_info.items():
        if not str(key).endswith(_CONTEXT_LENGTH_SUFFIX):
            continue
        try:
            lengths.append(int(value))
        except (TypeError, ValueError):
            continue
    return max(lengths) if lengths else None


def _parse_positive_int(text: str | None) -> int | None:
    if text is None:
        return None
    try:
        value = int(str(text).strip())
    except ValueError:
        return None
    return value if value > 0 else None


def _normalize_ollama_model_name(name: str) -> str:
    """``registry.ollama.ai/library/gemma4:31b`` → ``gemma4:31b``."""
    n = str(name or "").strip()
    if "/" in n:
        n = n.rsplit("/", 1)[-1]
    return n


def _model_names_match(requested: str, running: str) -> bool:
    req = _normalize_ollama_model_name(requested)
    run = _normalize_ollama_model_name(running)
    if not req or not run:
        return False
    if req == run:
        return True
    return run.startswith(f"{req}:") or req.startswith(f"{run}:")


def fetch_running_context_length(base_url: str, model: str) -> int | None:
    """Loaded model context from ``GET /api/ps`` (runtime KV size)."""
    root = _ollama_root_url(base_url)
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(f"{root}/api/ps")
    except httpx.HTTPError:
        return None
    if resp.status_code >= 400:
        return None
    data = resp.json()
    if not isinstance(data, dict):
        return None
    for entry in data.get("models") or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("model") or entry.get("name") or "")
        if not _model_names_match(model, name):
            continue
        ctx = entry.get("context_length")
        try:
            return max(2048, int(ctx))
        except (TypeError, ValueError):
            continue
    return None


@lru_cache(maxsize=64)
def _cached_show_payload(base_url: str, model: str) -> tuple[int | None, int | None]:
    root = _ollama_root_url(base_url)
    url = f"{root}/api/show"
    with httpx.Client(timeout=15.0) as client:
        resp = client.post(url, json={"model": model})
    if resp.status_code >= 400:
        raise OllamaError(f"Ollama show HTTP {resp.status_code}: {resp.text[:500]}")
    data = resp.json()
    if not isinstance(data, dict):
        raise OllamaError("Ollama show returned non-object JSON")
    params = parse_modelfile_parameters(data.get("parameters"))
    modelfile_num_ctx = _parse_positive_int(params.get("num_ctx"))
    model_max = model_context_length_from_info(data.get("model_info"))
    return modelfile_num_ctx, model_max


def _apply_cap(
    *,
    num_ctx: int,
    model_max: int | None,
    cap: int | None,
    trust_runtime: bool = False,
) -> int:
    out = num_ctx
    if not trust_runtime and model_max is not None:
        out = min(out, model_max)
    if cap is not None:
        out = min(out, cap)
    return max(2048, out)


def fetch_ollama_model_limits(
    base_url: str,
    model: str,
    *,
    cap: int | None = None,
) -> OllamaModelLimits:
    """Resolve context window from Ollama (``/api/ps`` then ``/api/show``)."""
    model_max: int | None = None
    modelfile_num_ctx: int | None = None
    limit_source = "ollama_fallback"
    num_ctx = OLLAMA_FALLBACK_NUM_CTX

    running_ctx = fetch_running_context_length(base_url, model)
    trust_runtime = running_ctx is not None
    if running_ctx is not None:
        num_ctx = running_ctx
        limit_source = "ollama_ps"

    try:
        modelfile_num_ctx, model_max = _cached_show_payload(base_url, model)
    except (OllamaError, httpx.HTTPError):
        allocated = _apply_cap(num_ctx=num_ctx, model_max=None, cap=cap, trust_runtime=trust_runtime)
        return OllamaModelLimits(
            model=model,
            num_ctx=allocated,
            model_context_length=None,
            modelfile_num_ctx=None,
            limit_source=limit_source,
            request_num_ctx=cap,
        )

    if running_ctx is None and modelfile_num_ctx is not None:
        num_ctx = modelfile_num_ctx
        limit_source = "ollama_show"
    elif running_ctx is None and model_max is not None:
        num_ctx = model_max
        limit_source = "ollama_show_max"

    allocated = _apply_cap(
        num_ctx=num_ctx,
        model_max=model_max,
        cap=cap,
        trust_runtime=trust_runtime,
    )
    if cap is not None and allocated < num_ctx:
        limit_source = f"{limit_source}+preset_cap"

    return OllamaModelLimits(
        model=model,
        num_ctx=allocated,
        model_context_length=model_max,
        modelfile_num_ctx=modelfile_num_ctx,
        limit_source=limit_source,
        request_num_ctx=cap,
    )


def _token_count_from_response(data: dict[str, Any]) -> int | None:
    if not isinstance(data, dict):
        return None
    for key in ("token_count", "count"):
        if key in data:
            try:
                return max(0, int(data[key]))
            except (TypeError, ValueError):
                pass
    tokens = data.get("tokens")
    if isinstance(tokens, list):
        return len(tokens)
    return None


def ollama_count_tokens(
    *,
    base_url: str,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    num_ctx: int | None = None,
    timeout_s: float = 60.0,
) -> tuple[int, str]:
    """Return (token_count, source) using Ollama ``/api/tokenize`` or a local estimate."""
    root = _ollama_root_url(base_url)
    url = f"{root}/api/tokenize"
    options: dict[str, Any] = {}
    if num_ctx is not None:
        options["num_ctx"] = int(num_ctx)

    body: dict[str, Any] = {"model": model, "messages": messages}
    if options:
        body["options"] = options

    total = 0
    try:
        with httpx.Client(timeout=timeout_s) as client:
            resp = client.post(url, json=body)
            if resp.status_code == 404:
                raise OllamaError("tokenize not supported")
            if resp.status_code >= 400:
                raise OllamaError(f"tokenize HTTP {resp.status_code}")
            count = _token_count_from_response(resp.json())
            if count is None:
                raise OllamaError("tokenize returned no count")
            total += count
    except (httpx.HTTPError, OllamaError, json.JSONDecodeError):
        return _estimate_messages_tokens(messages, tools), "estimate"

    if tools:
        tools_body: dict[str, Any] = {
            "model": model,
            "content": json.dumps(tools, default=str),
        }
        if options:
            tools_body["options"] = options
        try:
            with httpx.Client(timeout=timeout_s) as client:
                resp = client.post(url, json=tools_body)
                if resp.status_code < 400:
                    count = _token_count_from_response(resp.json())
                    if count is not None:
                        total += count
        except (httpx.HTTPError, json.JSONDecodeError):
            total += max(1, len(json.dumps(tools, default=str)) // 4)

    return total, "ollama_tokenize"


def _estimate_messages_tokens(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
) -> int:
    total = 0
    for message in messages:
        total += 4
        content = message.get("content")
        if content:
            total += max(1, len(str(content)) // 4)
        tool_calls = message.get("tool_calls")
        if tool_calls:
            total += max(1, len(json.dumps(tool_calls, default=str)) // 4)
        name = message.get("name")
        if name:
            total += max(1, len(str(name)) // 4)
    if tools:
        total += max(1, len(json.dumps(tools, default=str)) // 4)
    return total


def usage_prompt_tokens(usage: dict[str, Any] | None) -> int | None:
    if not usage:
        return None
    for key in ("prompt_tokens", "input_tokens"):
        if key in usage:
            try:
                return max(0, int(usage[key]))
            except (TypeError, ValueError):
                pass
    return None


def clear_model_limits_cache() -> None:
    _cached_show_payload.cache_clear()
