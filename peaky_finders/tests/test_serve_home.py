"""``peaky serve`` global ``$PEAKY_HOME`` settings APIs."""

from __future__ import annotations

import json
import shutil
import threading
from http.client import HTTPConnection
from pathlib import Path

import pytest

from peaky_finders.peaky_preset_defaults import load_home_simulation, update_home_simulation
from peaky_finders.peaky_profiles import (
    delete_modem_preset,
    load_modem_presets_catalog,
    upsert_modem_preset,
)
from peaky_finders.serve_app import make_serve_wsgi_app
from fixture_paths import PEAKY_TEST_HOME


def _setup_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    home = tmp_path / "home"
    shutil.copytree(PEAKY_TEST_HOME, home)
    monkeypatch.setenv("PEAKY_HOME", str(home))
    projects_dir = home / "projects"
    return home, projects_dir


def _start_server(projects_dir: Path):
    from wsgiref.simple_server import make_server

    app = make_serve_wsgi_app(projects_dir, verbose=False, request_log=False)
    server = make_server("127.0.0.1", 0, app)
    host, port = server.server_address[:2]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, host, port, thread


def test_update_home_simulation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, _projects = _setup_home(tmp_path, monkeypatch)
    before = load_home_simulation()
    assert float(before.radius_km) == 50.0

    written = update_home_simulation({"radius_km": 44.0, "raster_dimension": 512})
    assert written["radius_km"] == 44.0
    assert written["raster_dimension"] == 512

    after = load_home_simulation()
    assert float(after.radius_km) == 44.0
    assert after.raster_dimension == 512
    assert (home / "config.yaml").is_file()


def test_upsert_modem_preset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _setup_home(tmp_path, monkeypatch)
    catalog = load_modem_presets_catalog()
    assert "fixture-modem" in catalog

    upsert_modem_preset(
        "lab-modem",
        {
            "frequency_mhz": 915.0,
            "bandwidth_khz": 125.0,
            "spreading_factor": 8,
            "coding_rate": 5,
            "implementation_margin_db": 2.0,
            "power_dbm": 20.0,
            "sensitivity_dbm": -120.0,
        },
    )
    catalog = load_modem_presets_catalog()
    assert "lab-modem" in catalog
    assert catalog["lab-modem"]["spreading_factor"] == 8


def test_delete_modem_preset_blocks_when_referenced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup_home(tmp_path, monkeypatch)
    with pytest.raises(ReferenceError, match="fixture-modem"):
        delete_modem_preset("fixture-modem")


