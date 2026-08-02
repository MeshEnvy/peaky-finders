"""Project warm scheduler background enqueue and priority bumps."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

from peaky_finders.core.project.scaffold import scaffold_project
from peaky_finders.core.preset import load_preset_sites
from peaky_finders.serve.link_jobs import reset_link_jobs_for_tests
from peaky_finders.serve.project_warm_scheduler import (
    PRIORITY_BACKGROUND,
    PRIORITY_INTERACTIVE,
    PRIORITY_VIEWPORT,
    bump_project_priorities,
    ensure_project_warm,
)


def _setup(tmp_path: Path) -> tuple[Path, str, dict]:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    scaffold_project("demo", parent=projects_dir)
    project_dir = projects_dir / "demo"
    sites = load_preset_sites(project_dir / "config.yaml")
    return project_dir, "demo", sites


def test_ensure_project_warm_enqueues_missing_sites(tmp_path: Path) -> None:
    reset_link_jobs_for_tests()
    project_dir, slug, sites = _setup(tmp_path)
    enqueued: list[str] = []

    with patch(
        "peaky_finders.serve.project_warm_scheduler._enqueue_site_if_missing",
        side_effect=lambda _ps, _pd, _pr, site_slug, _site, **kw: (
            enqueued.append(site_slug) or True
            if kw.get("priority") == PRIORITY_BACKGROUND
            else False
        ),
    ):
        result = ensure_project_warm(slug, project_dir, sites=sites)
        assert result["status"] == "running"
        deadline = time.time() + 2.0
        while time.time() < deadline and len(enqueued) < len(sites):
            time.sleep(0.02)

    assert sorted(enqueued) == sorted(sites.keys())


def test_bump_project_priorities_submits_missing_slug(tmp_path: Path) -> None:
    reset_link_jobs_for_tests()
    project_dir, slug, sites = _setup(tmp_path)
    site_slug = next(iter(sites))

    with patch(
        "peaky_finders.serve.project_warm_scheduler.get_coverage_queue",
    ) as mock_queue:
        inst = mock_queue.return_value
        inst.bump.return_value = False
        inst.is_active.return_value = False
        with patch(
            "peaky_finders.serve.project_warm_scheduler._submit_site_warm",
            return_value=True,
        ):
            result = bump_project_priorities(
                slug,
                project_dir,
                [site_slug],
                priority=PRIORITY_INTERACTIVE,
                sites=sites,
            )

    assert result["status"] == "ok"
    assert result["submitted"] >= 1


def test_bump_viewport_skips_hop_neighbors(tmp_path: Path) -> None:
    reset_link_jobs_for_tests()
    project_dir, slug, sites = _setup(tmp_path)
    site_slug = next(iter(sites))

    with patch(
        "peaky_finders.serve.project_warm_scheduler._hop_neighbors",
        return_value={"neighbor-a"},
    ) as hop:
        with patch(
            "peaky_finders.serve.project_warm_scheduler.get_coverage_queue",
        ) as mock_queue:
            inst = mock_queue.return_value
            inst.bump.return_value = False
            inst.is_active.return_value = True
            bump_project_priorities(
                slug,
                project_dir,
                [site_slug],
                priority=PRIORITY_VIEWPORT,
                sites=sites,
            )

    hop.assert_not_called()


def test_schedule_bump_returns_immediately(tmp_path: Path) -> None:
    reset_link_jobs_for_tests()
    project_dir, slug, sites = _setup(tmp_path)
    site_slug = next(iter(sites))

    with patch(
        "peaky_finders.serve.project_warm_scheduler.bump_project_priorities",
    ) as bump:
        from peaky_finders.serve.project_warm_scheduler import schedule_bump_project_priorities

        result = schedule_bump_project_priorities(
            slug,
            project_dir,
            [site_slug],
            priority=PRIORITY_VIEWPORT,
        )
        assert result["status"] == "accepted"
        bump.assert_not_called()
        time.sleep(0.08)
        bump.assert_called_once()


def test_links_refresh_publishes_incremental(tmp_path: Path) -> None:
    reset_link_jobs_for_tests()
    project_dir, slug, sites = _setup(tmp_path)
    published: list[dict[str, object]] = []
    ready = {
        "status": "pending",
        "links": [],
        "geojson": {"type": "FeatureCollection", "features": []},
    }

    with patch(
        "peaky_finders.serve.project_warm_scheduler._publish_links",
        side_effect=lambda _s, payload: published.append(dict(payload)),
    ):
        with patch(
            "peaky_finders.serve.project_warm_scheduler.read_existing_footprints",
            return_value={s: None for s in sites},
        ):
            with patch(
                "peaky_finders.serve.project_warm_scheduler.vectorize_missing_footprints",
                side_effect=lambda _pd, _pr, _sites, footprints, **_k: footprints,
            ):
                with patch(
                    "peaky_finders.serve.project_warm_scheduler.compute_project_site_links",
                    return_value=ready,
                ):
                    from peaky_finders.serve.project_warm_scheduler import (
                        _refresh_project_links,
                    )

                    _refresh_project_links(slug, project_dir)
                    time.sleep(0.05)

    assert published
    assert published[-1]["status"] == "pending"
