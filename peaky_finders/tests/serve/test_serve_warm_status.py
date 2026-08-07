"""Warm status API for ``peaky serve``."""

from __future__ import annotations

import json
import threading
from http.client import HTTPConnection
from pathlib import Path

from peaky_finders.serve.app import make_serve_wsgi_app


def _write_min_project(project_dir: Path) -> None:
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "config.yaml").write_text(
        """
simulation:
  provider: splatter
  radius_km: 10.0
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
    loc: [40.0, -119.5]
""".strip()
        + "\n",
        encoding="utf-8",
    )


def _start_server(projects_dir: Path):
    from wsgiref.simple_server import make_server

    app = make_serve_wsgi_app(projects_dir, verbose=False, request_log=False)
    server = make_server("127.0.0.1", 0, app)
    host, port = server.server_address[:2]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, host, port


def test_warm_status_endpoint(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    _write_min_project(projects_dir / "demo")
    server, host, port = _start_server(projects_dir)
    try:
        conn = HTTPConnection(host, port, timeout=10)
        conn.request("GET", "/api/p/demo/warm/status")
        resp = conn.getresponse()
        assert resp.status == 200
        body = json.loads(resp.read().decode("utf-8"))
        assert body["project"] == "demo"
        assert body["sites_total"] == 1
        assert "coverage_queue" in body
        assert body["coverage_queue"]["workers"] >= 1
        assert "links" in body
        assert body["links"]["status"] in {"pending", "ready"}
    finally:
        server.shutdown()
