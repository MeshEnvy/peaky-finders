"""Global ``$PEAKY_HOME/config.yaml`` defaults merge and prune."""

from __future__ import annotations

from pathlib import Path

import pytest

from peaky_finders.core.home.bundled_templates import ensure_peaky_home
from peaky_finders.core.home.preset_defaults import (
    deep_merge_preset_dict,
    load_preset_defaults,
    prune_project_preset_dict,
    resolve_preset_raw,
)
from peaky_finders.core.preset import load_preset, write_preset_document
from rf_fixtures import MINIMAL_SIMULATION


def test_ensure_peaky_home_seeds_templates_at_home_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PEAKY_HOME", str(tmp_path))
    ensure_peaky_home()
    assert (tmp_path / "config.yaml").is_file()
    assert (tmp_path / "modems.yaml").is_file()
    assert (tmp_path / "environments.yaml").is_file()
    assert (tmp_path / "projects").is_dir()


def test_load_preset_merges_home_defaults(peaky_test_home: Path, tmp_path: Path) -> None:
    defaults = load_preset_defaults()
    preset_path = tmp_path / "config.yaml"
    write_preset_document(
        preset_path,
        {
            "simulation": {"radius_km": 61.0},
            "display": {"colormap": "rainbow", "min_dbm": -130.0, "max_dbm": -80.0},
            "sites": {"hub": {"name": "Hub", "loc": [39.5, -119.5]}},
        },
    )
    preset = load_preset(preset_path)
    assert float(preset.simulation.radius_km) == 61.0
    assert preset.simulation.modem == defaults["simulation"]["modem"]


def test_prune_drops_default_equal_keys() -> None:
    defaults = {"simulation": {"radius_km": 50.0, "modem": "meshcore-us"}}
    project = {
        "simulation": {"radius_km": 61.0, "modem": "meshcore-us"},
        "sites": {"hub": {"name": "Hub", "loc": [39.0, -119.0]}},
    }
    pruned = prune_project_preset_dict(project, defaults)
    assert pruned == {
        "simulation": {"radius_km": 61.0},
        "sites": project["sites"],
    }


def test_update_preset_yaml_tree_prunes_after_write(tmp_path: Path, peaky_test_home: Path) -> None:
    from peaky_finders.serve.simulation import update_viewshed_sim_to_preset

    preset_path = tmp_path / "config.yaml"
    defaults = load_preset_defaults()
    default_radius = float(defaults["simulation"]["radius_km"])
    write_preset_document(
        preset_path,
        {
            "simulation": dict(MINIMAL_SIMULATION) | {"radius_km": default_radius},
            "display": {"colormap": "rainbow", "min_dbm": -130.0, "max_dbm": -80.0},
            "sites": {"hub": {"name": "Hub", "loc": [39.5, -119.5]}},
        },
    )
    update_viewshed_sim_to_preset(
        preset_path,
        radius_km=default_radius,
        raster_dimension=int(defaults["simulation"]["raster_dimension"]),
    )
    raw = preset_path.read_text(encoding="utf-8")
    assert "radius_km" not in raw
    preset = load_preset(preset_path)
    assert float(preset.simulation.radius_km) == default_radius


def test_resolve_preset_raw_deep_merges_nested() -> None:
    merged = resolve_preset_raw(
        {
            "simulation": {"radius_km": 61.0},
            "sites": {"hub": {"name": "Hub", "loc": [39.5, -119.5]}},
        }
    )
    assert float(merged["simulation"]["radius_km"]) == 61.0
    assert "modem" in merged["simulation"]
    assert deep_merge_preset_dict({"a": {"b": 1, "c": 2}}, {"a": {"c": 3}}) == {"a": {"b": 1, "c": 3}}
