"""Viewshed ensure API tests."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from peaky_finders.web.app import create_app
from peaky_finders.web.viewshed_service import viewshed_raster_record


def test_site_viewshed_endpoint_cached_only_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PEAKY_PROJECTS", "/tmp/peaky-test-projects")
    app = create_app()
    client = TestClient(app)

    with patch(
        "peaky_finders.web.app.get_site_viewshed",
        side_effect=FileNotFoundError("viewshed not cached for site 'foo'"),
    ):
        res = client.get("/api/projects/demo/viewsheds/foo?ensure=false")

    assert res.status_code == 404


def test_site_viewshed_endpoint_ensure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PEAKY_PROJECTS", "/tmp/peaky-test-projects")
    app = create_app()
    client = TestClient(app)
    payload = {
        "slug": "foo",
        "digest": "abc123",
        "url": "/api/projects/demo/viewsheds/foo/splat.png",
        "coordinates": [[-116.0, 40.0], [-115.0, 40.0], [-115.0, 39.0], [-116.0, 39.0]],
        "opacity": 0.5,
        "cached": True,
        "computed": False,
    }

    with patch("peaky_finders.web.app.get_site_viewshed", return_value=payload):
        res = client.get("/api/projects/demo/viewsheds/foo")

    assert res.status_code == 200
    assert res.json()["digest"] == "abc123"
    assert res.json()["cached"] is True


def test_viewshed_raster_record_shape() -> None:
    with patch(
        "peaky_finders.web.viewshed_service.resolve_splat_png_path",
        return_value=__import__("pathlib").Path("/tmp/splat.png"),
    ), patch(
        "peaky_finders.web.viewshed_service.preset_path_for_project",
        return_value=__import__("pathlib").Path("/tmp/config.yaml"),
    ), patch(
        "peaky_finders.web.viewshed_service.load_preset",
    ) as load_preset, patch(
        "peaky_finders.web.viewshed_service._viewsheds_root_for_preset",
        return_value=__import__("pathlib").Path("/tmp/viewsheds"),
    ), patch(
        "peaky_finders.web.viewshed_service.resolved_viewshed_workdir_for_coords",
        return_value=__import__("pathlib").Path("/tmp/viewsheds/abc123"),
    ), patch(
        "peaky_finders.web.viewshed_service._bounds_for_workdir",
        return_value={"north": 40.0, "south": 39.0, "east": -115.0, "west": -116.0, "rotation": 0.0},
    ), patch(
        "peaky_finders.web.viewshed_service.tile_layer_metadata",
        return_value={
            "tile_url": "/api/projects/demo/viewsheds/foo/tiles/{z}/{x}/{y}.png",
            "bounds": [-116.0, 39.0, -115.0, 40.0],
            "minzoom": 8,
            "maxzoom": 14,
            "source_pixels": [500, 500],
            "source_path": "splat.png",
        },
    ):
        preset = load_preset.return_value
        preset.sites = {"foo": __import__("types").SimpleNamespace(lat=39.5, lon=-115.5)}
        rec = viewshed_raster_record(project_slug="demo", site_slug="foo", computed=False)

    assert rec["slug"] == "foo"
    assert rec["digest"] == "abc123"
    assert rec["url"].endswith("/splat.png")
    assert "tile_url" in rec


def test_normalize_point_coords_matches_tile_query() -> None:
    from peaky_finders.web.viewshed_rasters import normalize_point_coords
    from peaky_finders.web.viewshed_service import _point_query

    lat, lon = 39.7560209, -119.4604554
    lat_n, lon_n = normalize_point_coords(lat, lon)
    q = _point_query(lat, lon)
    assert q == f"lat={lat_n:.6f}&lon={lon_n:.6f}"
    assert lat_n == 39.756021
    assert lon_n == -119.460455


def test_get_point_viewshed_normalizes_lookup_coords(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PEAKY_PROJECTS", "/tmp/peaky-test-projects")
    seen: list[tuple[float, float]] = []

    def fake_record(*, project_slug: str, lat: float, lon: float, computed: bool = False):
        seen.append((lat, lon))
        return {
            "slug": "at-abc",
            "lat": lat,
            "lon": lon,
            "url": f"/api/projects/{project_slug}/viewsheds/at/splat.png?lat={lat:.6f}&lon={lon:.6f}",
            "coordinates": [[-120, 40], [-119, 40], [-119, 39], [-120, 39]],
            "bounds": [-120.0, 39.0, -119.0, 40.0],
            "opacity": 0.5,
            "cached": True,
            "computed": computed,
        }

    with patch(
        "peaky_finders.web.viewshed_service.point_viewshed_is_cached",
        return_value=True,
    ), patch(
        "peaky_finders.web.viewshed_service.point_viewshed_raster_record",
        side_effect=fake_record,
    ):
        from peaky_finders.web.viewshed_service import get_point_viewshed

        rec = get_point_viewshed(
            project_slug="demo",
            lat=39.7560209,
            lon=-119.4604554,
            ensure=False,
        )

    assert seen == [(39.756021, -119.460455)]
    assert rec["lat"] == 39.756021
