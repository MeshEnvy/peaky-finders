"""``peaky serve`` web UI."""

from __future__ import annotations

import json
import threading
from http.client import HTTPConnection
from pathlib import Path

import pytest

from peaky_finders.new_cli import scaffold_project
from peaky_finders.serve_cli import (
    build_serve_parser,
    make_serve_handler,
    resolve_serve_projects_dir,
    run_serve,
)


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
