"""``peaky serve`` PLSS/MLRS coordinate prefetch."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from peaky_finders.new_cli import scaffold_project
from peaky_finders.plss_mlrs_fetch import loc_stamp, read_plss_mlrs_loc_cache, write_plss_mlrs_loc_cache
from peaky_finders.serve_plss_mlrs import (
    apply_plss_mlrs_from_loc_cache,
    ensure_plss_mlrs_for_coords,
)
from peaky_finders.sites_job import load_preset_sites, resolved_preset_build_dir


def test_ensure_plss_mlrs_for_coords_cache_hit(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo"
    scaffold_project("demo", parent=tmp_path, here=False)
    cache_base = resolved_preset_build_dir(project_dir / "config.yaml")
    lat, lon = 39.6, -119.4
    write_plss_mlrs_loc_cache(
        cache_base,
        {loc_stamp(lat, lon): {"plss": "NV; Sec. 1", "mlrs": "NV123"}},
    )

    with patch("peaky_finders.serve_plss_mlrs.plss_mlrs_for_point") as mock_fetch:
        result = ensure_plss_mlrs_for_coords(project_dir, lat, lon)

    assert result == {"plss": "NV; Sec. 1", "mlrs": "NV123"}
    mock_fetch.assert_not_called()


def test_ensure_plss_mlrs_for_coords_network_miss(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project_dir = tmp_path / "demo"
    scaffold_project("demo", parent=tmp_path, here=False)
    lat, lon = 39.6, -119.4

    def fake_plss(_lon: float, _lat: float, **_: object) -> tuple[str, str]:
        return "NV; T.30N R.23E", "NV210300N0230E0SN360"

    monkeypatch.setattr("peaky_finders.serve_plss_mlrs.plss_mlrs_for_point", fake_plss)
    result = ensure_plss_mlrs_for_coords(project_dir, lat, lon)

    assert result["plss"] == "NV; T.30N R.23E"
    assert result["mlrs"] == "NV210300N0230E0SN360"
    cache_base = resolved_preset_build_dir(project_dir / "config.yaml")
    cached = read_plss_mlrs_loc_cache(cache_base)
    assert cached[loc_stamp(lat, lon)]["plss"] == "NV; T.30N R.23E"


def test_apply_plss_mlrs_from_loc_cache_writes_yaml(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo"
    scaffold_project("demo", parent=tmp_path, here=False)
    preset_path = project_dir / "config.yaml"
    cache_base = resolved_preset_build_dir(preset_path)
    lat, lon = 39.6, -119.4
    write_plss_mlrs_loc_cache(
        cache_base,
        {loc_stamp(lat, lon): {"plss": "NV; Sec. 1", "mlrs": "NV123"}},
    )

    plss, mlrs = apply_plss_mlrs_from_loc_cache(preset_path, "hub", lat, lon)
    assert plss == "NV; Sec. 1"
    assert mlrs == "NV123"

    sites = load_preset_sites(preset_path)
    assert sites["hub"].plss == "NV; Sec. 1"
    assert sites["hub"].mlrs == "NV123"
