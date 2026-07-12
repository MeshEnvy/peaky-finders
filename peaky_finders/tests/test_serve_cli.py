"""``peaky serve`` web UI."""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from http.client import HTTPConnection
from pathlib import Path

from unittest.mock import patch

import pytest

from fixture_paths import SAMPLE_PROJECT_CONFIG
from peaky_finders.new_cli import scaffold_project
from peaky_finders.plss_fetch import loc_stamp, write_plss_loc_cache
from peaky_finders.serve_app import _serialize_project_sites, make_serve_wsgi_app
from peaky_finders.serve_cli import (
    SERVE_RELOAD_CHILD_ENV,
    _is_serve_reload_child,
    _reload_changed,
    _reload_fingerprints,
    _serve_child_argv,
    build_serve_parser,
    resolve_serve_projects_dir,
    resolve_serve_request_log,
    run_serve,
)
from peaky_finders.sites_job import SiteEntry, SiteType, resolved_preset_build_dir


def _start_server(projects_dir: Path):
    from wsgiref.simple_server import make_server

    app = make_serve_wsgi_app(projects_dir, verbose=False, request_log=False)
    server = make_server("127.0.0.1", 0, app)
    host, port = server.server_address[:2]
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
    assert args.request_log is False
    assert args.no_request_log is False


def test_build_serve_parser_request_log_flags() -> None:
    assert build_serve_parser().parse_args(["--request-log"]).request_log is True
    assert build_serve_parser().parse_args(["--no-request-log"]).no_request_log is True


def test_build_serve_parser_reload_flags() -> None:
    assert build_serve_parser().parse_args(["--reload"]).reload is True
    assert build_serve_parser().parse_args(["--no-reload"]).no_reload is True


def test_reload_detects_py_change(tmp_path: Path) -> None:
    src = tmp_path / "pkg"
    src.mkdir()
    module = src / "app.py"
    module.write_text("x = 1\n", encoding="utf-8")
    before = _reload_fingerprints([src])
    assert _reload_changed(before, [src]) is False
    module.write_text("x = 2\n", encoding="utf-8")
    assert _reload_changed(before, [src]) is True


def test_reload_ignores_mtime_only_change(tmp_path: Path) -> None:
    src = tmp_path / "pkg"
    src.mkdir()
    module = src / "app.py"
    module.write_text("x = 1\n", encoding="utf-8")
    before = _reload_fingerprints([src])
    future = time.time() + 2.0
    os.utime(module, (future, future))
    assert _reload_changed(before, [src]) is False


def test_serve_child_argv_uses_no_reload() -> None:
    argv = _serve_child_argv("127.0.0.1", 9090, verbose=True)
    assert argv[1:3] == ["-m", "peaky_finders.serve_cli"]
    assert "--no-reload" in argv
    assert "--reload" not in argv
    assert "--no-request-log" not in argv
    assert "peaky_cli" not in argv
    assert argv[argv.index("--host") + 1] == "127.0.0.1"
    assert argv[argv.index("--port") + 1] == "9090"
    assert "--verbose" in argv

    quiet = _serve_child_argv("127.0.0.1", 9090, verbose=False, no_request_log=True)
    assert "--no-request-log" in quiet


def test_resolve_serve_request_log_reload_child(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SERVE_RELOAD_CHILD_ENV, "1")
    args = build_serve_parser().parse_args([])
    assert resolve_serve_request_log(args) is True


def test_resolve_serve_request_log_no_request_log_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SERVE_RELOAD_CHILD_ENV, "1")
    args = build_serve_parser().parse_args(["--no-request-log"])
    assert resolve_serve_request_log(args) is False


def test_probe_connect_host_maps_wildcard() -> None:
    from peaky_finders.serve_cli import _probe_connect_host, _serve_public_url

    assert _probe_connect_host("0.0.0.0") == "127.0.0.1"
    assert _serve_public_url("0.0.0.0", 8080) == "http://127.0.0.1:8080/"


