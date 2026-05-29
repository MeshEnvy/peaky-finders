"""Mock mesh-grow-ai agent loop."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from shapely.geometry import box

from peaky_finders.site_suggestions.context import SiteSuggestionContext
from peaky_finders.site_suggestions.depth_grid import build_coverage_depth_grid
from peaky_finders.site_suggestions.providers.mesh_grow_ai.agent import run_mesh_grow_agent
from peaky_finders.sites_job import load_preset, write_preset_document

_SIM = {
    "provider": "los",
    "radius_km": 60.0,
    "modem_presets": {
        "m": {
            "frequency_mhz": 910.525,
            "bandwidth_khz": 62.5,
            "spreading_factor": 7,
            "coding_rate": 5,
            "implementation_margin_db": 3.0,
            "power_dbm": 22.0,
            "sensitivity_dbm": -121.0,
        }
    },
    "environment_presets": {"e": {"climate": "desert", "polarization": "vertical", "clutter_height_m": 1.0}},
    "modem": "m",
    "environment": "e",
    "transmitter": {"height_m": 2.0, "gain_dbi": 2.0, "loss_db": 0.0},
    "receiver": {"height_m": 2.0, "gain_dbi": 2.0, "loss_db": 0.0},
}


def _mock_chat(**kwargs):
    messages = kwargs["messages"]
    last_user = next((m for m in reversed(messages) if m.get("role") == "user"), None)
    if last_user and "Start with snapshot" in str(last_user.get("content", "")):
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "c1",
                                "function": {"name": "snapshot", "arguments": "{}"},
                            }
                        ],
                    }
                }
            ]
        }
    if any(m.get("role") == "tool" and m.get("name") == "snapshot" for m in messages):
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "c2",
                                "function": {
                                    "name": "submit_proposals",
                                    "arguments": "{}",
                                },
                            }
                        ],
                    }
                }
            ]
        }
    return {"choices": [{"message": {"role": "assistant", "content": "done"}}]}


def test_mock_agent_loop(tmp_path: Path) -> None:
    eligible = box(-120.0, 35.0, -114.0, 42.0)
    grid = build_coverage_depth_grid(
        aoi_ll=eligible,
        target_ll=eligible,
        footprint_gpkg_paths=[],
        max_raster_dimension=64,
    )
    preset_path = tmp_path / "job.yaml"
    write_preset_document(
        preset_path,
        {
            "simulation": dict(_SIM),
            "display": {"colormap": "rainbow", "min_dbm": -130.0, "max_dbm": -80.0},
            "sites": {"seed": {"name": "Seed", "loc": [39.0, -117.0]}},
            "bundle": {
                "site_suggestions": {
                    "strategy": "mesh-grow-ai",
                    "mesh_grow_ai": {"goals": {"g1": {"loc": [39.5, -116.5]}}},
                },
            },
        },
    )
    preset = load_preset(preset_path)
    cfg = preset.bundle.site_suggestions
    ctx = SiteSuggestionContext(
        preset=preset,
        plan=type("Plan", (), {"viewsheds_root": tmp_path / "vs"})(),
        grid=grid,
        eligible_ll=eligible,
        aoi_ll=eligible,
        target_ll=eligible,
        suggest_root=tmp_path / "suggest",
        cfg=cfg,
        dem_mirror_root=tmp_path / "dem",
        eligible_sha="x",
        jobs=1,
        verbose=False,
    )
    emitted: list[tuple[str, dict]] = []

    def emit(op, **kw):
        emitted.append((op, kw))

    with patch(
        "peaky_finders.site_suggestions.providers.mesh_grow_ai.agent.ollama_health_ok",
        return_value=True,
    ):
        candidates = run_mesh_grow_agent(
            ctx,
            preset_path=preset_path,
            iteration=0,
            emit=emit,
            chat_client=_mock_chat,
        )
    assert candidates == []
    assert any(op == "chat.tool_call" for op, _ in emitted)
