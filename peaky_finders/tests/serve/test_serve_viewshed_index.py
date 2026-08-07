"""Bulk viewshed index API for ``peaky serve``."""

from __future__ import annotations

import json
import threading
from http.client import HTTPConnection
from io import BytesIO
from pathlib import Path

from PIL import Image

from peaky_finders.core.project.scaffold import scaffold_project
from peaky_finders.serve.app import make_serve_wsgi_app
from peaky_finders.serve.viewshed import viewshed_cache_png_api_path
from peaky_finders.serve.viewshed_index import build_viewshed_index, resolve_viewshed_cache_png


def _write_valid_png(path: Path) -> None:
    buf = BytesIO()
    Image.new("RGBA", (4, 4), (255, 0, 0, 255)).save(buf, format="PNG")
    path.write_bytes(buf.getvalue())


def _start_server(projects_dir: Path):
    from wsgiref.simple_server import make_server

    app = make_serve_wsgi_app(projects_dir, verbose=False, request_log=False)
    server = make_server("127.0.0.1", 0, app)
    host, port = server.server_address[:2]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, host, port, thread


def test_build_viewshed_index_ready_and_missing(tmp_path: Path, monkeypatch) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    project_dir = projects_dir / "demo"
    scaffold_project("demo", parent=projects_dir)

    from peaky_finders.core.preset import load_preset

    preset_path = project_dir / "config.yaml"
    preset = load_preset(preset_path)
    site_slug = next(iter(preset.sites))
    site = preset.sites[site_slug]

    from peaky_finders.core.viewshed.workspace import viewshed_workspace_digest
    from peaky_finders.core.rf.mapping import preset_to_request
    from peaky_finders.core.preset.paths import resolved_viewshed_root
    from peaky_finders.core.viewshed.workspace import resolved_viewshed_workdir

    req = preset_to_request(preset, float(site.lat), float(site.lon), site=site)
    digest = viewshed_workspace_digest(request=req)
    workdir = resolved_viewshed_workdir(
        digest=digest,
        viewshed_root=resolved_viewshed_root(preset_path),
    )
    workdir.mkdir(parents=True, exist_ok=True)
    _write_valid_png(workdir / "splat.png")
    (workdir / "request.json").write_text(req.model_dump_json(), encoding="utf-8")
    (workdir / "manifest.json").write_text(
        json.dumps({"bbox": {"north": 40.0, "south": 39.0, "east": -119.0, "west": -120.0}}),
        encoding="utf-8",
    )

    index = build_viewshed_index("demo", project_dir, preset.sites, preset=preset)
    entry = index["sites"][site_slug]
    assert entry["ready"] is True
    assert entry["url"] == viewshed_cache_png_api_path("demo", digest)
    assert len(entry["coordinates"]) == 4
    assert index["ready_count"] == 1
    assert index["total"] == len(preset.sites)


def test_viewshed_index_and_cache_png_http(tmp_path: Path, monkeypatch) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    project_dir = projects_dir / "demo"
    scaffold_project("demo", parent=projects_dir)

    from peaky_finders.core.preset import load_preset

    preset_path = project_dir / "config.yaml"
    preset = load_preset(preset_path)
    site_slug = next(iter(preset.sites))
    site = preset.sites[site_slug]

    from peaky_finders.core.viewshed.workspace import viewshed_workspace_digest
    from peaky_finders.core.rf.mapping import preset_to_request
    from peaky_finders.core.preset.paths import resolved_viewshed_root
    from peaky_finders.core.viewshed.workspace import resolved_viewshed_workdir

    req = preset_to_request(preset, float(site.lat), float(site.lon), site=site)
    digest = viewshed_workspace_digest(request=req)
    workdir = resolved_viewshed_workdir(
        digest=digest,
        viewshed_root=resolved_viewshed_root(preset_path),
    )
    workdir.mkdir(parents=True, exist_ok=True)
    _write_valid_png(workdir / "splat.png")
    (workdir / "request.json").write_text(req.model_dump_json(), encoding="utf-8")
    (workdir / "manifest.json").write_text(
        json.dumps({"bbox": {"north": 40.0, "south": 39.0, "east": -119.0, "west": -120.0}}),
        encoding="utf-8",
    )

    resolved = resolve_viewshed_cache_png(project_dir, digest)
    assert resolved is not None

    server, host, port, _thread = _start_server(projects_dir)
    try:
        conn = HTTPConnection(host, port, timeout=10)
        conn.request("GET", "/api/p/demo/viewsheds/index")
        resp = conn.getresponse()
        assert resp.status == 200
        body = json.loads(resp.read())
        assert body["sites"][site_slug]["ready"] is True

        png_url = body["sites"][site_slug]["url"]
        conn.request("GET", png_url)
        png_resp = conn.getresponse()
        assert png_resp.status == 200
        assert png_resp.getheader("Cache-Control", "").startswith("public")
        assert len(png_resp.read()) > 0
        conn.close()
    finally:
        server.shutdown()


def test_pair_link_strength_weak_and_strong() -> None:
    from peaky_finders.serve.links import _pair_link_strength
    from shapely.geometry import box

    big = box(-120.0, 39.0, -119.0, 41.0)
    assert (
        _pair_link_strength(
            big,
            big,
            lat_a=40.0,
            lon_a=-119.5,
            lat_b=39.5,
            lon_b=-119.5,
        )
        == "strong"
    )
    assert (
        _pair_link_strength(
            big,
            None,
            lat_a=40.0,
            lon_a=-119.5,
            lat_b=39.5,
            lon_b=-119.5,
        )
        == "weak"
    )
    assert (
        _pair_link_strength(
            None,
            None,
            lat_a=40.0,
            lon_a=-119.5,
            lat_b=39.5,
            lon_b=-119.5,
        )
        is None
    )
