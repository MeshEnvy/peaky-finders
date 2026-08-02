"""``peaky serve`` site link APIs."""

from __future__ import annotations

import json
import threading
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import patch

from shapely.geometry import box

from peaky_finders.serve.links import (
    ServeLinksError,
    canonical_site_pair,
    compute_single_site_links,
    evaluate_site_pair_linked,
    load_coords_site_links,
    load_project_site_links,
)
from peaky_finders.serve.app import make_serve_wsgi_app
from peaky_finders.core.preset import load_preset_sites

_COVERING_FP = box(-120.0, 39.0, -119.0, 41.0)

_LINKS_PROJECT_YAML = """
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
  hub:
    name: Hub Site
    loc: [40.42619, -119.5]
  peer-a:
    name: Peer A
    loc: [39.90951, -119.4]
  peer-b:
    name: Peer B
    loc: [39.91342, -119.3]
  peer-c:
    name: Peer C
    loc: [40.40376, -119.6]
links:
  - [hub, peer-a]
  - [hub, peer-b]
  - [hub, peer-c]
""".strip()


def _write_links_project(project_dir: Path) -> Path:
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "config.yaml").write_text(_LINKS_PROJECT_YAML + "\n", encoding="utf-8")
    return project_dir


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
    project_dir = _write_links_project(tmp_path / "sample")
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
    project_dir = _write_links_project(tmp_path / "sample")
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

    project_dir = _write_links_project(tmp_path / "sample")
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


def test_links_cache_survives_site_rename(tmp_path: Path) -> None:
    """Display-only YAML edits (name) must not invalidate the warm links cache."""
    from peaky_finders.core.preset import load_preset_for_coverage
    from peaky_finders.serve.links import links_input_fingerprint, store_project_site_links_cache
    from peaky_finders.serve.sites import update_site_in_preset

    project_dir = _write_links_project(tmp_path / "sample")
    sites = load_preset_sites(project_dir / "config.yaml")
    ready = {
        "status": "ready",
        "links": [{"a": "hub", "b": "peer-a", "linked": True, "manual": False}],
        "geojson": {"type": "FeatureCollection", "features": []},
    }
    store_project_site_links_cache(project_dir, sites, ready)

    update_site_in_preset(project_dir / "config.yaml", "hub", name="Hub Renamed")
    sites_after = load_preset_sites(project_dir / "config.yaml")
    assert sites_after["hub"].name == "Hub Renamed"
    assert float(sites_after["hub"].lat) == float(sites["hub"].lat)
    assert float(sites_after["hub"].lon) == float(sites["hub"].lon)

    with patch("peaky_finders.serve.links.compute_project_site_links") as mock_compute:
        payload = load_project_site_links(project_dir, sites_after)

    assert payload == ready
    mock_compute.assert_not_called()
    preset = load_preset_for_coverage(project_dir / "config.yaml")
    assert links_input_fingerprint(sites, preset=preset) == links_input_fingerprint(
        sites_after, preset=preset
    )


def test_links_fingerprint_changes_when_site_height_changes(tmp_path: Path) -> None:
    from peaky_finders.core.preset import load_preset_for_coverage
    from peaky_finders.serve.links import links_input_fingerprint
    from peaky_finders.serve.sites import update_site_in_preset

    project_dir = _write_links_project(tmp_path / "sample")
    sites = load_preset_sites(project_dir / "config.yaml")
    preset = load_preset_for_coverage(project_dir / "config.yaml")
    before = links_input_fingerprint(sites, preset=preset)

    update_site_in_preset(project_dir / "config.yaml", "hub", height_m=25.0)
    sites_after = load_preset_sites(project_dir / "config.yaml")
    after = links_input_fingerprint(sites_after, preset=preset)
    assert before != after


def test_warm_links_returns_cache_without_rerun(tmp_path: Path) -> None:
    from peaky_finders.serve.link_jobs import reset_link_jobs_for_tests, warm_project_site_links
    from peaky_finders.serve.links import store_project_site_links_cache

    reset_link_jobs_for_tests()
    project_dir = _write_links_project(tmp_path / "sample")
    sites = load_preset_sites(project_dir / "config.yaml")
    ready = {
        "status": "ready",
        "links": [{"a": "hub", "b": "peer-a", "linked": True, "manual": False}],
        "geojson": {"type": "FeatureCollection", "features": []},
    }
    store_project_site_links_cache(project_dir, sites, ready)

    with patch("peaky_finders.serve.link_jobs.ensure_project_warm") as mock_ensure:
        result = warm_project_site_links("sample", project_dir, sites)

    assert result["status"] == "ready"
    assert result["links"] == ready["links"]
    mock_ensure.assert_not_called()
    reset_link_jobs_for_tests()


def test_warm_links_starts_background_scheduler(tmp_path: Path) -> None:
    from peaky_finders.serve.link_jobs import reset_link_jobs_for_tests, warm_project_site_links

    reset_link_jobs_for_tests()
    project_dir = _write_links_project(tmp_path / "sample")
    sites = load_preset_sites(project_dir / "config.yaml")

    with patch(
        "peaky_finders.serve.link_jobs.ensure_project_warm",
        return_value={"project": "sample", "status": "running", "enqueued": 4},
    ) as mock_ensure:
        result = warm_project_site_links("sample", project_dir, sites)

    assert result["status"] == "running"
    mock_ensure.assert_called_once()
    reset_link_jobs_for_tests()


