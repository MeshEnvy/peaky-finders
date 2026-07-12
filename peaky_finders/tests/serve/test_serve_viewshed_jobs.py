"""Background viewshed warm jobs."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from peaky_finders.core.project.scaffold import scaffold_project
from peaky_finders.serve.events import reset_serve_event_hub_for_tests
from peaky_finders.serve.viewshed_jobs import reset_viewshed_warm_jobs_for_tests, warm_site_viewshed
from peaky_finders.serve.viewshed_sim import ViewshedSimOverrides
from peaky_finders.core.preset import load_preset_sites


def test_warm_supersedes_stale_site_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    scaffold_project("demo", parent=projects_dir)
    project_dir = projects_dir / "demo"
    sites = load_preset_sites(project_dir / "config.yaml")

    hub = reset_serve_event_hub_for_tests()
    reset_viewshed_warm_jobs_for_tests()
    ready_urls: list[str] = []
    real_publish = hub.publish

    def _track_publish(project_slug: str, event) -> None:
        if event.name == "viewshed" and event.data.get("status") == "ready":
            ready_urls.append(str(event.data.get("url")))
        real_publish(project_slug, event)

    monkeypatch.setattr(hub, "publish", _track_publish)
    monkeypatch.setattr(
        "peaky_finders.serve.viewshed_jobs.site_viewshed_overlay_if_ready",
        lambda *_args, **_kwargs: None,
    )

    started = threading.Event()
    proceed = threading.Event()

    def _slow_overlay(
        _project_slug: str,
        _project_dir: Path,
        _site_slug: str,
        _site,
        *,
        sim_overrides: ViewshedSimOverrides | None = None,
        verbose: bool = False,
    ) -> dict[str, object]:
        del _project_dir, _site, verbose
        started.set()
        assert proceed.wait(timeout=2.0)
        radius = 60.0 if sim_overrides is None else float(sim_overrides.radius_km or 60.0)
        return {
            "slug": _site_slug,
            "url": f"/api/p/demo/viewsheds/hub/splat.png?radius_km={radius:g}",
            "coordinates": [[-115.9, 39.1], [-115.7, 39.1], [-115.7, 39.0], [-115.9, 39.0]],
        }

    monkeypatch.setattr(
        "peaky_finders.serve.viewshed_jobs.ensure_site_viewshed_overlay",
        _slow_overlay,
    )

    first = warm_site_viewshed(
        "demo",
        project_dir,
        "hub",
        sites["hub"],
        sim_overrides=ViewshedSimOverrides(radius_km=60.0, raster_dimension=500),
        verbose=False,
    )
    assert first["status"] == "queued"
    assert started.wait(timeout=2.0)

    second = warm_site_viewshed(
        "demo",
        project_dir,
        "hub",
        sites["hub"],
        sim_overrides=ViewshedSimOverrides(radius_km=100.0, raster_dimension=500),
        verbose=False,
    )
    assert second["status"] == "queued"
    proceed.set()

    deadline = threading.Event()
    deadline.wait(timeout=2.0)
    assert ready_urls == ["/api/p/demo/viewsheds/hub/splat.png?radius_km=100"]
