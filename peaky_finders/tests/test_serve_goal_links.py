"""``peaky serve`` goal link APIs."""

from __future__ import annotations

import json
import threading
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import patch

from peaky_finders.serve_cli import make_serve_handler
from peaky_finders.serve_goal_links import load_project_goal_links
from peaky_finders.sites_job import load_preset_goals, load_preset_sites, write_preset_document


def _start_server(projects_dir: Path):
    from http.server import HTTPServer

    handler = make_serve_handler(projects_dir)
    server = HTTPServer(("127.0.0.1", 0), handler)
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, host, port, thread


def _write_land_preset(preset_path: Path) -> None:
    write_preset_document(
        preset_path,
        {
            "simulation": {
                "provider": "splatter",
                "radius_km": 60.0,
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
                "transmitter": {"height_m": 2.0, "gain_dbi": 3.0, "loss_db": 2.0},
                "receiver": {"height_m": 2.0, "gain_dbi": 3.0, "loss_db": 2.0},
            },
            "display": {"colormap": "plasma", "min_dbm": -130.0, "max_dbm": -80.0},
            "land": {
                "inputs_root": "data",
                "aoi": [{"path": "aoi/missing.gdb", "layers": [{"name": "boundary"}]}],
                "include": [],
                "exclude": [],
            },
            "sites": {"hub": {"name": "Hub", "loc": [39.5, -119.5]}},
            "goals": {"valley": {"name": "Valley", "loc": [39.6, -119.4]}},
        },
    )


def test_load_project_goal_links_without_bundle_gdb(tmp_path: Path) -> None:
    project_dir = tmp_path / "nevada"
    project_dir.mkdir()
    _write_land_preset(project_dir / "config.yaml")
    sites = load_preset_sites(project_dir / "config.yaml")
    goals = load_preset_goals(project_dir / "config.yaml")

    def _rf_batch(_session, pairs, *, rf_json: str) -> list[bool]:
        return [True] * len(pairs)

    with (
        patch("peaky_finders.serve_goal_links.splatter_session"),
        patch("peaky_finders.serve_goal_links.ensure_dem_for_points"),
        patch("peaky_finders.serve_goal_links.mutual_hop_batch", side_effect=_rf_batch),
    ):
        payload = load_project_goal_links(project_dir, goals, sites)

    assert len(payload["links"]) == 1
    row = payload["links"][0]
    assert row["goal"] == "valley"
    assert row["site"] == "hub"
    assert row["linked"] is True
    assert row["captured"] is False


def test_api_project_goal_links_without_bundle_gdb(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    project_dir = projects_dir / "nevada"
    project_dir.mkdir(parents=True)
    _write_land_preset(project_dir / "config.yaml")

    server, host, port, _thread = _start_server(projects_dir)
    try:
        with (
            patch("peaky_finders.serve_goal_links.splatter_session"),
            patch("peaky_finders.serve_goal_links.ensure_dem_for_points"),
            patch("peaky_finders.serve_goal_links.mutual_hop_batch", side_effect=lambda *_a, **_k: [True]),
        ):
            conn = HTTPConnection(host, port, timeout=2)
            conn.request("GET", "/api/p/nevada/goal-links")
            resp = conn.getresponse()
            payload = json.loads(resp.read().decode("utf-8"))

        assert resp.status == 200
        assert payload["project"] == "nevada"
        assert len(payload["links"]) == 1
        assert payload["links"][0]["captured"] is False
    finally:
        server.shutdown()
        server.server_close()
