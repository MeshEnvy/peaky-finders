"""Web project discovery and context API."""

from __future__ import annotations

from pathlib import Path

import yaml

from peaky_finders.web.projects import project_context

from fixture_paths import PEAKY_TEST_HOME


def test_project_context_includes_site_name(monkeypatch) -> None:
    monkeypatch.setenv("PEAKY_HOME", str(PEAKY_TEST_HOME))
    ctx = project_context("sample")
    by_slug = {s["slug"]: s for s in ctx["sites"]}
    assert by_slug["hub"]["name"] == "Hub Site"
    assert by_slug["peer-a"]["name"] == "Peer A"


def test_project_context_goal_labels_default_to_key(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "peaky_home"
    proj = home / "projects" / "goals"
    proj.mkdir(parents=True)
    cfg = proj / "config.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {
                "simulation": {"provider": "los", "radius_km": 5.0},
                "display": {},
                "bundle": {"inputs_root": "data", "aoi": [], "include": [], "exclude": []},
                "sites": {
                    "vegas": {"type": "goal", "name": "Las Vegas", "loc": [36.17, -115.14]},
                    "hub": {"name": "Hub", "loc": [36.5, -115.5]},
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PEAKY_HOME", str(home))
    ctx = project_context("goals")
    by_key = {g["key"]: g for g in ctx["goals"]}
    assert by_key["vegas"]["label"] == "Las Vegas"
