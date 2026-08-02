"""Background viewshed warm jobs."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from peaky_finders.core.project.scaffold import scaffold_project
from peaky_finders.core.preset import load_preset_sites
from peaky_finders.serve.events import reset_serve_event_hub_for_tests
from peaky_finders.serve.viewshed_jobs import reset_viewshed_warm_jobs_for_tests, warm_site_viewshed
from peaky_finders.serve.viewshed_sim import ViewshedSimOverrides


def test_warm_returns_ready_when_overlay_cached(
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
    published: list[dict[str, object]] = []

    def _track_publish(_project_slug: str, event) -> None:
        if event.name == "viewshed":
            published.append(dict(event.data))

    monkeypatch.setattr(hub, "publish", _track_publish)
    overlay = {
        "slug": "hub",
        "url": "/api/p/demo/viewsheds/hub/splat.png",
        "coordinates": [[-115.9, 39.1], [-115.7, 39.1], [-115.7, 39.0], [-115.9, 39.0]],
    }
    monkeypatch.setattr(
        "peaky_finders.serve.viewshed_jobs.site_viewshed_overlay_if_ready",
        lambda *_args, **_kwargs: overlay,
    )

    result = warm_site_viewshed(
        "demo",
        project_dir,
        "hub",
        sites["hub"],
        verbose=False,
    )
    assert result["status"] == "ready"
    assert result["url"] == overlay["url"]
    assert len(published) == 1


def test_warm_queues_via_priority_bump(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    scaffold_project("demo", parent=projects_dir)
    project_dir = projects_dir / "demo"
    sites = load_preset_sites(project_dir / "config.yaml")

    reset_viewshed_warm_jobs_for_tests()
    monkeypatch.setattr(
        "peaky_finders.serve.viewshed_jobs.site_viewshed_overlay_if_ready",
        lambda *_args, **_kwargs: None,
    )
    bumped: list[list[str]] = []

    def _bump(_slug, _dir, slugs, **kwargs):
        bumped.append(list(slugs))
        return {"status": "ok", "bumped": 0, "submitted": len(slugs)}

    monkeypatch.setattr(
        "peaky_finders.serve.viewshed_jobs.bump_project_priorities",
        _bump,
    )

    result = warm_site_viewshed(
        "demo",
        project_dir,
        "hub",
        sites["hub"],
        sim_overrides=ViewshedSimOverrides(radius_km=60.0, raster_dimension=500),
        verbose=False,
    )
    assert result["status"] == "queued"
    assert bumped == [["hub"]]
