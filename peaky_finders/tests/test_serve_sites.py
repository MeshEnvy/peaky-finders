"""``peaky serve`` site append helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from peaky_finders.new_cli import scaffold_project
from peaky_finders.serve_sites import append_planned_site_to_preset, unique_site_slug
from peaky_finders.sites_job import load_preset


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
