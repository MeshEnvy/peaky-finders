"""``peaky serve`` site link APIs."""

from __future__ import annotations

import json
import shutil
import threading
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import patch

from shapely.geometry import box

from fixture_paths import SAMPLE_PROJECT_CONFIG

from peaky_finders.serve.links import (
    canonical_site_pair,
    evaluate_site_pair_linked,
    load_coords_site_links,
    load_project_site_links,
)
from peaky_finders.serve.app import make_serve_wsgi_app
from peaky_finders.core.preset import load_preset_sites

_COVERING_FP = box(-120.0, 39.0, -119.0, 41.0)


def _start_server(projects_dir: Path):
    from wsgiref.simple_server import make_server

    app = make_serve_wsgi_app(projects_dir, verbose=False, request_log=False)
    server = make_server("127.0.0.1", 0, app)
    host, port = server.server_address[:2]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, host, port, thread


def test_canonical_site_pair_orders_slugs() -> None:
    assert canonical_site_pair("b", "a") == ("a", "b")


def test_evaluate_site_pair_manual_link_skips_viewshed(tmp_path: Path) -> None:
    project_dir = tmp_path / "sample"
    shutil.copytree(SAMPLE_PROJECT_CONFIG.parent, project_dir)
    sites = load_preset_sites(project_dir / "config.yaml")

    with patch("peaky_finders.serve.links.get_viewshed_engine") as mock_engine:
        result = evaluate_site_pair_linked(project_dir, "hub", "peer-a", sites)

    assert result == {"a": "hub", "b": "peer-a", "linked": True, "manual": True}
    mock_engine.assert_not_called()


def test_evaluate_site_pair_out_of_range_skips_viewshed(tmp_path: Path) -> None:
    project_dir = tmp_path / "far"
    project_dir.mkdir()
    (project_dir / "config.yaml").write_text(
        """
simulation:
  provider: splatter
  radius_km: 1.0
  modem: fixture-modem
  environment: fixture-desert
  transmitter: {height_m: 2.0, gain_dbi: 3.0, loss_db: 2.0}
  receiver: {height_m: 2.0, gain_dbi: 3.0, loss_db: 2.0}
display:
  colormap: plasma
  min_dbm: -130.0
  max_dbm: -80.0
sites:
  a:
    name: A
    loc: [39.0, -119.0]
  b:
    name: B
    loc: [40.0, -118.0]
links: []
""".strip(),
        encoding="utf-8",
    )
    sites = load_preset_sites(project_dir / "config.yaml")

    with patch("peaky_finders.serve.links.get_viewshed_engine") as mock_engine:
        result = evaluate_site_pair_linked(project_dir, "a", "b", sites)

    assert result == {"a": "a", "b": "b", "linked": False, "manual": False}
    mock_engine.assert_not_called()


def test_load_project_site_links_minimal_preset(tmp_path: Path) -> None:
    project_dir = tmp_path / "sample"
    shutil.copytree(SAMPLE_PROJECT_CONFIG.parent, project_dir)
    sites = load_preset_sites(project_dir / "config.yaml")

    with patch("peaky_finders.serve.links.load_site_viewshed_footprint") as mock_fp:
        payload = load_project_site_links(project_dir, sites)

    assert payload["status"] == "pending"
    assert len(payload["links"]) == 3
    assert all(row["manual"] for row in payload["links"])
    assert len(payload["geojson"]["features"]) == 3
    mock_fp.assert_not_called()
    for feature in payload["geojson"]["features"]:
        dist = feature["properties"]["distance_km"]
        assert isinstance(dist, (int, float))
        assert dist > 0


def test_load_project_site_links_uses_warm_cache(tmp_path: Path) -> None:
    from peaky_finders.serve.links import store_project_site_links_cache

    project_dir = tmp_path / "sample"
    shutil.copytree(SAMPLE_PROJECT_CONFIG.parent, project_dir)
    sites = load_preset_sites(project_dir / "config.yaml")
    ready = {
        "status": "ready",
        "links": [{"a": "hub", "b": "peer-a", "linked": True, "manual": False}],
        "geojson": {"type": "FeatureCollection", "features": []},
    }
    store_project_site_links_cache(project_dir, sites, ready)

    with patch("peaky_finders.serve.links.compute_project_site_links") as mock_compute:
        payload = load_project_site_links(project_dir, sites)

    assert payload == ready
    mock_compute.assert_not_called()


