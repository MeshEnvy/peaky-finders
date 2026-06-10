"""``peaky serve`` site append helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from peaky_finders.new_cli import scaffold_project
from peaky_finders.serve_sites import append_planned_site_to_preset, delete_site_from_preset, unique_site_slug, update_site_in_preset
from peaky_finders.sites_job import load_preset, write_preset_document


def test_unique_site_slug_dedupes() -> None:
    existing = {"russell-peak", "russell-peak-2"}
    assert unique_site_slug(existing, "Russell Peak") == "russell-peak-3"


def test_unique_site_slug_from_name() -> None:
    assert unique_site_slug(set(), "Davidson Peak") == "davidson-peak"


def test_append_planned_site_to_preset(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    scaffold_project("demo", parent=projects_dir)
    preset_path = projects_dir / "demo" / "config.yaml"
    slug = append_planned_site_to_preset(
        preset_path,
        name="New Peak",
        lat=39.6,
        lon=-119.4,
    )
    assert slug == "new-peak"
    preset = load_preset(preset_path)
    entry = preset.sites["new-peak"]
    assert entry.name == "New Peak"
    assert entry.type.value == "planned"
    assert entry.lat == 39.6
    assert entry.lon == -119.4


def test_append_planned_site_rejects_empty_name(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    scaffold_project("demo", parent=projects_dir)
    preset_path = projects_dir / "demo" / "config.yaml"
    with pytest.raises(ValueError, match="name is required"):
        append_planned_site_to_preset(preset_path, name="  ", lat=39.5, lon=-119.5)


def test_delete_site_from_preset(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    scaffold_project("demo", parent=projects_dir)
    preset_path = projects_dir / "demo" / "config.yaml"
    slug = append_planned_site_to_preset(
        preset_path,
        name="New Peak",
        lat=39.6,
        lon=-119.4,
    )
    assert delete_site_from_preset(preset_path, slug) == slug
    preset = load_preset(preset_path)
    assert slug not in preset.sites


def test_delete_site_scrubs_manual_links(tmp_path: Path) -> None:
    preset_path = tmp_path / "config.yaml"
    write_preset_document(
        preset_path,
        {
            "simulation": {
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
                "environment_presets": {
                    "e": {
                        "climate": "desert",
                        "polarization": "vertical",
                        "clutter_height_m": 1.0,
                    }
                },
                "modem": "m",
                "environment": "e",
                "transmitter": {"height_m": 2.0, "gain_dbi": 2.0, "loss_db": 0.0},
                "receiver": {"height_m": 2.0, "gain_dbi": 2.0, "loss_db": 0.0},
            },
            "display": {"colormap": "rainbow", "min_dbm": -130.0, "max_dbm": -80.0},
            "sites": {
                "alpha": {"name": "Alpha", "loc": [39.5, -119.5]},
                "beta": {"name": "Beta", "loc": [39.6, -119.4]},
                "gamma": {"name": "Gamma", "loc": [39.7, -119.3]},
            },
            "links": [["alpha", "beta"], ["beta", "gamma"], ["alpha", "gamma"]],
        },
    )
    delete_site_from_preset(preset_path, "beta")
    preset = load_preset(preset_path)
    assert "beta" not in preset.sites
    assert preset.links == [("alpha", "gamma")]


def test_delete_site_missing_raises(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    scaffold_project("demo", parent=projects_dir)
    preset_path = projects_dir / "demo" / "config.yaml"
    with pytest.raises(ValueError, match="not found"):
        delete_site_from_preset(preset_path, "missing")


def test_update_site_in_preset(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    scaffold_project("demo", parent=projects_dir)
    preset_path = projects_dir / "demo" / "config.yaml"
    slug = append_planned_site_to_preset(
        preset_path,
        name="New Peak",
        lat=39.6,
        lon=-119.4,
    )
    update_site_in_preset(
        preset_path,
        slug,
        name="Renamed Peak",
        lat=39.61,
        lon=-119.41,
        site_type="installed",
    )
    preset = load_preset(preset_path)
    entry = preset.sites[slug]
    assert entry.name == "Renamed Peak"
    assert entry.lat == 39.61
    assert entry.lon == -119.41
    assert entry.type.value == "installed"