def test_is_serve_reload_child(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(SERVE_RELOAD_CHILD_ENV, raising=False)
    assert _is_serve_reload_child() is False
    monkeypatch.setenv(SERVE_RELOAD_CHILD_ENV, "1")
    assert _is_serve_reload_child() is True


def test_run_serve_reload_uses_supervisor(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def _fake_supervisor(*_args: object, **_kwargs: object) -> int:
        calls.append("supervisor")
        return 0

    def _fake_blocking(*_args: object, **_kwargs: object) -> int:
        calls.append("blocking")
        return 0

    monkeypatch.setattr("peaky_finders.serve_cli._supervise_serve_reload", _fake_supervisor)
    monkeypatch.setattr("peaky_finders.serve_cli._run_serve_blocking", _fake_blocking)
    monkeypatch.delenv(SERVE_RELOAD_CHILD_ENV, raising=False)
    monkeypatch.setenv("PEAKY_PROJECTS", "/tmp/peaky-reload-test-projects")

    args = build_serve_parser().parse_args(["--reload"])
    assert run_serve(args) == 0
    assert calls == ["supervisor"]

    args = build_serve_parser().parse_args(["--reload", "--no-reload"])
    assert run_serve(args) == 0
    assert calls == ["supervisor", "blocking"]

    monkeypatch.setenv(SERVE_RELOAD_CHILD_ENV, "1")
    args = build_serve_parser().parse_args(["--reload"])
    assert run_serve(args) == 0
    assert calls == ["supervisor", "blocking", "blocking"]


def test_resolve_serve_projects_dir_defaults_to_peaky_home_projects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    home_dir = workspace / "peaky_home"
    (home_dir / "projects").mkdir(parents=True)
    monkeypatch.setattr("peaky_finders.sites_job.repo_root", lambda: workspace)
    monkeypatch.delenv("PEAKY_HOME", raising=False)
    monkeypatch.delenv("PEAKY_PROJECTS", raising=False)
    assert resolve_serve_projects_dir() == (home_dir / "projects").resolve()


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
        assert 'class="wa-dark wa-theme-default wa-brand-blue"' in body
        assert "@awesome.me/webawesome@" in body
        assert "dist-cdn/webawesome.loader.js" in body
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
        assert '"name": "Hub"' in body
        assert 'id="site-panel"' in body
        assert "/static/project-map.js" in body
        assert 'class="wa-dark wa-theme-default wa-brand-blue"' in body
        assert "@awesome.me/webawesome@" in body
        assert "dist-cdn/webawesome.loader.js" in body
        assert "PEAKY_PROJECT" in body

        conn = HTTPConnection(host, port, timeout=2)
        conn.request("GET", "/static/project-map.js")
        js_resp = conn.getresponse()
        js_body = js_resp.read().decode("utf-8")
        assert js_resp.status == 200
        assert "basemap.nationalmap.gov" in js_body
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
        assert "function installMapToolbar" in js_body
        assert "map-tool-basemap" in js_body
        assert "map-tool-sites" in js_body
        assert "map-tool-goals" in js_body
        assert "map-tool-opacity" in js_body
        assert "home-settings-open" in js_body
        assert "USGS Topo" in js_body
        assert "Satellite" in js_body
        assert "opentopo" not in js_body
    finally:
        server.shutdown()
        server.server_close()


def test_project_page_loads_sites_when_bundle_invalid(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    project_dir = projects_dir / "nevada-like"
    project_dir.mkdir(parents=True)
    (project_dir / "config.yaml").write_text(
        """
simulation:
  provider: splatter
  radius_km: 50.0
  modem: fixture-modem
  environment: fixture-desert
  transmitter: {height_m: 2.0, gain_dbi: 3.0, loss_db: 2.0}
  receiver: {height_m: 2.0, gain_dbi: 3.0, loss_db: 2.0}
display:
  colormap: plasma
  min_dbm: -130.0
  max_dbm: -80.0
sites:
  hub:
    name: Hub
    loc: [39.5, -119.5]
links: []
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
            plss="NV210300N0230E0SN360ASENW",
            rationale="Approved site",
        ),
    }
    row = _serialize_project_sites(sites)[0]
    assert row["elevation_m"] == 1713.0
    assert row["plss"] == "NV210300N0230E0SN360ASENW"
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
    plss: NV210300N0230E0SN360ASENW
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
        assert site["plss"] == "NV210300N0230E0SN360ASENW"
    finally:
        server.shutdown()
        server.server_close()


def test_post_project_site_creates_planned_site(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    scaffold_project("mesh-demo", parent=projects_dir)

    server, host, port, _thread = _start_server(projects_dir)
    try:
        body = json.dumps({"name": "Ridge Top", "lat": 39.6, "lon": -119.4}).encode("utf-8")
        conn = HTTPConnection(host, port, timeout=2)
        conn.request(
            "POST",
            "/api/p/mesh-demo/sites",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        payload = json.loads(resp.read().decode("utf-8"))
        assert resp.status == 201
        assert payload["slug"] == "mesh-demo"
        assert payload["site"]["slug"] == "ridge-top"
        assert payload["site"]["name"] == "Ridge Top"
        assert payload["site"]["type"] == "planned"
        assert payload["site"]["lat"] == 39.6
        assert payload["site"]["lon"] == -119.4
        assert payload["site"].get("tags") in (None, [])

        conn = HTTPConnection(host, port, timeout=2)
        conn.request("GET", "/api/p/mesh-demo/sites")
        resp = conn.getresponse()
        sites_payload = json.loads(resp.read().decode("utf-8"))
        slugs = {row["slug"] for row in sites_payload["sites"]}
        assert "ridge-top" in slugs
        assert "hub" in slugs
    finally:
        server.shutdown()
        server.server_close()


def test_post_project_site_with_tags(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    scaffold_project("mesh-demo", parent=projects_dir)

    server, host, port, _thread = _start_server(projects_dir)
    try:
        body = json.dumps(
            {
                "name": "Tagged Ridge",
                "lat": 40.65495,
                "lon": -119.35161,
                "tags": ["EIP", "slpt"],
            }
        ).encode("utf-8")
        conn = HTTPConnection(host, port, timeout=2)
        conn.request(
            "POST",
            "/api/p/mesh-demo/sites",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        payload = json.loads(resp.read().decode("utf-8"))
        assert resp.status == 201
        assert payload["site"]["slug"] == "tagged-ridge"
        assert payload["site"]["tags"] == ["eip", "slpt"]
        assert payload["site"]["lat"] == 40.65495
        assert payload["site"]["lon"] == -119.35161
    finally:
        server.shutdown()
        server.server_close()


def test_api_sites_prefetch(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    shutil.copytree(SAMPLE_PROJECT_CONFIG.parent, projects_dir / "sample")

    server, host, port, _thread = _start_server(projects_dir)
    try:
        with (
            patch(
                "peaky_finders.serve_plss.plss_for_point",
                return_value="NV210300N0230E0SN360ASENW",
            ),
            patch("peaky_finders.serve_links.splatter_session"),
            patch("peaky_finders.serve_links.ensure_dem_for_points"),
            patch(
                "peaky_finders.serve_links.mutual_hop_batch",
                return_value=[True],
            ),
        ):
            conn = HTTPConnection(host, port, timeout=2)
            conn.request("GET", "/api/p/sample/sites/prefetch?lat=39.5&lon=-119.5")
            resp = conn.getresponse()
            payload = json.loads(resp.read().decode("utf-8"))

        assert resp.status == 200
        assert payload["project"] == "sample"
        assert payload["plss"] == "NV210300N0230E0SN360ASENW"
        assert isinstance(payload["links"], list)
        assert payload["links_geojson"]["type"] == "FeatureCollection"
        assert isinstance(payload["links_geojson"]["features"], list)
    finally:
        server.shutdown()
        server.server_close()


def test_post_project_site_applies_cached_plss(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    project_dir = scaffold_project("mesh-demo", parent=projects_dir)
    preset_path = project_dir / "config.yaml"
    lat, lon = 39.6, -119.4
    cache_base = resolved_preset_build_dir(preset_path)
    write_plss_loc_cache(
        cache_base,
        {loc_stamp(lat, lon): {"plss": "NV210300N0230E0SN360ASENW"}},
    )

    server, host, port, _thread = _start_server(projects_dir)
    try:
        body = json.dumps({"name": "Ridge Top", "lat": lat, "lon": lon}).encode("utf-8")
        conn = HTTPConnection(host, port, timeout=2)
        conn.request(
            "POST",
            "/api/p/mesh-demo/sites",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        payload = json.loads(resp.read().decode("utf-8"))
        assert resp.status == 201
        assert payload["site"]["plss"] == "NV210300N0230E0SN360ASENW"
    finally:
        server.shutdown()
        server.server_close()


def test_post_project_site_dedupes_slug(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    scaffold_project("mesh-demo", parent=projects_dir)
    preset_path = projects_dir / "mesh-demo" / "config.yaml"
    text = preset_path.read_text(encoding="utf-8")
    insert = """  ridge-top:
    type: planned
    name: Ridge Top
    loc: [39.1, -119.1]
"""
    marker = "\nlinks:"
    assert marker in text
    preset_path.write_text(text.replace(marker, f"\n{insert}{marker}", 1), encoding="utf-8")

    server, host, port, _thread = _start_server(projects_dir)
    try:
        body = json.dumps({"name": "Ridge Top", "lat": 39.2, "lon": -119.2}).encode("utf-8")
        conn = HTTPConnection(host, port, timeout=2)
        conn.request(
            "POST",
            "/api/p/mesh-demo/sites",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        payload = json.loads(resp.read().decode("utf-8"))
        assert resp.status == 201
        assert payload["site"]["slug"] == "ridge-top-2"
    finally:
        server.shutdown()
        server.server_close()


def test_post_project_site_rejects_invalid_coords(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    scaffold_project("mesh-demo", parent=projects_dir)

    server, host, port, _thread = _start_server(projects_dir)
    try:
        body = json.dumps({"name": "Bad", "lat": 95.0, "lon": -119.0}).encode("utf-8")
        conn = HTTPConnection(host, port, timeout=2)
        conn.request(
            "POST",
            "/api/p/mesh-demo/sites",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        payload = json.loads(resp.read().decode("utf-8"))
        assert resp.status == 422
        assert "error" in payload
    finally:
        server.shutdown()
        server.server_close()


def test_post_project_site_with_top_level_goals(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    project_dir = projects_dir / "nevada-like"
    project_dir.mkdir(parents=True)
    (project_dir / "config.yaml").write_text(
        """
simulation:
  modem: fixture-modem
  environment: fixture-desert
  transmitter: {height_m: 2.0, gain_dbi: 2.0, loss_db: 0.0}
  receiver: {height_m: 2.0, gain_dbi: 2.0, loss_db: 0.0}
display:
  colormap: rainbow
  min_dbm: -130.0
  max_dbm: -80.0
sites:
  hub:
    name: Hub
    loc: [39.5, -119.5]
  bridge:
    type: goal
    name: Bridge
    loc: [39.9, -119.3]
links: []
suggest:
  strategy: mesh-backbone
  mesh_backbone:
    goal_order: [bridge]
""".strip(),
        encoding="utf-8",
    )

    server, host, port, _thread = _start_server(projects_dir)
    try:
        body = json.dumps({"name": "Foo", "lat": 40.8, "lon": -120.36}).encode("utf-8")
        conn = HTTPConnection(host, port, timeout=2)
        conn.request(
            "POST",
            "/api/p/nevada-like/sites",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        payload = json.loads(resp.read().decode("utf-8"))
        assert resp.status == 201
        assert payload["site"]["slug"] == "foo"
        assert payload["site"]["name"] == "Foo"
    finally:
        server.shutdown()
        server.server_close()


def test_project_page_includes_add_controls(tmp_path: Path) -> None:
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
        assert 'id="entity-panel"' in body
        assert 'id="entity-panel-toggle"' in body
        assert 'id="entity-panel-add-site"' in body
        assert 'id="entity-panel-add-goal"' in body
        assert 'id="add-site-modal"' in body
        assert 'id="add-site-coords"' in body
        assert 'id="site-panel-create"' in body
        assert 'id="site-panel-create-viewshed"' in body
        assert 'id="site-panel-slug-preview"' in body
    finally:
        server.shutdown()
        server.server_close()


def test_delete_project_site(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    scaffold_project("mesh-demo", parent=projects_dir)
    preset_path = projects_dir / "mesh-demo" / "config.yaml"
    from peaky_finders.serve_sites import append_planned_site_to_preset

    slug = append_planned_site_to_preset(
        preset_path,
        name="Ridge Top",
        lat=39.6,
        lon=-119.4,
    )

    server, host, port, _thread = _start_server(projects_dir)
    try:
        conn = HTTPConnection(host, port, timeout=2)
        conn.request("DELETE", f"/api/p/mesh-demo/sites/{slug}")
        resp = conn.getresponse()
        payload = json.loads(resp.read().decode("utf-8"))
        assert resp.status == 200
        assert payload["deleted"] == slug

        conn = HTTPConnection(host, port, timeout=2)
        conn.request("DELETE", "/api/p/mesh-demo/sites/missing")
        resp = conn.getresponse()
        assert resp.status == 404
    finally:
        server.shutdown()
        server.server_close()


def test_delete_project_goal(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    project_dir = projects_dir / "goal-demo"
    project_dir.mkdir(parents=True)
    (project_dir / "config.yaml").write_text(
        """
simulation:
  modem: fixture-modem
  environment: fixture-desert
  transmitter: {height_m: 2.0, gain_dbi: 2.0, loss_db: 0.0}
  receiver: {height_m: 2.0, gain_dbi: 2.0, loss_db: 0.0}
display:
  colormap: rainbow
  min_dbm: -130.0
  max_dbm: -80.0
sites:
  hub:
    name: Hub
    loc: [39.5, -119.5]
  bridge:
    type: goal
    name: Bridge
    loc: [39.9, -119.3]
""".strip(),
        encoding="utf-8",
    )

    server, host, port, _thread = _start_server(projects_dir)
    try:
        conn = HTTPConnection(host, port, timeout=2)
        conn.request("DELETE", "/api/p/goal-demo/goals/bridge")
        resp = conn.getresponse()
        payload = json.loads(resp.read().decode("utf-8"))
        assert resp.status == 200
        assert payload["deleted"] == "bridge"
    finally:
        server.shutdown()
        server.server_close()


def test_run_serve_exits_on_keyboard_interrupt(monkeypatch) -> None:
    def _interrupt(*_args, **_kwargs) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("peaky_finders.serve_cli.waitress_serve", _interrupt)
    monkeypatch.setenv("PEAKY_PROJECTS", "/tmp/peaky-test-projects")
    args = build_serve_parser().parse_args(["--port", "9090"])
    assert run_serve(args) == 0
