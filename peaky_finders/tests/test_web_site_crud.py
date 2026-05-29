"""Web site CRUD API."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import yaml
from fastapi.testclient import TestClient

from peaky_finders.sites_job import load_preset
from peaky_finders.web.app import create_app

from fixture_paths import PEAKY_TEST_HOME


def _client(monkeypatch, home: Path | None = None) -> TestClient:
    monkeypatch.setenv("PEAKY_HOME", str(home or PEAKY_TEST_HOME))
    return TestClient(create_app())


def test_get_site_detail_includes_helpers(monkeypatch) -> None:
    client = _client(monkeypatch)
    res = client.get("/api/projects/sample/sites/hub")
    assert res.status_code == 200
    body = res.json()
    assert body["slug"] == "hub"
    assert body["name"] == "Hub Site"
    assert set(body["sees"]) == {"peer-a", "peer-b", "peer-c"}
    assert set(body["sees_mutual"]) == {"peer-a", "peer-b", "peer-c"}
    assert body["sees_pending"] == []
    assert "peer_slugs" in body
    assert "rf_peers" in body


def test_patch_loc_change_triggers_metadata_resolve(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "peaky_home"
    src = PEAKY_TEST_HOME / "projects" / "sample"
    dst = home / "projects" / "sample"
    dst.parent.mkdir(parents=True)
    import shutil

    shutil.copytree(src, dst)
    client = _client(monkeypatch, home)

    with patch("peaky_finders.web.projects.resolve_site_metadata") as resolve:
        res = client.patch("/api/projects/sample/sites/hub", json={"lat": 40.5})
        assert res.status_code == 200
        resolve.assert_called_once()
        assert resolve.call_args.kwargs["force"] is True


def test_patch_site_name_persists(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "peaky_home"
    src = PEAKY_TEST_HOME / "projects" / "sample"
    dst = home / "projects" / "sample"
    dst.parent.mkdir(parents=True)
    import shutil

    shutil.copytree(src, dst)
    client = _client(monkeypatch, home)

    res = client.patch("/api/projects/sample/sites/peer-a", json={"name": "Peer Alpha"})
    assert res.status_code == 200
    assert res.json()["name"] == "Peer Alpha"

    preset = load_preset(dst / "config.yaml")
    assert preset.sites["peer-a"].name == "Peer Alpha"


def test_patch_slug_rekeys_and_rewrites_sees(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "peaky_home"
    src = PEAKY_TEST_HOME / "projects" / "sample"
    dst = home / "projects" / "sample"
    dst.parent.mkdir(parents=True)
    import shutil

    shutil.copytree(src, dst)
    client = _client(monkeypatch, home)

    res = client.patch("/api/projects/sample/sites/peer-a", json={"new_slug": "peer-alpha"})
    assert res.status_code == 200
    assert res.json()["slug"] == "peer-alpha"

    preset = load_preset(dst / "config.yaml")
    assert "peer-alpha" in preset.sites
    assert "peer-a" not in preset.sites
    assert "peer-alpha" in preset.sites["hub"].sees


def test_patch_type_goal_clears_sees(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "peaky_home"
    proj = home / "projects" / "goals"
    proj.mkdir(parents=True)
    cfg = proj / "config.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {
                "simulation": {
                    "provider": "los",
                    "radius_km": 5.0,
                    "modem_presets": {
                        "m": {
                            "frequency_mhz": 910.0,
                            "bandwidth_khz": 62.5,
                            "spreading_factor": 7,
                            "coding_rate": 5,
                            "implementation_margin_db": 3.0,
                            "power_dbm": 22.0,
                            "sensitivity_dbm": -121.0,
                        }
                    },
                    "environment_presets": {"e": {"climate": "desert", "polarization": "vertical", "clutter_height_m": 1.0}},
                    "modem": "m",
                    "environment": "e",
                    "transmitter": {"height_m": 2.0, "gain_dbi": 3.0, "loss_db": 2.0},
                    "receiver": {"height_m": 2.0, "gain_dbi": 3.0, "loss_db": 2.0},
                },
                "display": {},
                "bundle": {"inputs_root": "data", "aoi": [], "include": [], "exclude": []},
                "sites": {
                    "hub": {"name": "Hub", "loc": [36.5, -115.5], "sees": ["peer"]},
                    "peer": {"name": "Peer", "loc": [36.6, -115.4], "sees": ["hub"]},
                },
            }
        ),
        encoding="utf-8",
    )
    client = _client(monkeypatch, home)

    res = client.patch("/api/projects/goals/sites/hub", json={"type": "goal"})
    assert res.status_code == 200
    assert res.json()["type"] == "goal"
    assert res.json()["sees"] == []

    preset = load_preset(cfg)
    assert preset.sites["hub"].type.value == "goal"
    assert preset.sites["hub"].sees == []


def test_delete_site_scrubs_sees_and_rejects_last(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "peaky_home"
    src = PEAKY_TEST_HOME / "projects" / "sample"
    dst = home / "projects" / "sample"
    dst.parent.mkdir(parents=True)
    import shutil

    shutil.copytree(src, dst)
    client = _client(monkeypatch, home)

    res = client.delete("/api/projects/sample/sites/peer-c")
    assert res.status_code == 200

    preset = load_preset(dst / "config.yaml")
    assert "peer-c" not in preset.sites
    assert "peer-c" not in preset.sites["hub"].sees

    for slug in ("peer-b", "peer-a"):
        assert client.delete(f"/api/projects/sample/sites/{slug}").status_code == 200

    last = client.delete("/api/projects/sample/sites/hub")
    assert last.status_code == 409


def test_site_not_found(monkeypatch) -> None:
    client = _client(monkeypatch)
    res = client.get("/api/projects/sample/sites/missing-site")
    assert res.status_code == 404


def test_patch_requires_field(monkeypatch) -> None:
    client = _client(monkeypatch)
    res = client.patch("/api/projects/sample/sites/hub", json={})
    assert res.status_code == 400
