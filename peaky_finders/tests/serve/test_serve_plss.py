"""``peaky serve`` PLSS coordinate prefetch."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from peaky_finders.core.project.scaffold import scaffold_project
from peaky_finders.core.plss.fetch import loc_stamp, read_plss_loc_cache, write_plss_loc_cache
from peaky_finders.serve.plss import (
    apply_plss_from_loc_cache,
    ensure_plss_for_coords,
)
from peaky_finders.core.preset import load_preset_sites, resolved_preset_cache_dir


def test_ensure_plss_for_coords_cache_hit(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo"
    scaffold_project("demo", parent=tmp_path, here=False)
    cache_base = resolved_preset_cache_dir(project_dir / "config.yaml")
    lat, lon = 39.6, -119.4
    write_plss_loc_cache(
        cache_base,
        {loc_stamp(lat, lon): {"plss": "NV210300N0230E0SN360ASENW"}},
    )

    with patch("peaky_finders.serve.plss.plss_for_point") as mock_fetch:
        result = ensure_plss_for_coords(project_dir, lat, lon)

    assert result == {"plss": "NV210300N0230E0SN360ASENW"}
    mock_fetch.assert_not_called()


def test_ensure_plss_for_coords_network_miss(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project_dir = tmp_path / "demo"
    scaffold_project("demo", parent=tmp_path, here=False)
    lat, lon = 39.6, -119.4

    def fake_plss(_lon: float, _lat: float, **_: object) -> str:
        return "NV210300N0230E0SN360ASENW"

    monkeypatch.setattr("peaky_finders.serve.plss.plss_for_point", fake_plss)
    result = ensure_plss_for_coords(project_dir, lat, lon)

    assert result["plss"] == "NV210300N0230E0SN360ASENW"
    cache_base = resolved_preset_cache_dir(project_dir / "config.yaml")
    cached = read_plss_loc_cache(cache_base)
    assert cached[loc_stamp(lat, lon)]["plss"] == "NV210300N0230E0SN360ASENW"


def test_apply_plss_from_loc_cache_writes_yaml(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo"
    scaffold_project("demo", parent=tmp_path, here=False)
    preset_path = project_dir / "config.yaml"
    cache_base = resolved_preset_cache_dir(preset_path)
    lat, lon = 39.6, -119.4
    write_plss_loc_cache(
        cache_base,
        {loc_stamp(lat, lon): {"plss": "NV210300N0230E0SN360ASENW"}},
    )

    plss = apply_plss_from_loc_cache(preset_path, "hub", lat, lon)
    assert plss == "NV210300N0230E0SN360ASENW"

    sites = load_preset_sites(preset_path)
    assert sites["hub"].plss == "NV210300N0230E0SN360ASENW"
