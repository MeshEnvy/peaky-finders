"""``peaky serve`` site append helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from rf_fixtures import MINIMAL_SIMULATION
from peaky_finders.core.project.scaffold import scaffold_project
from peaky_finders.serve.sites import append_planned_site_to_preset, delete_site_from_preset, unique_site_slug, update_site_in_preset
from peaky_finders.core.preset import load_preset, write_preset_document


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
            "simulation": dict(MINIMAL_SIMULATION),
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
        tags=["installed"],
    )
    preset = load_preset(preset_path)
    entry = preset.sites[slug]
    assert entry.name == "Renamed Peak"
    assert entry.lat == 39.61
    assert entry.lon == -119.41
    assert entry.tags == ["installed"]


def test_update_site_tags(tmp_path: Path) -> None:
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
    update_site_in_preset(preset_path, slug, tags=["eip", "HSC", "eip"])
    preset = load_preset(preset_path)
    assert preset.sites[slug].tags == ["eip", "hsc"]
    update_site_in_preset(preset_path, slug, tags=[])
    preset = load_preset(preset_path)
    assert preset.sites[slug].tags == []
    assert "tags" not in preset_path.read_text()


def test_update_site_rejects_type_key(tmp_path: Path) -> None:
    preset_path = tmp_path / "config.yaml"
    write_preset_document(
        preset_path,
        {
            "simulation": dict(MINIMAL_SIMULATION),
            "display": {"colormap": "rainbow", "min_dbm": -130.0, "max_dbm": -80.0},
            "sites": {
                "peak": {"type": "planned", "name": "Peak", "loc": [39.6, -119.4]},
            },
        },
    )
    with pytest.raises(ValueError, match="type is removed"):
        update_site_in_preset(preset_path, "peak", name="Renamed")


def test_load_preset_rejects_type_key(tmp_path: Path) -> None:
    preset_path = tmp_path / "config.yaml"
    write_preset_document(
        preset_path,
        {
            "simulation": dict(MINIMAL_SIMULATION),
            "display": {"colormap": "rainbow", "min_dbm": -130.0, "max_dbm": -80.0},
            "sites": {
                "hub": {"type": "planned", "name": "Hub", "loc": [39.5, -119.5]},
            },
        },
    )
    with pytest.raises(ValueError, match="type is removed"):
        load_preset(preset_path)


def test_normalize_site_tags_rejects_invalid() -> None:
    from peaky_finders.core.preset import normalize_site_tags

    with pytest.raises(ValueError, match="invalid tag"):
        normalize_site_tags(["Bad Tag!"])
