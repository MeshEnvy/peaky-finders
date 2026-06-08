"""``peaky serve`` on-demand RF viewshed PNG API."""

from __future__ import annotations

import json
import threading
from http.client import HTTPConnection
from http.server import HTTPServer
from pathlib import Path

import pytest

from peaky_finders.new_cli import scaffold_project
from peaky_finders.serve_cli import make_serve_handler
from peaky_finders.serve_viewshed import (
    ServeViewshedError,
    ensure_site_viewshed_overlay,
    ensure_site_viewshed_png,
    image_coordinates_from_bbox,
    viewshed_meta_api_path,
    viewshed_png_api_path,
)


def _start_server(projects_dir: Path):
    handler = make_serve_handler(projects_dir)
    server = HTTPServer(("127.0.0.1", 0), handler)
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, host, port, thread


def test_viewshed_api_paths() -> None:
    assert viewshed_png_api_path("nevada", "hub") == "/api/p/nevada/viewsheds/hub/splat.png"
    assert viewshed_meta_api_path("nevada", "hub") == "/api/p/nevada/viewsheds/hub"


def test_image_coordinates_from_bbox_unrotated() -> None:
    coords = image_coordinates_from_bbox(
        {"north": 39.1, "south": 39.0, "east": -115.7, "west": -115.9, "rotation": 0.0}
    )
    assert coords[0][0] == pytest.approx(-115.9)
    assert coords[0][1] == pytest.approx(39.1)
    assert coords[1] == [pytest.approx(-115.7), pytest.approx(39.1)]
    assert coords[2] == [pytest.approx(-115.7), pytest.approx(39.0)]
    assert coords[3] == [pytest.approx(-115.9), pytest.approx(39.0)]


def test_ensure_site_viewshed_png_uses_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    project_dir = projects_dir / "demo"
    scaffold_project("demo", parent=projects_dir)

    fixed = project_dir / "build" / "viewsheds" / "abc123"
    fixed.mkdir(parents=True)
    png_bytes = b"\x89PNG\r\n\x1a\n"
    (fixed / "splat.png").write_bytes(png_bytes)
    (fixed / "request.json").write_text('{"lat": 39.5}', encoding="utf-8")

    monkeypatch.setattr(
        "peaky_finders.serve_viewshed.resolve_site_viewshed_workdir",
        lambda _project_dir, _preset, _site: fixed,
    )
    monkeypatch.setattr(
        "peaky_finders.serve_viewshed.viewshed_request_digest_matches",
        lambda _workdir, expected_workspace_digest: True,
    )

    def _fail_coverage(**_kwargs: object) -> int:
        raise AssertionError("coverage should not run when PNG is cached")

    monkeypatch.setattr("peaky_finders.serve_viewshed.run_viewshed_coverage", _fail_coverage)

    from peaky_finders.sites_job import load_preset_sites

    sites = load_preset_sites(project_dir / "config.yaml")
    png = ensure_site_viewshed_png(project_dir, "hub", sites["hub"])
    assert png.read_bytes() == png_bytes