def test_warm_links_publishes_once_after_footprints(tmp_path: Path) -> None:
    from peaky_finders.serve.link_jobs import reset_link_jobs_for_tests, warm_project_site_links

    reset_link_jobs_for_tests()
    project_dir = _write_links_project(tmp_path / "sample")
    sites = load_preset_sites(project_dir / "config.yaml")
    ready = {
        "status": "ready",
        "links": [{"a": "hub", "b": "peer-a", "linked": True, "manual": False}],
        "geojson": {"type": "FeatureCollection", "features": [{"type": "Feature"}]},
    }
    published: list[dict[str, object]] = []

    with patch(
        "peaky_finders.serve.project_warm_scheduler._publish_links",
        side_effect=lambda _slug, payload: published.append(dict(payload)),
    ):
        with patch(
            "peaky_finders.serve.project_warm_scheduler.read_existing_footprints",
            return_value={slug: None for slug in sites},
        ):
            with patch(
                "peaky_finders.serve.project_warm_scheduler.vectorize_missing_footprints",
                side_effect=lambda *_a, **_k: {slug: object() for slug in sites},
            ):
                with patch(
                    "peaky_finders.serve.project_warm_scheduler.compute_project_site_links",
                    return_value=ready,
                ) as compute:
                    with patch(
                        "peaky_finders.serve.project_warm_scheduler._enqueue_site_if_missing",
                        return_value=False,
                    ):
                        result = warm_project_site_links("sample", project_dir, sites)
                        assert result["status"] == "running"
                        from peaky_finders.serve.project_warm_scheduler import (
                            _refresh_project_links,
                        )

                        _refresh_project_links("sample", project_dir)

    assert published
    assert published[-1]["links"] == ready["links"]
    compute.assert_called()
    reset_link_jobs_for_tests()


def test_api_project_links_manual(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    _write_links_project(projects_dir / "sample")

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
    _write_links_project(projects_dir / "sample")

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
    inst.read_site_footprint.return_value = site_fp
    inst.vectorize_site_footprint.return_value = site_fp
    return engine, inst


def test_compute_single_site_links_ready(tmp_path: Path) -> None:
    project_dir = _write_links_project(tmp_path / "sample")
    sites = load_preset_sites(project_dir / "config.yaml")

    mock_engine, inst = _mock_engine()
    try:
        result = compute_single_site_links(project_dir, "hub", sites)
    finally:
        mock_engine.stop()

    assert result["status"] == "ready"
    assert result["site"] == "hub"
    assert result["center_footprint"] is True
    assert result["missing"] == []
    # Manual pairs (hub↔a/b/c) plus RF links via covering footprints.
    slugs_linked = {row["b"] if row["a"] == "hub" else row["a"] for row in result["links"]}
    assert slugs_linked == {"peer-a", "peer-b", "peer-c"}
    assert len(result["geojson"]["features"]) == len(result["links"])
    # Never runs splatter.
    inst.ensure_site_footprint.assert_not_called()


def test_compute_single_site_links_missing_neighbors(tmp_path: Path) -> None:
    project_dir = _write_links_project(tmp_path / "sample")
    sites = load_preset_sites(project_dir / "config.yaml")

    mock_engine, inst = _mock_engine()

    def read_fp(_pd, _preset, site, **_kw):
        return _COVERING_FP if float(site.lat) == float(sites["peer-a"].lat) else None

    inst.read_site_footprint.side_effect = read_fp
    inst.vectorize_site_footprint.return_value = None
    try:
        result = compute_single_site_links(project_dir, "peer-a", sites)
    finally:
        mock_engine.stop()

    assert result["status"] == "partial"
    assert result["center_footprint"] is True
    # Manual pair to hub still resolves; RF neighbors without footprints are reported.
    assert {row["a"] for row in result["links"]} | {row["b"] for row in result["links"]} >= {"hub", "peer-a"}
    assert result["missing"] == ["peer-b", "peer-c"]


def test_compute_single_site_links_unknown_slug(tmp_path: Path) -> None:
    project_dir = _write_links_project(tmp_path / "sample")
    sites = load_preset_sites(project_dir / "config.yaml")
    try:
        compute_single_site_links(project_dir, "nope", sites)
        raise AssertionError("expected ServeLinksError")
    except ServeLinksError:
        pass


def test_api_single_site_links(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    _write_links_project(projects_dir / "sample")

    mock_engine, _inst = _mock_engine()
    server, host, port, _thread = _start_server(projects_dir)
    try:
        conn = HTTPConnection(host, port, timeout=10)
        conn.request("GET", "/api/p/sample/sites/hub/links")
        resp = conn.getresponse()
        body = json.loads(resp.read().decode("utf-8"))
        assert resp.status == 200
        assert body["project"] == "sample"
        assert body["site"] == "hub"
        assert body["links"]
        assert body["geojson"]["features"]
    finally:
        mock_engine.stop()
        server.shutdown()
        server.server_close()


def test_load_coords_site_links_viewshed_batch(tmp_path: Path) -> None:
    project_dir = _write_links_project(tmp_path / "sample")
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
    project_dir = _write_links_project(tmp_path / "sample")
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
    _write_links_project(projects_dir / "sample")

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
    _write_links_project(projects_dir / "sample")

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
