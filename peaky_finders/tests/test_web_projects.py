"""Web project discovery and context API."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import yaml

from peaky_finders.web.projects import enrich_all_project_sites, project_context

from fixture_paths import PEAKY_TEST_HOME


def test_project_context_enriches_all_sites(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PEAKY_HOME", str(tmp_path))
    proj = tmp_path / "projects" / "sample"
    import shutil

    shutil.copytree(PEAKY_TEST_HOME / "projects" / "sample", proj)
    with patch("peaky_finders.web.projects.enrich_all_preset_sites", return_value=(0, 4)) as enrich:
        ctx = project_context("sample")
    enrich.assert_called_once()
    assert ctx["slug"] == "sample"


def test_project_context_includes_site_name(monkeypatch) -> None:
    monkeypatch.setenv("PEAKY_HOME", str(PEAKY_TEST_HOME))
    ctx = project_context("sample")
    by_slug = {s["slug"]: s for s in ctx["sites"]}
    assert by_slug["hub"]["name"] == "Hub Site"
    assert by_slug["peer-a"]["name"] == "Peer A"


def test_project_context_includes_full_site_detail(monkeypatch) -> None:
    monkeypatch.setenv("PEAKY_HOME", str(PEAKY_TEST_HOME))
    ctx = project_context("sample")
    hub = next(s for s in ctx["sites"] if s["slug"] == "hub")
    assert set(hub["sees"]) == {"peer-a", "peer-b", "peer-c"}
    assert set(hub["sees_mutual"]) == {"peer-a", "peer-b", "peer-c"}
    assert hub["sees_pending"] == []
    assert "peer_slugs" in hub
    assert "rf_peers" in hub
    assert hub["elevation_m"] == 1454.0
    assert "plss" in hub
    assert "participates_in_rf" in hub


def test_enrich_all_project_sites_skips_invalid_presets(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PEAKY_HOME", str(tmp_path))
    root = tmp_path / "projects"
    good = root / "good"
    good.mkdir(parents=True)
    (good / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "simulation": {"provider": "los", "radius_km": 5.0},
                "display": {},
                "bundle": {"inputs_root": "data", "aoi": [], "include": [], "exclude": []},
                "sites": {"a": {"loc": [36.0, -115.0]}},
            }
        ),
        encoding="utf-8",
    )
    bad = root / "bad"
    bad.mkdir(parents=True)
    (bad / "config.yaml").write_text("not: valid: preset: [", encoding="utf-8")
    empty = root / "empty"
    empty.mkdir(parents=True)

    with patch("peaky_finders.web.projects.enrich_all_preset_sites") as enrich:
        def _enrich(cfg: Path, **kwargs):
            if cfg.parent.name == "bad":
                raise ValueError("invalid preset")
            return (0, 1)

        enrich.side_effect = _enrich
        processed, network, updated = enrich_all_project_sites(allow_network_plss=False)
    assert processed == 1
    assert network == 0
    assert updated == 1
    assert enrich.call_count == 2


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
