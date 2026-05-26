"""Tests for splatter batch request assembly."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fixture_paths import SAMPLE_PROJECT_CONFIG
from peaky_finders.sites_job import load_preset
from peaky_finders.viewshed_batch import resolved_splatter_batch_jobs, write_batch_request_json


def test_resolved_splatter_batch_jobs(peaky_test_home: Path) -> None:
    preset = load_preset(SAMPLE_PROJECT_CONFIG)
    assert resolved_splatter_batch_jobs(preset=preset, workspace_count=8) == 1
    assert resolved_splatter_batch_jobs(preset=preset, workspace_count=8, build_jobs=8) == 8
    assert resolved_splatter_batch_jobs(preset=preset, workspace_count=3, build_jobs=8) == 3


def test_write_batch_request_json(tmp_path: Path) -> None:
    ws_a = tmp_path / "a"
    ws_b = tmp_path / "b"
    ws_a.mkdir()
    ws_b.mkdir()
    req_a = {
        "lat": 39.1,
        "lon": -119.8,
        "tx_height": 2.0,
        "tx_power": 10.0,
        "tx_gain": 2.0,
        "frequency_mhz": 911.525,
        "rx_height": 2.0,
        "rx_gain": 2.0,
        "signal_threshold": -112.0,
        "clutter_height": 1.0,
        "radius": 50000.0,
        "situation_fraction": 95.0,
        "time_fraction": 95.0,
        "modem": {
            "spreading_factor": 9,
            "bandwidth_khz": 125.0,
            "coding_rate": 5,
            "implementation_margin_db": 0.0,
        },
    }
    req_b = {**req_a, "lat": 38.2, "lon": -117.1}
    (ws_a / "request.json").write_text(json.dumps(req_a), encoding="utf-8")
    (ws_b / "request.json").write_text(json.dumps(req_b), encoding="utf-8")

    from peaky_finders.build_configure import PlannedViewshedWorkspace

    workspaces = (
        PlannedViewshedWorkspace(
            digest="aaa",
            workdir=ws_a,
            output_ppm=ws_a / "output.ppm",
            splat_png=ws_a / "splat.png",
            splat_gpkg=ws_a / "splat.gpkg",
            request_json=ws_a / "request.json",
            site_slugs=("site-a",),
        ),
        PlannedViewshedWorkspace(
            digest="bbb",
            workdir=ws_b,
            output_ppm=ws_b / "output.ppm",
            splat_png=ws_b / "splat.png",
            splat_gpkg=ws_b / "splat.gpkg",
            request_json=ws_b / "request.json",
            site_slugs=("site-b",),
        ),
    )

    out = write_batch_request_json(viewshed_root=tmp_path / "viewsheds", workspaces=workspaces)
    assert out.name == "request.json"
    parsed = json.loads(out.read_text(encoding="utf-8"))
    assert isinstance(parsed, list)
    assert len(parsed) == 2
    assert parsed[0]["lat"] == pytest.approx(39.1)
    assert parsed[1]["lat"] == pytest.approx(38.2)
