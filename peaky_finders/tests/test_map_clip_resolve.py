"""Resolve legacy clip paths for map GeoJSON."""

from __future__ import annotations

from pathlib import Path

import pytest

from peaky_finders.sites_job import load_preset, peaky_projects_dir
from peaky_finders.web.project_maps import map_entry_geojson


def test_nevada_state_boundary_geojson_from_legacy_clip(monkeypatch) -> None:
    monkeypatch.setenv("PEAKY_HOME", str(Path(__file__).resolve().parents[2]))
    preset_path = peaky_projects_dir() / "nevada" / "config.yaml"
    if not preset_path.is_file():
        pytest.skip("nevada project not present")
    clips_aoi = preset_path.parent / "build" / "clips" / "layer_jobs" / "aoi"
    if not clips_aoi.is_dir():
        pytest.skip("nevada clips not built")

    gj = map_entry_geojson("nevada", "nevada_state_boundary")
    assert gj["type"] == "FeatureCollection"
    assert len(gj["features"]) >= 1


def test_map_visibility_patch_roundtrip(monkeypatch, tmp_path: Path) -> None:
    from fixture_paths import PEAKY_TEST_HOME

    monkeypatch.setenv("PEAKY_HOME", str(PEAKY_TEST_HOME))
    from peaky_finders.web.project_maps import patch_map_entry

    out = patch_map_entry("sample", "test_aoi", {"visible": True})
    assert out["visible"] is True
    preset = load_preset(PEAKY_TEST_HOME / "projects" / "sample" / "config.yaml")
    entry = next(m for m in preset.maps if m.id == "test_aoi")
    assert entry.visible is True
    patch_map_entry("sample", "test_aoi", {"visible": False})
