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
            "source_path": "output.ppm",
        },
    ):
        preset = load_preset.return_value
        preset.sites = {"foo": __import__("types").SimpleNamespace(lat=39.5, lon=-115.5)}
        rec = viewshed_raster_record(project_slug="demo", site_slug="foo", computed=False)

    assert rec["slug"] == "foo"
    assert rec["digest"] == "abc123"
    assert rec["url"].endswith("/splat.png")
    assert "tile_url" in rec
