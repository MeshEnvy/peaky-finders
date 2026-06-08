"""``peaky serve`` web UI."""

from __future__ import annotations

import json
import os
import threading
import time
from http.client import HTTPConnection
from pathlib import Path

import pytest

from peaky_finders.new_cli import scaffold_project
from peaky_finders.serve_cli import (
    _reload_detected,
    _reload_snapshots,
    _serialize_project_sites,
    build_serve_parser,
    make_serve_handler,
    resolve_serve_projects_dir,
    run_serve,
)
from peaky_finders.sites_job import SiteEntry, SiteType


def _start_server(projects_dir: Path):
    from http.server import HTTPServer

    handler = make_serve_handler(projects_dir)
    server = HTTPServer(("127.0.0.1", 0), handler)
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, host, port, thread


def test_build_serve_parser_defaults() -> None:
    args = build_serve_parser().parse_args([])
    assert args.host == "0.0.0.0"
    assert args.port == 8080
    assert args.verbose is False
    assert args.reload is False
    assert args.no_reload is False


def test_build_serve_parser_reload_flags() -> None:
    assert build_serve_parser().parse_args(["--reload"]).reload is True
    assert build_serve_parser().parse_args(["--no-reload"]).no_reload is True


def test_reload_detects_py_change(tmp_path: Path) -> None:
    src = tmp_path / "pkg"
    src.mkdir()
    module = src / "app.py"
    module.write_text("x = 1\n", encoding="utf-8")
    before = _reload_snapshots([src])
    assert _reload_detected(before, [src]) is False
    module.write_text("x = 2\n", encoding="utf-8")
    future = time.time() + 2.0
    os.utime(module, (future, future))
    assert _reload_detected(before, [src]) is True