def test_serve_home_simulation_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _home, projects_dir = _setup_home(tmp_path, monkeypatch)
    server, host, port, _thread = _start_server(projects_dir)
    try:
        conn = HTTPConnection(host, port, timeout=10)
        conn.request("GET", "/api/home/simulation")
        resp = conn.getresponse()
        assert resp.status == 200
        payload = json.loads(resp.read().decode("utf-8"))
        assert "fixture-modem" in payload["modem_names"]
        assert payload["simulation"]["modem"] == "fixture-modem"
        conn.close()

        conn = HTTPConnection(host, port, timeout=10)
        conn.request(
            "PATCH",
            "/api/home/simulation",
            body=json.dumps({"radius_km": 48, "raster_dimension": 400}),
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        assert resp.status == 200
        payload = json.loads(resp.read().decode("utf-8"))
        assert payload["simulation"]["radius_km"] == 48.0
        assert payload["simulation"]["raster_dimension"] == 400
        conn.close()
    finally:
        server.shutdown()


def test_serve_modem_crud_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _home, projects_dir = _setup_home(tmp_path, monkeypatch)
    server, host, port, _thread = _start_server(projects_dir)
    try:
        conn = HTTPConnection(host, port, timeout=10)
        conn.request(
            "POST",
            "/api/home/modems",
            body=json.dumps(
                {
                    "name": "serve-modem",
                    "frequency_mhz": 868.0,
                    "bandwidth_khz": 125.0,
                    "spreading_factor": 7,
                    "coding_rate": 5,
                    "implementation_margin_db": 3.0,
                    "power_dbm": 14.0,
                    "sensitivity_dbm": -123.0,
                }
            ),
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        assert resp.status == 201
        payload = json.loads(resp.read().decode("utf-8"))
        assert payload["name"] == "serve-modem"
        conn.close()

        conn = HTTPConnection(host, port, timeout=10)
        conn.request(
            "PATCH",
            "/api/home/modems/serve-modem",
            body=json.dumps({"power_dbm": 16.0}),
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        assert resp.status == 200
        payload = json.loads(resp.read().decode("utf-8"))
        assert payload["preset"]["power_dbm"] == 16.0
        conn.close()

        conn = HTTPConnection(host, port, timeout=10)
        conn.request("DELETE", "/api/home/modems/serve-modem")
        resp = conn.getresponse()
        assert resp.status == 200
        conn.close()

        conn = HTTPConnection(host, port, timeout=10)
        conn.request("DELETE", "/api/home/modems/fixture-modem")
        resp = conn.getresponse()
        assert resp.status == 409
        payload = json.loads(resp.read().decode("utf-8"))
        assert "referenced" in payload["error"]
        conn.close()
    finally:
        server.shutdown()


def test_serve_get_project_simulation_shows_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _home, projects_dir = _setup_home(tmp_path, monkeypatch)
    server, host, port, _thread = _start_server(projects_dir)
    try:
        conn = HTTPConnection(host, port, timeout=10)
        conn.request("GET", "/api/p/sample/simulation")
        resp = conn.getresponse()
        assert resp.status == 200
        payload = json.loads(resp.read().decode("utf-8"))
        assert payload["simulation"]["radius_km"] == 10.0
        assert "radius_km" in payload["overrides"]
        assert float(payload["defaults"]["radius_km"]) == 50.0
        conn.close()
    finally:
        server.shutdown()


def test_serve_patch_project_simulation_reset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _home, projects_dir = _setup_home(tmp_path, monkeypatch)
    server, host, port, _thread = _start_server(projects_dir)
    try:
        conn = HTTPConnection(host, port, timeout=10)
        conn.request(
            "PATCH",
            "/api/p/sample/simulation",
            body=json.dumps({"reset": ["radius_km"]}),
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        assert resp.status == 200
        payload = json.loads(resp.read().decode("utf-8"))
        assert payload["simulation"]["radius_km"] == 50.0
        assert "radius_km" not in payload["overrides"]
        conn.close()
    finally:
        server.shutdown()


def test_serve_environment_crud_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _home, projects_dir = _setup_home(tmp_path, monkeypatch)
    server, host, port, _thread = _start_server(projects_dir)
    try:
        conn = HTTPConnection(host, port, timeout=10)
        conn.request(
            "POST",
            "/api/home/environments",
            body=json.dumps(
                {
                    "name": "serve-env",
                    "description": "Test env",
                    "climate": "desert",
                    "polarization": "vertical",
                    "clutter_height_m": 2.0,
                    "fresnel_clearance_fraction": 0.5,
                    "coverage_pessimism_db": 1.0,
                    "situation_pct": 90.0,
                    "time_pct": 90.0,
                    "ground_dielectric_v_m": 15.0,
                    "ground_conductivity_s_m": 0.005,
                    "atmosphere_bending_n": 301.0,
                }
            ),
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        assert resp.status == 201
        payload = json.loads(resp.read().decode("utf-8"))
        assert payload["name"] == "serve-env"
        conn.close()

        conn = HTTPConnection(host, port, timeout=10)
        conn.request("GET", "/api/home/environments")
        resp = conn.getresponse()
        assert resp.status == 200
        payload = json.loads(resp.read().decode("utf-8"))
        assert "serve-env" in payload["presets"]
        conn.close()
    finally:
        server.shutdown()
