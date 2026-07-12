"""``peaky serve`` simulation preset updates."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from peaky_finders.core.project.scaffold import scaffold_project
from peaky_finders.serve.app import make_serve_wsgi_app
from peaky_finders.serve.simulation import update_viewshed_sim_to_preset
from peaky_finders.core.preset import load_preset


def _start_server(projects_dir: Path):
    from wsgiref.simple_server import make_server

    app = make_serve_wsgi_app(projects_dir, verbose=False, request_log=False)
    server = make_server("127.0.0.1", 0, app)
    host, port = server.server_address[:2]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, host, port, thread


def test_update_viewshed_sim_to_preset(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    scaffold_project("demo", parent=projects_dir)
    preset_path = projects_dir / "demo" / "config.yaml"
    before = load_preset(preset_path)
    assert float(before.simulation.radius_km) != 42.0

    written = update_viewshed_sim_to_preset(
        preset_path,
        radius_km=42.0,
        raster_dimension=1024,
    )
    assert written == {"radius_km": 42.0, "raster_dimension": 1024}

    preset = load_preset(preset_path)
    assert float(preset.simulation.radius_km) == 42.0
    assert preset.simulation.raster_dimension == 1024


def test_update_viewshed_sim_rejects_out_of_range(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    scaffold_project("demo", parent=projects_dir)
    preset_path = projects_dir / "demo" / "config.yaml"
    with pytest.raises(ValueError, match="radius_km"):
        update_viewshed_sim_to_preset(preset_path, radius_km=150.0, raster_dimension=500)
    with pytest.raises(ValueError, match="raster_dimension"):
        update_viewshed_sim_to_preset(preset_path, radius_km=50.0, raster_dimension=64)


def test_serve_post_simulation_api(tmp_path: Path) -> None:
    from http.client import HTTPConnection

    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    scaffold_project("demo", parent=projects_dir)
    server, host, port, _thread = _start_server(projects_dir)
    try:
        conn = HTTPConnection(host, port, timeout=10)
        conn.request(
            "POST",
            "/api/p/demo/simulation",
            body=json.dumps({"radius_km": 55, "raster_dimension": 768}),
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        assert resp.status == 200
        payload = json.loads(resp.read().decode("utf-8"))
        assert payload["slug"] == "demo"
        assert payload["simulation"] == {"radius_km": 55.0, "raster_dimension": 768}
        conn.close()

        preset = load_preset(projects_dir / "demo" / "config.yaml")
        assert float(preset.simulation.radius_km) == 55.0
        assert preset.simulation.raster_dimension == 768
    finally:
        server.shutdown()