def test_run_serve_reload_uses_supervisor(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def _fake_reload(*_args: object, **_kwargs: object) -> int:
        calls.append("reload")
        return 0

    def _fake_blocking(*_args: object, **_kwargs: object) -> int:
        calls.append("blocking")
        return 0

    monkeypatch.setattr("peaky_finders.serve_cli._run_serve_with_reload", _fake_reload)
    monkeypatch.setattr("peaky_finders.serve_cli._run_serve_blocking", _fake_blocking)
    monkeypatch.setenv("PEAKY_PROJECTS", "/tmp/peaky-reload-test-projects")

    args = build_serve_parser().parse_args(["--reload"])
    assert run_serve(args) == 0
    assert calls == ["reload"]

    args = build_serve_parser().parse_args(["--reload", "--no-reload"])
    assert run_serve(args) == 0
    assert calls == ["reload", "blocking"]


def test_resolve_serve_projects_dir_defaults_to_peaky_home_projects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    monkeypatch.delenv("PEAKY_HOME", raising=False)
    monkeypatch.delenv("PEAKY_PROJECTS", raising=False)
    monkeypatch.setenv("HOME", str(home))
    assert resolve_serve_projects_dir() == (home / ".peaky" / "projects").resolve()


def test_resolve_serve_projects_dir_honors_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    custom = tmp_path / "custom-projects"
    monkeypatch.setenv("PEAKY_PROJECTS", str(custom))
    assert resolve_serve_projects_dir() == custom.resolve()


def test_landing_lists_projects(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    scaffold_project("alpha", parent=projects_dir)
    scaffold_project("beta", parent=projects_dir)

    server, host, port, _thread = _start_server(projects_dir)
    try:
        conn = HTTPConnection(host, port, timeout=2)
        conn.request("GET", "/")
        resp = conn.getresponse()
        body = resp.read().decode("utf-8")
        assert resp.status == 200
        assert "alpha" in body
        assert 'href="/p/beta/"' in body
        assert "Create empty template" in body
        assert 'href="/favicon.svg"' in body
        assert 'href="/site.webmanifest"' in body
        assert 'data-bs-theme="dark"' in body
        assert "bootstrap@5.3.3" in body
    finally:
        server.shutdown()
        server.server_close()


def test_serve_favicon_assets(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()

    server, host, port, _thread = _start_server(projects_dir)
    try:
        conn = HTTPConnection(host, port, timeout=2)
        conn.request("GET", "/favicon.ico")
        resp = conn.getresponse()
        body = resp.read()
        assert resp.status == 200
        assert resp.getheader("Content-Type") == "image/x-icon"
        assert len(body) > 0

        conn = HTTPConnection(host, port, timeout=2)
        conn.request("GET", "/site.webmanifest")
        resp = conn.getresponse()
        manifest = resp.read().decode("utf-8")
        assert resp.status == 200
        assert resp.getheader("Content-Type") == "application/manifest+json"
        assert '"short_name": "Peaky"' in manifest
    finally:
        server.shutdown()
        server.server_close()


def test_api_projects_json(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    scaffold_project("demo", parent=projects_dir)

    server, host, port, _thread = _start_server(projects_dir)
    try:
        conn = HTTPConnection(host, port, timeout=2)
        conn.request("GET", "/api/projects")
        resp = conn.getresponse()
        payload = json.loads(resp.read().decode("utf-8"))
        assert resp.status == 200
        assert payload["projects"] == [{"slug": "demo"}]
        assert payload["root"] == str(projects_dir)
    finally:
        server.shutdown()
        server.server_close()


def test_post_creates_project_and_redirects(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()

    server, host, port, _thread = _start_server(projects_dir)
    try:
        conn = HTTPConnection(host, port, timeout=2)
        conn.request("POST", "/projects", body=b"slug=new-region", headers={"Content-Type": "application/x-www-form-urlencoded"})
        resp = conn.getresponse()
        resp.read()
        assert resp.status == 303
        assert resp.getheader("Location") == "/p/new-region/"
        assert (projects_dir / "new-region" / "config.yaml").is_file()
    finally:
        server.shutdown()
        server.server_close()


def test_post_invalid_slug_shows_error(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()

    server, host, port, _thread = _start_server(projects_dir)
    try:
        conn = HTTPConnection(host, port, timeout=2)
        conn.request("POST", "/projects", body=b"slug=bad%20slug", headers={"Content-Type": "application/x-www-form-urlencoded"})
        resp = conn.getresponse()
        body = resp.read().decode("utf-8")
        assert resp.status == 400
        assert "invalid project slug" in body
    finally:
        server.shutdown()
        server.server_close()


def test_serve_static_app_css(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()

    server, host, port, _thread = _start_server(projects_dir)
    try:
        conn = HTTPConnection(host, port, timeout=2)
        conn.request("GET", "/static/app.css")
        resp = conn.getresponse()
        body = resp.read().decode("utf-8")
        assert resp.status == 200
        assert resp.getheader("Content-Type") == "text/css"
        assert ".map-shell" in body
    finally:
        server.shutdown()
        server.server_close()


def test_project_page_includes_site_map(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    scaffold_project("mesh-demo", parent=projects_dir)

    server, host, port, _thread = _start_server(projects_dir)
    try:
        conn = HTTPConnection(host, port, timeout=2)
        conn.request("GET", "/p/mesh-demo/")
        resp = conn.getresponse()
        body = resp.read().decode("utf-8")
        assert resp.status == 200
        assert 'id="map"' in body
        assert "maplibre-gl" in body
        assert 'id="basemap"' in body
        assert "Street" in body
        assert "Topo" in body
        assert "Satellite" in body
        assert '"name": "Hub"' in body
        assert "1 site" in body
        assert 'id="site-panel"' in body
        assert "/static/project-map.js" in body
        assert 'data-bs-theme="dark"' in body
        assert "bootstrap@5.3.3" in body
        assert "PEAKY_PROJECT" in body

        conn = HTTPConnection(host, port, timeout=2)
        conn.request("GET", "/static/project-map.js")
        js_resp = conn.getresponse()
        js_body = js_resp.read().decode("utf-8")
        assert js_resp.status == 200
        assert "clarity.maptiles.arcgis.com" in js_body
        assert "terrain-dem" in js_body
        assert "maxPitch: 85" in js_body
        assert "SITE_FIT_BUFFER_KM = 30" in js_body
        assert "function resetHomeView()" in js_body
        assert "stopImmediatePropagation" in js_body
        assert "visualizePitch: true" in js_body
        assert "function selectSite" in js_body
        assert "queryRenderedFeatures" in js_body
        assert "setViewshedVisible" in js_body
        assert "function renderPanel" in js_body
        assert "localStorage" in js_body
        assert "peaky.map.v1." in js_body
        assert "scheduleSaveMapState" in js_body
    finally:
        server.shutdown()
        server.server_close()


def test_project_page_loads_sites_when_bundle_invalid(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    project_dir = projects_dir / "nevada-like"
    project_dir.mkdir(parents=True)
    (project_dir / "config.yaml").write_text(
        """
sites:
  hub:
    name: Hub
    loc: [39.5, -119.5]
links: []
suggest:
  strategy: mesh-backbone
  mesh_backbone:
    goals:
      russel-bridge:
        loc: russell-peak
""".strip(),
        encoding="utf-8",
    )

    server, host, port, _thread = _start_server(projects_dir)
    try:
        conn = HTTPConnection(host, port, timeout=2)
        conn.request("GET", "/p/nevada-like/")
        resp = conn.getresponse()
        body = resp.read().decode("utf-8")
        assert resp.status == 200
        assert 'id="map"' in body
        assert '"name": "Hub"' in body
    finally:
        server.shutdown()
        server.server_close()


def test_api_project_sites_json(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    scaffold_project("mesh-demo", parent=projects_dir)

    server, host, port, _thread = _start_server(projects_dir)
    try:
        conn = HTTPConnection(host, port, timeout=2)
        conn.request("GET", "/api/p/mesh-demo/sites")
        resp = conn.getresponse()
        payload = json.loads(resp.read().decode("utf-8"))
        assert resp.status == 200
        assert payload["slug"] == "mesh-demo"
        assert len(payload["sites"]) == 1
        assert payload["sites"][0]["slug"] == "hub"
        assert payload["sites"][0]["name"] == "Hub"
        assert payload["sites"][0]["lat"] == 39.5
        assert payload["sites"][0]["lon"] == -119.5
    finally:
        server.shutdown()
        server.server_close()


def test_serialize_project_sites_includes_metadata() -> None:
    sites = {
        "peak": SiteEntry(
            type=SiteType.INSTALLED,
            name="Peak",
            loc=(39.5, -119.5),
            elevation_m=1713.0,
            plss="NV; T.30N R.23E",
            mlrs="NV210300N0230E0SN360",
            rationale="Approved site",
        ),
    }
    row = _serialize_project_sites(sites)[0]
    assert row["elevation_m"] == 1713.0
    assert row["plss"] == "NV; T.30N R.23E"
    assert row["mlrs"] == "NV210300N0230E0SN360"
    assert row["rationale"] == "Approved site"
    assert "description" not in row


def test_api_project_sites_json_with_metadata(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    project_dir = projects_dir / "rich"
    project_dir.mkdir(parents=True)
    (project_dir / "config.yaml").write_text(
        """
sites:
  peak:
    name: Peak
    loc: [39.5, -119.5]
    elevation_m: 1713.0
    plss: NV; T.30N R.23E
    mlrs: NV210300N0230E0SN360
    rationale: Approved site
links: []
suggest:
  strategy: mesh-backbone
  mesh_backbone:
    goals:
      russel-bridge:
        loc: russell-peak
""".strip(),
        encoding="utf-8",
    )

    server, host, port, _thread = _start_server(projects_dir)
    try:
        conn = HTTPConnection(host, port, timeout=2)
        conn.request("GET", "/api/p/rich/sites")
        resp = conn.getresponse()
        payload = json.loads(resp.read().decode("utf-8"))
        assert resp.status == 200
        site = payload["sites"][0]
        assert site["elevation_m"] == 1713.0
        assert site["plss"] == "NV; T.30N R.23E"
    finally:
        server.shutdown()
        server.server_close()


def test_run_serve_exits_on_keyboard_interrupt(monkeypatch) -> None:
    class _FakeServer:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def serve_forever(self) -> None:
            raise KeyboardInterrupt

        def server_close(self) -> None:
            pass

    monkeypatch.setattr("peaky_finders.serve_cli.HTTPServer", _FakeServer)
    monkeypatch.setenv("PEAKY_PROJECTS", "/tmp/peaky-test-projects")
    args = build_serve_parser().parse_args(["--port", "9090"])
    assert run_serve(args) == 0
