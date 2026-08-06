"""Goal-seek plan persistence in project YAML."""

from __future__ import annotations

import json
import threading
from http.client import HTTPConnection
from pathlib import Path

from peaky_finders.serve.app import make_serve_wsgi_app
from peaky_finders.serve.seek_plan import patch_seek_plan, seek_plan_to_api, convert_seek_plan_locs_to_sites
from peaky_finders.core.preset import load_preset

_SEEK_PROJECT_YAML = """
simulation:
  provider: splatter
  radius_km: 100.0
  modem: fixture-modem
  environment: fixture-desert
  transmitter: {height_m: 2.0, gain_dbi: 3.0, loss_db: 2.0}
  receiver: {height_m: 2.0, gain_dbi: 3.0, loss_db: 2.0}
display:
  colormap: plasma
  min_dbm: -130.0
  max_dbm: -80.0
sites:
  start:
    name: Start
    loc: [40.0, -119.5]
  relay:
    name: Relay
    loc: [40.1, -119.4]
links: []
""".strip()

_PLAN_BODY = {
    "start": "start",
    "goal": [38.063508400276845, -117.2346081883534],
    "complete": False,
    "hops": [
        {"site": "start"},
        {"loc": [40.54805573486266, -118.18083353340626], "height_m": 2832},
        {"site": "relay"},
    ],
}


def _write_project(project_dir: Path) -> Path:
    project_dir.mkdir(parents=True, exist_ok=True)
    preset_path = project_dir / "config.yaml"
    preset_path.write_text(_SEEK_PROJECT_YAML + "\n", encoding="utf-8")
    return preset_path


def _start_server(projects_dir: Path):
    from wsgiref.simple_server import make_server

    app = make_serve_wsgi_app(projects_dir, verbose=False, request_log=False)
    server = make_server("127.0.0.1", 0, app)
    host, port = server.server_address[:2]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, host, port


def test_patch_seek_plan_writes_yaml(tmp_path: Path) -> None:
    preset_path = _write_project(tmp_path / "sample")
    api = patch_seek_plan(preset_path, _PLAN_BODY)
    assert api["start"] == "start"
    assert api["hops"][0] == {"site": "start"}
    preset = load_preset(preset_path)
    assert preset.seek.plan is not None
    assert preset.seek.plan.start == "start"
    assert preset.seek.plan.hops[1].loc == (40.54805573486266, -118.18083353340626)


def test_seek_plan_model_rejects_unknown_site(tmp_path: Path) -> None:
    preset_path = _write_project(tmp_path / "sample")
    body = dict(_PLAN_BODY)
    body["hops"] = [{"site": "missing"}]
    try:
        patch_seek_plan(preset_path, body)
        raise AssertionError("expected validation error")
    except Exception as exc:
        assert "unknown site" in str(exc)


def test_api_seek_plan_patch_and_get(tmp_path: Path) -> None:
    _write_project(tmp_path / "sample")
    server, host, port = _start_server(tmp_path)
    try:
        conn = HTTPConnection(host, port, timeout=5)
        conn.request(
            "PATCH",
            "/api/p/sample/seek/plan",
            json.dumps(_PLAN_BODY),
            {"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        body = json.loads(resp.read().decode("utf-8"))
        assert resp.status == 200
        assert body["plan"]["start"] == "start"

        conn = HTTPConnection(host, port, timeout=5)
        conn.request("GET", "/api/p/sample/seek/plan")
        resp = conn.getresponse()
        body = json.loads(resp.read().decode("utf-8"))
        assert resp.status == 200
        assert body["plan"]["hops"][2] == {"site": "relay"}
    finally:
        server.shutdown()


def test_api_seek_plan_delete(tmp_path: Path) -> None:
    preset_path = _write_project(tmp_path / "sample")
    patch_seek_plan(preset_path, _PLAN_BODY)
    server, host, port = _start_server(tmp_path)
    try:
        conn = HTTPConnection(host, port, timeout=5)
        conn.request("DELETE", "/api/p/sample/seek/plan")
        resp = conn.getresponse()
        assert resp.status == 200
        preset = load_preset(preset_path)
        assert preset.seek.plan is None
    finally:
        server.shutdown()


def test_seek_plan_to_api_none() -> None:
    assert seek_plan_to_api(None) is None


def test_convert_seek_plan_locs_to_sites(tmp_path: Path) -> None:
    preset_path = _write_project(tmp_path / "sample")
    patch_seek_plan(preset_path, _PLAN_BODY)
    result = convert_seek_plan_locs_to_sites(
        preset_path,
        name_prefix="Relay",
        tags=["seek", "relay"],
    )
    assert result["converted"] == 1
    assert result["tagged"] == 2
    assert len(result["sites"]) == 3
    created = next(row for row in result["sites"] if row["name"] == "Relay 1")
    assert result["plan"]["hops"][1] == {"site": created["slug"]}
    preset = load_preset(preset_path)
    assert preset.seek.plan is not None
    assert preset.seek.plan.hops[1].site == created["slug"]
    assert preset.seek.plan.hops[1].loc is None
    slug = created["slug"]
    assert preset.sites[slug].tags == ["seek", "relay"]
    assert preset.sites[slug].height_m is None
    assert set(preset.sites["start"].tags or []) >= {"seek", "relay"}
    assert set(preset.sites["relay"].tags or []) >= {"seek", "relay"}


def test_convert_seek_plan_no_loc_hops(tmp_path: Path) -> None:
    preset_path = _write_project(tmp_path / "sample")
    body = {
        "start": "start",
        "goal": [38.0, -117.0],
        "complete": False,
        "hops": [{"site": "start"}, {"site": "relay"}],
    }
    patch_seek_plan(preset_path, body)
    try:
        convert_seek_plan_locs_to_sites(preset_path, name_prefix="Relay", tags=["x"])
        raise AssertionError("expected error")
    except Exception as exc:
        assert "no coordinate hops" in str(exc)


def test_api_seek_plan_convert_to_sites(tmp_path: Path) -> None:
    preset_path = _write_project(tmp_path / "sample")
    patch_seek_plan(preset_path, _PLAN_BODY)
    server, host, port = _start_server(tmp_path)
    try:
        conn = HTTPConnection(host, port, timeout=5)
        conn.request(
            "POST",
            "/api/p/sample/seek/plan/convert-to-sites",
            json.dumps({"name_prefix": "Hop", "tags": ["path"]}),
            {"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        body = json.loads(resp.read().decode("utf-8"))
        assert resp.status == 200
        assert body["converted"] == 1
        assert body["plan"]["hops"][1]["site"]
        assert body["sites"][0]["tags"] == ["path"]
    finally:
        server.shutdown()
