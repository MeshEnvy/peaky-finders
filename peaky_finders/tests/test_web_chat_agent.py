"""Web chat agent and map tools tests."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import PropertyMock, patch

from peaky_finders.site_suggestions.providers.mesh_grow_ai.ollama_client import (
    assemble_streamed_assistant_message,
    stream_chat_completions_turn,
)
from peaky_finders.web.chat_agent import stream_web_chat
from peaky_finders.web.chat_tools import (
    WebChatContext,
    dispatch_web_tool,
    lookup_map_pin,
    normalize_map_pins,
    web_chat_system_prompt,
)
from peaky_finders.web.geocode import geocode_place


def test_assemble_streamed_assistant_message() -> None:
    msg = assemble_streamed_assistant_message(
        content_parts=["Hello", " world"],
        tool_calls_by_index={
            0: {
                "id": "c1",
                "type": "function",
                "function": {"name": "geocode_place", "arguments": '{"query":"x"}'},
            }
        },
    )
    assert msg["content"] == "Hello world"
    assert msg["tool_calls"][0]["function"]["name"] == "geocode_place"


def test_stream_chat_completions_turn_yields_deltas() -> None:
    payloads = [
        json.dumps({"choices": [{"delta": {"content": "Hel"}}]}),
        json.dumps({"choices": [{"delta": {"content": "lo"}}]}),
    ]

    with patch(
        "peaky_finders.site_suggestions.providers.mesh_grow_ai.ollama_client.chat_completions_stream",
        return_value=iter(payloads),
    ):
        events = list(
            stream_chat_completions_turn(
                base_url="http://ollama",
                model="test",
                messages=[{"role": "user", "content": "hi"}],
            )
        )

    assert events[0] == ("delta", "Hel")
    assert events[1] == ("delta", "lo")
    assert events[2][0] == "done"
    assert events[2][1]["content"] == "Hello"


def test_stream_chat_completions_turn_routes_reasoning_separately() -> None:
    payloads = [
        json.dumps({"choices": [{"delta": {"reasoning": "internal thought"}}]}),
        json.dumps({"choices": [{"delta": {"content": "Hi"}}]}),
    ]

    with patch(
        "peaky_finders.site_suggestions.providers.mesh_grow_ai.ollama_client.chat_completions_stream",
        return_value=iter(payloads),
    ):
        events = list(
            stream_chat_completions_turn(
                base_url="http://ollama",
                model="test",
                messages=[{"role": "user", "content": "hi"}],
            )
        )

    assert events[0] == ("reasoning", "internal thought")
    assert events[1] == ("delta", "Hi")
    assert events[2][1]["content"] == "Hi"


def test_geocode_place_parses_nominatim_payload() -> None:
    payload = [
        {
            "display_name": "Peavine Mountain, Nevada, USA",
            "lat": "39.591",
            "lon": "-119.947",
            "boundingbox": ["39.55", "39.63", "-119.98", "-119.91"],
            "category": "natural",
            "type": "peak",
            "importance": 0.5,
        }
    ]
    with patch("peaky_finders.web.geocode.httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        client.get.return_value.status_code = 200
        client.get.return_value.json.return_value = payload
        hits = geocode_place("Peavine Mountain")

    assert len(hits) == 1
    assert hits[0]["lat"] == 39.591
    assert hits[0]["bbox"] == [-119.98, 39.55, -119.91, 39.63]


def test_web_chat_system_prompt_requires_dem_for_peaks() -> None:
    prompt = web_chat_system_prompt(project_slug="nevada")
    assert "query_project_dem_highest" in prompt
    assert "Never state which peak is highest" in prompt


def test_query_project_dem_highest_tool() -> None:
    emitted: list[tuple[str, dict]] = []
    ctx = WebChatContext(project_slug="nevada", emit=lambda op, **kw: emitted.append((op, kw)))
    dem_hit = {
        "scope": "project bundle AOI",
        "project": "nevada",
        "lat": 37.846,
        "lon": -118.325,
        "elev_m": 4005.0,
        "elev_ft": 13140.0,
        "place_label": "Boundary Peak",
        "source": "Skadi SRTM (1 arc-second) highest cell in search polygon",
    }
    with patch(
        "peaky_finders.web.chat_tools.query_project_dem_highest",
        return_value=dem_hit,
    ):
        result = dispatch_web_tool("query_project_dem_highest", {}, ctx=ctx)
    assert result["elev_ft"] == 13140.0
    assert result["place_label"] == "Boundary Peak"
    assert "pin_id" in result
    assert any(op == "map.pin" for op, _ in emitted)


def test_query_project_dem_highest_requires_project() -> None:
    ctx = WebChatContext(project_slug=None)
    result = dispatch_web_tool("query_project_dem_highest", {}, ctx=ctx)
    assert "select a project" in result["error"]


def test_stream_web_chat_dem_highest_tool_loop() -> None:
    dem_payload = {
        "scope": "project bundle AOI",
        "project": "nevada",
        "lat": 37.846,
        "lon": -118.325,
        "elev_m": 4005.0,
        "elev_ft": 13140.0,
        "place_label": "Boundary Peak",
    }
    responses = [
        {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "d1",
                                "function": {
                                    "name": "query_project_dem_highest",
                                    "arguments": "{}",
                                },
                            }
                        ],
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "message": {
                        "content": (
                            "Boundary Peak is the highest DEM point in the Nevada project AOI "
                            "at about 13,140 ft."
                        ),
                    }
                }
            ]
        },
    ]

    def fake_client(**kwargs: object) -> dict:
        return responses.pop(0)

    with patch("peaky_finders.web.chat_agent.ollama_health_ok", return_value=True), patch(
        "peaky_finders.web.chat_agent.resolve_ollama_config",
    ) as cfg, patch(
        "peaky_finders.web.chat_tools.query_project_dem_highest",
        return_value=dem_payload,
    ):
        cfg.return_value.ollama_base_url = "http://ollama"
        cfg.return_value.ollama_model = "test"
        cfg.return_value.temperature = 0.2
        events = list(
            stream_web_chat(
                "what's the highest peak in NV?",
                project_slug="nevada",
                chat_client=fake_client,
            )
        )

    assert any(e.get("op") == "chat.tool_call" and e.get("name") == "query_project_dem_highest" for e in events)
    assert any(
        e.get("op") == "chat.delta" and "Boundary" in e.get("text", "") for e in events
    )


def test_geocode_call_limit() -> None:
    ctx = WebChatContext(project_slug="nevada")
    hit = {
        "query": "x",
        "results": [{"display_name": "X", "lat": 1.0, "lon": 2.0, "quality": "place", "score": 1.0}],
        "best": {"display_name": "X", "lat": 1.0, "lon": 2.0, "quality": "place", "score": 1.0},
        "hint": None,
    }
    with patch("peaky_finders.web.chat_tools.geocode_place_ranked", return_value=hit):
        dispatch_web_tool("geocode_place", {"query": "a"}, ctx=ctx)
        dispatch_web_tool("geocode_place", {"query": "b"}, ctx=ctx)
        third = dispatch_web_tool("geocode_place", {"query": "c"}, ctx=ctx)
    assert "already called twice" in third["error"]


def test_show_on_map_ok_when_viewshed_fails() -> None:
    ctx = WebChatContext(project_slug="nevada", emit=lambda *a, **k: None)
    with patch.object(
        WebChatContext,
        "preset_path",
        new_callable=PropertyMock,
        return_value=Path("/projects/nevada/config.yaml"),
    ), patch(
        "peaky_finders.web.chat_tools.ensure_chat_site_in_preset",
        return_value="chat-abc",
    ), patch(
        "peaky_finders.web.chat_tools.ensure_point_viewshed",
        side_effect=RuntimeError("viewshed boom"),
    ):
        result = dispatch_web_tool(
            "show_on_map",
            {"lat": 36.27, "lon": -115.69, "label": "Charleston Peak"},
            ctx=ctx,
        )
    assert result["ok"] is True
    assert result["saved_to_preset"] is True
    assert result["site_slug"] == "chat-abc"
    assert "viewshed_error" in result


def test_show_on_map_emits_pin_and_viewshed() -> None:
    emitted: list[tuple[str, dict]] = []

    def emit(op: str, **payload: object) -> None:
        emitted.append((op, payload))

    ctx = WebChatContext(project_slug="nevada", emit=emit)
    viewshed_rec = {
        "slug": "at-abc",
        "lat": 39.591,
        "lon": -119.947,
        "url": "/api/projects/nevada/viewsheds/at/splat.png?lat=39.591000&lon=-119.947000",
        "coordinates": [[-120, 40], [-119, 40], [-119, 39], [-120, 39]],
        "bounds": [-120.0, 39.0, -119.0, 40.0],
        "opacity": 0.5,
    }
    with patch.object(
        WebChatContext,
        "preset_path",
        new_callable=PropertyMock,
        return_value=Path("/projects/nevada/config.yaml"),
    ), patch(
        "peaky_finders.web.chat_tools.ensure_chat_site_in_preset",
        return_value="chat-peavine",
    ), patch(
        "peaky_finders.web.chat_tools.ensure_point_viewshed",
        return_value=viewshed_rec,
    ), patch(
        "peaky_finders.web.chat_tools.load_preset",
        return_value=object(),
    ), patch(
        "peaky_finders.web.chat_tools.rf_link_line_features_from_site",
        return_value=[
            {
                "type": "Feature",
                "properties": {"id": "chat-peavine--site-x", "from": "chat-peavine", "to": "site-x"},
                "geometry": {"type": "LineString", "coordinates": [[-119.947, 39.591], [-119.0, 39.0]]},
            }
        ],
    ):
        result = dispatch_web_tool(
            "show_on_map",
            {"lat": 39.591, "lon": -119.947, "label": "Peavine Mountain"},
            ctx=ctx,
        )

    assert result["ok"] is True
    ops = [op for op, _ in emitted]
    assert "map.pin" in ops
    assert "map.viewshed" in ops
    assert "map.line" in ops
    line_emits = [payload for op, payload in emitted if op == "map.line"]
    assert line_emits[0]["layer_id"] == "mesh_links"
    assert line_emits[0]["id"] == "chat-peavine--site-x"
    assert ("map.fit_bounds", {"bbox": viewshed_rec["bounds"]}) in emitted
    assert len(result["rf_links"]) == 1


def test_stream_web_chat_forwards_thinking_deltas() -> None:
    def fake_turn(**kwargs: object):
        yield "reasoning", "The user is asking"
        yield "reasoning", " about peaks."
        yield "delta", "Boundary Peak."
        yield "done", {"role": "assistant", "content": "Boundary Peak."}

    with patch("peaky_finders.web.chat_agent.ollama_health_ok", return_value=True), patch(
        "peaky_finders.web.chat_agent.resolve_ollama_config",
    ) as cfg, patch(
        "peaky_finders.web.chat_agent.stream_chat_completions_turn",
        side_effect=lambda **kwargs: fake_turn(**kwargs),
    ):
        cfg.return_value.ollama_base_url = "http://ollama"
        cfg.return_value.ollama_model = "test"
        cfg.return_value.temperature = 0.2
        events = list(stream_web_chat("tallest peak?", project_slug="nevada"))

    thinking = [e for e in events if e.get("op") == "chat.thinking.delta"]
    assert [e["text"] for e in thinking] == ["The user is asking", " about peaks."]
    assert any(e.get("op") == "chat.delta" and "Boundary" in e.get("text", "") for e in events)


def test_stream_web_chat_tool_loop() -> None:
    responses = [
        {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "c1",
                                "function": {
                                    "name": "geocode_place",
                                    "arguments": '{"query":"Peavine Mountain"}',
                                },
                            }
                        ],
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "c2",
                                "function": {
                                    "name": "show_on_map",
                                    "arguments": (
                                        '{"lat":39.591,"lon":-119.947,"label":"Peavine Mountain"}'
                                    ),
                                },
                            }
                        ],
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "message": {
                        "content": "Placed a pin on Peavine Mountain and computed the viewshed.",
                    }
                }
            ]
        },
    ]

    def fake_client(**kwargs: object) -> dict:
        return responses.pop(0)

    geocode_payload = {
        "query": "Peavine Mountain",
        "results": [{"display_name": "Peavine Mountain", "lat": 39.591, "lon": -119.947, "quality": "peak", "score": 10}],
        "best": {"display_name": "Peavine Mountain", "lat": 39.591, "lon": -119.947, "quality": "peak", "score": 10},
        "hint": None,
    }
    with patch("peaky_finders.web.chat_agent.ollama_health_ok", return_value=True), patch(
        "peaky_finders.web.chat_agent.resolve_ollama_config",
    ) as cfg, patch(
        "peaky_finders.web.chat_tools.geocode_place_ranked",
        return_value=geocode_payload,
    ), patch.object(
        WebChatContext,
        "preset_path",
        new_callable=PropertyMock,
        return_value=Path("/projects/nevada/config.yaml"),
    ), patch(
        "peaky_finders.web.chat_tools.ensure_chat_site_in_preset",
        return_value="chat-x",
    ), patch(
        "peaky_finders.web.chat_tools.ensure_point_viewshed",
        return_value={"slug": "chat-x", "url": "/u", "coordinates": []},
    ):
        cfg.return_value.ollama_base_url = "http://ollama"
        cfg.return_value.ollama_model = "test"
        cfg.return_value.temperature = 0.2
        events = list(
            stream_web_chat(
                "place a pin on Peavine Mountain",
                project_slug="nevada",
                chat_client=fake_client,
            )
        )

    ops = [e["op"] for e in events]
    assert "chat.started" in ops
    assert "chat.tool_call" in ops
    assert "map.pin" in ops
    assert "map.viewshed" in ops
    assert any(e.get("op") == "chat.delta" and "Peavine" in e.get("text", "") for e in events)
    assert "chat.done" in ops


def test_show_on_map_resolves_pin_id_from_map_state() -> None:
    emitted: list[tuple[str, dict]] = []

    def emit(op: str, **payload: object) -> None:
        emitted.append((op, payload))

    ctx = WebChatContext(
        project_slug="nevada",
        emit=emit,
        map_pins=[
            {
                "pin_id": "baf9c0cfa4c4",
                "label": "Charleston Peak",
                "lat": 36.2716284,
                "lon": -115.6954918,
            }
        ],
    )
    viewshed_rec = {
        "slug": "at-x",
        "lat": 36.271628,
        "lon": -115.695492,
        "url": "/u",
        "coordinates": [[-116, 37], [-115, 37], [-115, 36], [-116, 36]],
        "bounds": [-116.0, 36.0, -115.0, 37.0],
    }
    with patch.object(
        WebChatContext,
        "preset_path",
        new_callable=PropertyMock,
        return_value=Path("/projects/nevada/config.yaml"),
    ), patch(
        "peaky_finders.web.chat_tools.ensure_chat_site_in_preset",
        return_value="chat-baf9c0cfa4c4",
    ), patch(
        "peaky_finders.web.chat_tools.ensure_point_viewshed",
        return_value=viewshed_rec,
    ) as ensure:
        result = dispatch_web_tool(
            "show_on_map",
            {"pin_id": "baf9c0cfa4c4"},
            ctx=ctx,
        )

    assert result["ok"] is True
    assert result["lat"] == 36.271628
    assert result["lon"] == -115.695492
    ensure.assert_called_once_with(project_slug="nevada", lat=36.271628, lon=-115.695492)
    viewshed_emits = [payload for op, payload in emitted if op == "map.viewshed"]
    assert len(viewshed_emits) == 1
    assert viewshed_emits[0]["slug"] == viewshed_rec["slug"]
    assert viewshed_emits[0]["lat"] == 36.271628
    assert emitted[-1] == ("map.fit_bounds", {"bbox": viewshed_rec["bounds"]})


def test_show_on_map_unknown_pin_id_errors() -> None:
    ctx = WebChatContext(project_slug="nevada", map_pins=[])
    result = dispatch_web_tool("show_on_map", {"pin_id": "missing"}, ctx=ctx)
    assert "unknown pin_id" in result["error"]


def test_normalize_map_pins_dedupes() -> None:
    pins = normalize_map_pins(
        [
            {"pin_id": "a", "label": "One", "lat": 1.0, "lon": 2.0},
            {"pin_id": "a", "label": "One", "lat": 1.0, "lon": 2.0},
        ]
    )
    assert len(pins) == 1
    assert lookup_map_pin(pins, "a")["label"] == "One"


def test_stream_web_chat_cancelled() -> None:
    cancelled = False

    def should_cancel() -> bool:
        return cancelled

    def fake_turn(**kwargs: object):
        yield "delta", "partial "
        nonlocal cancelled
        cancelled = True
        yield "delta", "ignored"

    with patch("peaky_finders.web.chat_agent.ollama_health_ok", return_value=True), patch(
        "peaky_finders.web.chat_agent.resolve_ollama_config",
    ) as cfg, patch(
        "peaky_finders.web.chat_agent.stream_chat_completions_turn",
        side_effect=lambda **kwargs: fake_turn(**kwargs),
    ):
        cfg.return_value.ollama_base_url = "http://ollama"
        cfg.return_value.ollama_model = "test"
        cfg.return_value.temperature = 0.2
        events = list(
            stream_web_chat(
                "hello",
                project_slug="nevada",
                should_cancel=should_cancel,
            )
        )

    ops = [e["op"] for e in events]
    assert "chat.cancelled" in ops
    assert "chat.done" not in ops
    assert any(e.get("op") == "chat.delta" and e.get("text") == "partial " for e in events)