def test_ensure_site_viewshed_png_generates_when_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    project_dir = projects_dir / "demo"
    scaffold_project("demo", parent=projects_dir)

    fixed = project_dir / "build" / "viewsheds" / "abc123"
    fixed.mkdir(parents=True)

    monkeypatch.setattr(
        "peaky_finders.serve_viewshed.resolve_site_viewshed_workdir",
        lambda _project_dir, _preset, _site: fixed,
    )
    monkeypatch.setattr(
        "peaky_finders.serve_viewshed.viewshed_request_digest_matches",
        lambda _workdir, expected_workspace_digest: False,
    )

    calls: list[str] = []

    def _fake_coverage(**kwargs: object) -> int:
        calls.append("coverage")
        data_dir = kwargs["data_dir"]
        Path(data_dir).joinpath("output.ppm").write_bytes(b"ppm")
        Path(data_dir).joinpath("output.kml").write_text(
            """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <GroundOverlay>
    <LatLonBox>
      <north>39.1</north><south>39.0</south><east>-115.7</east><west>-115.9</west>
    </LatLonBox>
  </GroundOverlay>
</kml>""",
            encoding="utf-8",
        )
        return 0

    def _fake_raster(**kwargs: object) -> None:
        calls.append("raster")
        data_dir = Path(kwargs["data_dir"])
        data_dir.joinpath("splat.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    monkeypatch.setattr("peaky_finders.serve_viewshed.run_viewshed_coverage", _fake_coverage)
    monkeypatch.setattr("peaky_finders.serve_viewshed.ensure_splat_raster_png", _fake_raster)

    from peaky_finders.sites_job import load_preset_sites

    sites = load_preset_sites(project_dir / "config.yaml")
    png = ensure_site_viewshed_png(project_dir, "hub", sites["hub"])
    assert png.name == "splat.png"
    assert calls == ["coverage", "raster"]


def test_serve_viewshed_png_endpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    scaffold_project("rf-demo", parent=projects_dir)

    png_bytes = b"\x89PNG\r\n\x1a\nfake"

    def _fake_ensure(project_dir: Path, site_slug: str, site, *, verbose: bool = False) -> Path:
        del project_dir, site_slug, site, verbose
        out = tmp_path / "cached.png"
        out.write_bytes(png_bytes)
        return out

    monkeypatch.setattr("peaky_finders.serve_cli.ensure_site_viewshed_png", _fake_ensure)

    server, host, port, _thread = _start_server(projects_dir)
    try:
        conn = HTTPConnection(host, port, timeout=2)
        conn.request("GET", "/api/p/rf-demo/viewsheds/hub/splat.png")
        resp = conn.getresponse()
        body = resp.read()
        assert resp.status == 200
        assert resp.getheader("Content-Type") == "image/png"
        assert body == png_bytes
    finally:
        server.shutdown()
        server.server_close()


def test_serve_viewshed_meta_endpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    scaffold_project("mesh-demo", parent=projects_dir)

    def _fake_overlay(
        project_slug: str,
        project_dir: Path,
        site_slug: str,
        site,
        *,
        verbose: bool = False,
    ) -> dict[str, object]:
        del project_dir, site, verbose
        return {
            "slug": site_slug,
            "url": viewshed_png_api_path(project_slug, site_slug),
            "coordinates": [[-115.9, 39.1], [-115.7, 39.1], [-115.7, 39.0], [-115.9, 39.0]],
        }

    monkeypatch.setattr("peaky_finders.serve_cli.ensure_site_viewshed_overlay", _fake_overlay)

    server, host, port, _thread = _start_server(projects_dir)
    try:
        conn = HTTPConnection(host, port, timeout=2)
        conn.request("GET", "/api/p/mesh-demo/viewsheds/hub")
        resp = conn.getresponse()
        payload = json.loads(resp.read().decode("utf-8"))
        assert resp.status == 200
        assert payload["project"] == "mesh-demo"
        assert payload["slug"] == "hub"
        assert payload["url"] == "/api/p/mesh-demo/viewsheds/hub/splat.png"
        assert len(payload["coordinates"]) == 4
    finally:
        server.shutdown()
        server.server_close()


def test_project_page_loads_viewsheds_on_demand(tmp_path: Path) -> None:
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
        assert "/static/project-map.js" in body
        assert '"mesh-demo"' in body

        conn = HTTPConnection(host, port, timeout=2)
        conn.request("GET", "/static/project-map.js")
        js_resp = conn.getresponse()
        js_body = js_resp.read().decode("utf-8")
        assert js_resp.status == 200
        assert "loadAllViewsheds" in js_body
        assert "loadViewshedForSite" in js_body
        assert "viewshedMetaUrl" in js_body
        assert "setViewshedVisible" in js_body
    finally:
        server.shutdown()
        server.server_close()


def test_ensure_site_viewshed_overlay_requires_bounds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    project_dir = projects_dir / "demo"
    scaffold_project("demo", parent=projects_dir)

    fixed = project_dir / "build" / "viewsheds" / "abc123"
    fixed.mkdir(parents=True)
    (fixed / "splat.png").write_bytes(b"png")

    monkeypatch.setattr(
        "peaky_finders.serve_viewshed.resolve_site_viewshed_workdir",
        lambda _project_dir, _preset, _site: fixed,
    )
    monkeypatch.setattr(
        "peaky_finders.serve_viewshed.ensure_site_viewshed_png",
        lambda *_args, **_kwargs: fixed / "splat.png",
    )
    monkeypatch.setattr("peaky_finders.serve_viewshed.load_viewshed_bounds", lambda _wd: None)

    from peaky_finders.sites_job import load_preset_sites

    sites = load_preset_sites(project_dir / "config.yaml")
    with pytest.raises(ServeViewshedError, match="bounds"):
        ensure_site_viewshed_overlay("demo", project_dir, "hub", sites["hub"])


def test_load_preset_for_coverage_ignores_site_suggestions(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    project_dir = projects_dir / "nevada-like"
    scaffold_project("nevada-like", parent=projects_dir)
    config_path = project_dir / "config.yaml"
    raw = config_path.read_text(encoding="utf-8")
    config_path.write_text(
        raw.replace(
            "  exclude: []\n\nsites:",
            "  exclude: []\n\nsuggest:\n"
            "  strategy: mesh-backbone\n"
            "  mesh_backbone:\n"
            "    goals:\n"
            "      russel-bridge:\n"
            "        loc: russell-peak\n\nsites:",
        ),
        encoding="utf-8",
    )

    from peaky_finders.sites_job import load_preset, load_preset_for_coverage

    with pytest.raises(Exception):
        load_preset(config_path)

    preset = load_preset_for_coverage(config_path)
    assert preset.land is not None
    assert preset.sites["hub"].name == "Hub"


def test_viewshed_prefetch_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    scaffold_project("demo", parent=projects_dir)

    calls: list[tuple[float, float]] = []

    def _fake_prefetch(project_dir: Path, lat: float, lon: float, *, verbose: bool = False) -> Path:
        calls.append((lat, lon))
        return project_dir / "build" / "viewsheds" / "warm" / "splat.png"

    monkeypatch.setattr("peaky_finders.serve_cli.ensure_coords_viewshed_png", _fake_prefetch)

    server, host, port, _thread = _start_server(projects_dir)
    try:
        conn = HTTPConnection(host, port, timeout=2)
        conn.request("GET", "/api/p/demo/viewsheds/prefetch?lat=39.6&lon=-119.4")
        resp = conn.getresponse()
        payload = json.loads(resp.read().decode("utf-8"))
        assert resp.status == 200
        assert payload["ok"] is True
        assert payload["lat"] == 39.6
        assert payload["lon"] == -119.4
        assert calls == [(39.6, -119.4)]
    finally:
        server.shutdown()
        server.server_close()
