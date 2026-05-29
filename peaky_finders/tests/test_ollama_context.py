"""Ollama context limits and token counting."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx

from peaky_finders.site_suggestions.providers.mesh_grow_ai.ollama_client import fetch_ollama_models
from peaky_finders.site_suggestions.providers.mesh_grow_ai.ollama_context import (
    OllamaModelLimits,
    clear_model_limits_cache,
    fetch_ollama_model_limits,
    fetch_running_context_length,
    model_context_length_from_info,
    ollama_count_tokens,
    parse_modelfile_parameters,
    usage_prompt_tokens,
)


def test_parse_modelfile_parameters() -> None:
    params = parse_modelfile_parameters("temperature 0.2\nnum_ctx 8192\n")
    assert params["temperature"] == "0.2"
    assert params["num_ctx"] == "8192"


def test_model_context_length_from_info() -> None:
    assert model_context_length_from_info({"gemma3.context_length": 131072}) == 131072
    assert model_context_length_from_info({"llama.context_length": 8192, "foo": 1}) == 8192


def test_model_names_match_registry_path() -> None:
    from peaky_finders.site_suggestions.providers.mesh_grow_ai.ollama_context import (
        _model_names_match,
        _normalize_ollama_model_name,
    )

    assert _normalize_ollama_model_name("registry.ollama.ai/library/gemma4:31b") == "gemma4:31b"
    assert _model_names_match("gemma4:31b", "registry.ollama.ai/library/gemma4:31b")


def test_fetch_running_context_length() -> None:
    ps_resp = MagicMock()
    ps_resp.status_code = 200
    ps_resp.json.return_value = {
        "models": [
            {
                "model": "registry.ollama.ai/library/gemma4:31b",
                "context_length": 262144,
            }
        ],
    }

    with patch("httpx.Client") as client_cls:
        client = MagicMock()
        client_cls.return_value.__enter__.return_value = client
        client.get.return_value = ps_resp

        assert (
            fetch_running_context_length("http://localhost:11434/v1", "gemma4:31b")
            == 262144
        )


def test_fetch_ollama_models() -> None:
    tags_resp = MagicMock()
    tags_resp.status_code = 200
    tags_resp.json.return_value = {
        "models": [
            {"name": "llama3:8b"},
            {"name": "qwen3.5:9b"},
        ],
    }

    with patch("httpx.Client") as client_cls:
        client = MagicMock()
        client_cls.return_value.__enter__.return_value = client
        client.get.return_value = tags_resp

        models = fetch_ollama_models("http://host.docker.internal:11434/v1")

    assert models == ["llama3:8b", "qwen3.5:9b"]


def test_fetch_ollama_model_limits_prefers_ps() -> None:
    clear_model_limits_cache()
    show_resp = MagicMock()
    show_resp.status_code = 200
    show_resp.json.return_value = {
        "parameters": "num_ctx 2048\n",
        "model_info": {"gemma3.context_length": 131072},
    }
    ps_resp = MagicMock()
    ps_resp.status_code = 200
    ps_resp.json.return_value = {
        "models": [
            {
                "model": "registry.ollama.ai/library/gemma4:31b",
                "context_length": 262144,
            }
        ],
    }

    with patch("httpx.Client") as client_cls:
        client = MagicMock()
        client_cls.return_value.__enter__.return_value = client
        client.post.return_value = show_resp
        client.get.return_value = ps_resp

        limits = fetch_ollama_model_limits(
            "http://host.docker.internal:11434/v1",
            "gemma4:31b",
            cap=None,
        )

    assert limits.num_ctx == 262144
    assert limits.limit_source == "ollama_ps"
    assert limits.request_num_ctx is None


def test_fetch_ollama_model_limits_show_when_not_running() -> None:
    clear_model_limits_cache()
    show_resp = MagicMock()
    show_resp.status_code = 200
    show_resp.json.return_value = {
        "parameters": "num_ctx 8192\n",
        "model_info": {"gemma3.context_length": 131072},
    }
    ps_resp = MagicMock()
    ps_resp.status_code = 200
    ps_resp.json.return_value = {"models": []}

    with patch("httpx.Client") as client_cls:
        client = MagicMock()
        client_cls.return_value.__enter__.return_value = client
        client.post.return_value = show_resp
        client.get.return_value = ps_resp

        limits = fetch_ollama_model_limits(
            "http://localhost:11434/v1",
            "gemma4:31b",
            cap=None,
        )

    assert limits.num_ctx == 8192
    assert limits.limit_source == "ollama_show"


def test_fetch_ollama_model_limits_preset_cap() -> None:
    clear_model_limits_cache()
    show_resp = MagicMock()
    show_resp.status_code = 200
    show_resp.json.return_value = {
        "parameters": "",
        "model_info": {"gemma3.context_length": 131072},
    }
    ps_resp = MagicMock()
    ps_resp.status_code = 200
    ps_resp.json.return_value = {
        "models": [{"model": "gemma4:31b", "context_length": 262144}],
    }

    with patch("httpx.Client") as client_cls:
        client = MagicMock()
        client_cls.return_value.__enter__.return_value = client
        client.post.return_value = show_resp
        client.get.return_value = ps_resp

        limits = fetch_ollama_model_limits(
            "http://localhost:11434/v1",
            "gemma4:31b",
            cap=32768,
        )

    assert limits.num_ctx == 32768
    assert "preset_cap" in limits.limit_source
    assert limits.request_num_ctx == 32768


def test_ollama_count_tokens_uses_tokenize() -> None:
    tok_resp = MagicMock()
    tok_resp.status_code = 200
    tok_resp.json.return_value = {"tokens": [1, 2, 3, 4, 5]}

    with patch("httpx.Client") as client_cls:
        client = MagicMock()
        client_cls.return_value.__enter__.return_value = client
        client.post.return_value = tok_resp

        count, source = ollama_count_tokens(
            base_url="http://localhost:11434/v1",
            model="test",
            messages=[{"role": "user", "content": "hi"}],
            tools=None,
        )

    assert count == 5
    assert source == "ollama_tokenize"


def test_usage_prompt_tokens() -> None:
    assert usage_prompt_tokens({"prompt_tokens": 120, "completion_tokens": 8}) == 120
    assert usage_prompt_tokens({"input_tokens": 50}) == 50