def test_api_project_links_manual(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    shutil.copytree(SAMPLE_PROJECT_CONFIG.parent, projects_dir / "sample")

    server, host, port, _thread = _start_server(projects_dir)
    try:
        with patch("peaky_finders.serve.links.load_site_viewshed_footprint", return_value=None):
            conn = HTTPConnection(host, port, timeout=2)
            conn.request("GET", "/api/p/sample/links")
            resp = conn.getresponse()
            payload = json.loads(resp.read().decode("utf-8"))

        assert resp.status == 200
        assert payload["project"] == "sample"
        assert len(payload["links"]) == 3
        assert payload["geojson"]["type"] == "FeatureCollection"
    finally:
        server.shutdown()
        server.server_close()


def test_api_project_link_pair_manual(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    shutil.copytree(SAMPLE_PROJECT_CONFIG.parent, projects_dir / "sample")

    server, host, port, _thread = _start_server(projects_dir)
    try:
        with patch("peaky_finders.serve.links.load_site_viewshed_footprint") as mock_fp:
            conn = HTTPConnection(host, port, timeout=2)
            conn.request("GET", "/api/p/sample/links/hub/peer-b")
            resp = conn.getresponse()
            payload = json.loads(resp.read().decode("utf-8"))

        assert resp.status == 200
        assert payload == {
            "project": "sample",
            "a": "hub",
            "b": "peer-b",
            "linked": True,
            "manual": True,
        }
        mock_fp.assert_not_called()
    finally:
        server.shutdown()
        server.server_close()


def _mock_engine(*, site_fp=_COVERING_FP, coords_fp=_COVERING_FP):
    engine = patch("peaky_finders.serve.links.get_viewshed_engine").start()
    inst = engine.return_value
    inst.ensure_site_footprint.return_value = site_fp
    inst.ensure_coords_footprint.return_value = coords_fp
    return engine, inst


def test_load_coords_site_links_viewshed_batch(tmp_path: Path) -> None:
    project_dir = tmp_path / "sample"
    shutil.copytree(SAMPLE_PROJECT_CONFIG.parent, project_dir)
    sites = load_preset_sites(project_dir / "config.yaml")
    lat = float(next(iter(sites.values())).lat)
    lon = float(next(iter(sites.values())).lon)

    mock_engine, _inst = _mock_engine()
    try:
        records = load_coords_site_links(project_dir, lat, lon, sites)
    finally:
        mock_engine.stop()

    assert all(row["linked"] for row in records)
    assert all(not row["manual"] for row in records)
    assert all("distance_km" in row for row in records)
    assert all(isinstance(row["distance_km"], (int, float)) for row in records)


def test_load_coords_site_links_exclude_edited_site(tmp_path: Path) -> None:
    project_dir = tmp_path / "sample"
    shutil.copytree(SAMPLE_PROJECT_CONFIG.parent, project_dir)
    sites = load_preset_sites(project_dir / "config.yaml")
    hub = sites["hub"]
    lat = float(hub.lat)
    lon = float(hub.lon)

    mock_engine, _inst = _mock_engine()
    try:
        all_records = load_coords_site_links(project_dir, lat, lon, sites)
        filtered = load_coords_site_links(
            project_dir,
            lat,
            lon,
            sites,
            exclude_site_slug="hub",
        )
    finally:
        mock_engine.stop()

    assert any(row["slug"] == "hub" for row in all_records)
    assert not any(row["slug"] == "hub" for row in filtered)


def test_api_sites_prefetch_exclude_site(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    shutil.copytree(SAMPLE_PROJECT_CONFIG.parent, projects_dir / "sample")

    server, host, port, _thread = _start_server(projects_dir)
    try:
        sites = load_preset_sites(projects_dir / "sample" / "config.yaml")
        hub = sites["hub"]
        lat = float(hub.lat)
        lon = float(hub.lon)

        with (
            patch(
                "peaky_finders.serve.plss.plss_for_point",
                return_value="NV210300N0230E0SN360ASENW",
            ),
            patch("peaky_finders.serve.links.get_viewshed_engine") as mock_engine,
        ):
            inst = mock_engine.return_value
            inst.ensure_site_footprint.return_value = _COVERING_FP
            inst.ensure_coords_footprint.return_value = _COVERING_FP
            conn = HTTPConnection(host, port, timeout=2)
            conn.request(
                "GET",
                f"/api/p/sample/sites/prefetch?lat={lat}&lon={lon}&exclude_site=hub",
            )
            resp = conn.getresponse()
            payload = json.loads(resp.read().decode("utf-8"))

        assert resp.status == 200
        slugs = {row["slug"] for row in payload["links"]}
        assert "hub" not in slugs
        feature_slugs = {
            feature["properties"]["slug"] for feature in payload["links_geojson"]["features"]
        }
        assert "hub" not in feature_slugs
    finally:
        server.shutdown()
        server.server_close()


def test_project_page_includes_site_links_toggle(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    shutil.copytree(SAMPLE_PROJECT_CONFIG.parent, projects_dir / "sample")

    server, host, port, _thread = _start_server(projects_dir)
    try:
        conn = HTTPConnection(host, port, timeout=2)
        conn.request("GET", "/p/sample/")
        resp = conn.getresponse()
        body = resp.read().decode("utf-8")
        assert resp.status == 200
        assert "/static/project-map.js" in body

        conn.request("GET", "/static/project-map.js")
        js_resp = conn.getresponse()
        js_body = js_resp.read().decode("utf-8")
        assert js_resp.status == 200
        assert "site-links-line" in js_body
        assert "site-links-label" in js_body
        assert "setViewshedOpacity" in js_body
        assert "viewshed-opacity" in js_body
    finally:
        server.shutdown()
        server.server_close()
