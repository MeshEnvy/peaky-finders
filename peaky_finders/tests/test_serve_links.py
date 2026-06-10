"""``peaky serve`` site link APIs."""

from __future__ import annotations

import json
import shutil
import threading
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import patch

import pytest

from fixture_paths import SAMPLE_PROJECT_CONFIG

from peaky_finders.serve_links import (
    canonical_site_pair,
    evaluate_site_pair_linked,
    load_coords_site_links,
    load_project_site_links,
)
from peaky_finders.serve_app import make_serve_wsgi_app
from peaky_finders.sites_job import load_preset_sites


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


def test_evaluate_site_pair_manual_link_skips_rf(tmp_path: Path) -> None:
    project_dir = tmp_path / "sample"
    shutil.copytree(SAMPLE_PROJECT_CONFIG.parent, project_dir)
    sites = load_preset_sites(project_dir / "config.yaml")

    with patch("peaky_finders.serve_links.mutual_hop_viable") as mock_rf:
        result = evaluate_site_pair_linked(project_dir, "hub", "peer-a", sites)

    assert result == {"a": "hub", "b": "peer-a", "linked": True, "manual": True}
    mock_rf.assert_not_called()


def test_evaluate_site_pair_out_of_range_skips_rf(tmp_path: Path) -> None:
    project_dir = tmp_path / "far"
    project_dir.mkdir()
    (project_dir / "config.yaml").write_text(
        """
simulation:
  provider: splatter
  radius_km: 1.0
  modem_presets:
    meshcore-us:
      frequency_mhz: 910.525
      bandwidth_khz: 62.5
      spreading_factor: 7
      coding_rate: 5
      implementation_margin_db: 3.0
      power_dbm: 22.0
      sensitivity_dbm: -121.0
  environment_presets:
    open:
      climate: desert
      polarization: vertical
      clutter_height_m: 1.0
  modem: meshcore-us
  environment: open
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

    with patch("peaky_finders.serve_links.mutual_hop_viable") as mock_rf:
        result = evaluate_site_pair_linked(project_dir, "a", "b", sites)

    assert result == {"a": "a", "b": "b", "linked": False, "manual": False}
    mock_rf.assert_not_called()


def _rf_batch_none(session, pairs, *, rf_json: str) -> list[bool]:
    return [False] * len(pairs)


def test_load_project_site_links_without_land(tmp_path: Path) -> None:
    project_dir = tmp_path / "sample"
    shutil.copytree(SAMPLE_PROJECT_CONFIG.parent, project_dir)
    cfg_path = project_dir / "config.yaml"
    text = cfg_path.read_text(encoding="utf-8")
    start = text.index("land:")
    end = text.index("sites:")
    cfg_path.write_text(text[:start] + text[end:], encoding="utf-8")
    sites = load_preset_sites(cfg_path)

    with (
        patch("peaky_finders.serve_links.splatter_session"),
        patch("peaky_finders.serve_links.ensure_dem_for_points"),
        patch("peaky_finders.serve_links.mutual_hop_batch", side_effect=_rf_batch_none),
    ):
        payload = load_project_site_links(project_dir, sites)

    assert len(payload["links"]) == 3


def test_load_project_site_links_manual_only(tmp_path: Path) -> None:
    project_dir = tmp_path / "sample"
    shutil.copytree(SAMPLE_PROJECT_CONFIG.parent, project_dir)
    sites = load_preset_sites(project_dir / "config.yaml")

    with (
        patch("peaky_finders.serve_links.splatter_session"),
        patch("peaky_finders.serve_links.ensure_dem_for_points"),
        patch("peaky_finders.serve_links.mutual_hop_batch", side_effect=_rf_batch_none) as mock_batch,
    ):
        payload = load_project_site_links(project_dir, sites)

    assert len(payload["links"]) == 3
    assert all(row["manual"] for row in payload["links"])
    assert len(payload["geojson"]["features"]) == 3
    for feature in payload["geojson"]["features"]:
        dist = feature["properties"]["distance_km"]
        assert isinstance(dist, (int, float))
        assert dist > 0


def test_api_project_links_manual(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    shutil.copytree(SAMPLE_PROJECT_CONFIG.parent, projects_dir / "sample")

    server, host, port, _thread = _start_server(projects_dir)
    try:
        with (
            patch("peaky_finders.serve_links.splatter_session"),
            patch("peaky_finders.serve_links.ensure_dem_for_points"),
            patch("peaky_finders.serve_links.mutual_hop_batch", side_effect=_rf_batch_none),
        ):
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
        with patch("peaky_finders.serve_links.mutual_hop_viable") as mock_rf:
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
        mock_rf.assert_not_called()
    finally:
        server.shutdown()
        server.server_close()


def test_load_coords_site_links_rf_batch(tmp_path: Path) -> None:
    project_dir = tmp_path / "sample"
    shutil.copytree(SAMPLE_PROJECT_CONFIG.parent, project_dir)
    sites = load_preset_sites(project_dir / "config.yaml")
    lat = float(next(iter(sites.values())).lat)
    lon = float(next(iter(sites.values())).lon)

    def _rf_batch(_session, pairs, *, rf_json: str) -> list[bool]:
        return [True] * len(pairs)

    with (
        patch("peaky_finders.serve_links.splatter_session"),
        patch("peaky_finders.serve_links.ensure_dem_for_points"),
        patch("peaky_finders.serve_links.mutual_hop_batch", side_effect=_rf_batch) as mock_batch,
    ):
        records = load_coords_site_links(project_dir, lat, lon, sites)

    assert mock_batch.called
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

    def _rf_batch(_session, pairs, *, rf_json: str) -> list[bool]:
        return [True] * len(pairs)

    with (
        patch("peaky_finders.serve_links.splatter_session"),
        patch("peaky_finders.serve_links.ensure_dem_for_points"),
        patch("peaky_finders.serve_links.mutual_hop_batch", side_effect=_rf_batch),
    ):
        all_records = load_coords_site_links(project_dir, lat, lon, sites)
        filtered = load_coords_site_links(
            project_dir,
            lat,
            lon,
            sites,
            exclude_site_slug="hub",
        )

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

        def _rf_batch(_session, pairs, *, rf_json: str) -> list[bool]:
            return [True] * len(pairs)

        with (
            patch("peaky_finders.serve_links.splatter_session"),
            patch("peaky_finders.serve_links.ensure_dem_for_points"),
            patch("peaky_finders.serve_links.mutual_hop_batch", side_effect=_rf_batch),
        ):
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
        assert 'id="show-links"' in body
        assert "Site links" in body
        assert 'id="viewshed-opacity"' in body
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
