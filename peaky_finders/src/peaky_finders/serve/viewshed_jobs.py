"""Background viewshed warm jobs with SSE notifications."""

from __future__ import annotations

import threading
from pathlib import Path

from peaky_finders.core.preset import SiteEntry
from peaky_finders.serve.coverage_queue import get_coverage_queue
from peaky_finders.serve.events import ServeEvent, get_serve_event_hub
from peaky_finders.serve.project_warm_scheduler import (
    PRIORITY_INTERACTIVE,
    bump_project_priorities,
)
from peaky_finders.serve.viewshed import (
    DRAFT_VIEWSHED_SLUG,
    ServeViewshedError,
    _preview_site_at,
    ensure_coords_viewshed_overlay,
    site_viewshed_overlay_if_ready,
)
from peaky_finders.serve.viewshed_sim import ViewshedSimOverrides, viewshed_sim_query_string

_active_warm: set[str] = set()
_active_guard = threading.Lock()


def _warm_key(project_slug: str, site_key: str, sim: ViewshedSimOverrides) -> str:
    qs = viewshed_sim_query_string(sim)
    return f"{project_slug}:{site_key}:{qs}"


def _publish_viewshed(project_slug: str, payload: dict[str, object]) -> None:
    get_serve_event_hub().publish(project_slug, ServeEvent("viewshed", payload))


def _overlay_ready_payload(
    project_slug: str,
    overlay: dict[str, object],
) -> dict[str, object]:
    return {"project": project_slug, "status": "ready", **overlay}


def warm_site_viewshed(
    project_slug: str,
    project_dir: Path,
    site_slug: str,
    site: SiteEntry,
    *,
    sim_overrides: ViewshedSimOverrides | None = None,
    verbose: bool = False,
    priority: int = PRIORITY_INTERACTIVE,
) -> dict[str, object]:
    """Bump or queue viewshed generation; publish immediately when cached."""
    del verbose
    sim = sim_overrides or ViewshedSimOverrides()
    overlay = site_viewshed_overlay_if_ready(
        project_slug,
        project_dir,
        site_slug,
        site,
        sim_overrides=sim,
    )
    if overlay is not None:
        payload = _overlay_ready_payload(project_slug, overlay)
        _publish_viewshed(project_slug, payload)
        return payload

    bump_project_priorities(
        project_slug,
        project_dir,
        [site_slug],
        priority=priority,
        sites={site_slug: site},
    )
    return {"project": project_slug, "slug": site_slug, "status": "queued"}


def warm_coords_viewshed(
    project_slug: str,
    project_dir: Path,
    lat: float,
    lon: float,
    *,
    sim_overrides: ViewshedSimOverrides | None = None,
    verbose: bool = False,
) -> dict[str, object]:
    """Queue draft-site viewshed warm at coordinates (interactive priority)."""
    sim = sim_overrides or ViewshedSimOverrides()
    site = _preview_site_at(lat, lon)
    overlay = site_viewshed_overlay_if_ready(
        project_slug,
        project_dir,
        DRAFT_VIEWSHED_SLUG,
        site,
        sim_overrides=sim,
    )
    if overlay is not None:
        payload = _overlay_ready_payload(
            project_slug,
            {**overlay, "lat": lat, "lon": lon},
        )
        _publish_viewshed(project_slug, payload)
        return payload

    site_key = f"{DRAFT_VIEWSHED_SLUG}:{lat:.6f}:{lon:.6f}"
    warm_key = _warm_key(project_slug, site_key, sim)

    def _runner() -> dict[str, object]:
        return ensure_coords_viewshed_overlay(
            project_slug,
            project_dir,
            lat,
            lon,
            sim_overrides=sim,
            verbose=verbose,
        )

    def _on_done(fut) -> None:
        try:
            overlay_done = fut.result()
        except ServeViewshedError as exc:
            _publish_viewshed(
                project_slug,
                {
                    "project": project_slug,
                    "slug": DRAFT_VIEWSHED_SLUG,
                    "status": "error",
                    "error": str(exc),
                },
            )
            return
        except BaseException as exc:
            _publish_viewshed(
                project_slug,
                {
                    "project": project_slug,
                    "slug": DRAFT_VIEWSHED_SLUG,
                    "status": "error",
                    "error": str(exc),
                },
            )
            return
        _publish_viewshed(
            project_slug,
            _overlay_ready_payload(
                project_slug,
                {**overlay_done, "lat": lat, "lon": lon},
            ),
        )

    _publish_viewshed(
        project_slug,
        {"project": project_slug, "slug": DRAFT_VIEWSHED_SLUG, "status": "queued"},
    )
    future = get_coverage_queue().ensure_submitted(
        warm_key, _runner, priority=PRIORITY_INTERACTIVE
    )
    future.add_done_callback(_on_done)
    return {"project": project_slug, "slug": DRAFT_VIEWSHED_SLUG, "status": "queued"}


def reset_viewshed_warm_jobs_for_tests() -> None:
    """Clear in-flight warm state (tests only)."""
    from peaky_finders.serve.coverage_queue import reset_coverage_queue_for_tests
    from peaky_finders.serve.project_warm_scheduler import reset_project_warm_scheduler_for_tests

    with _active_guard:
        _active_warm.clear()
    reset_project_warm_scheduler_for_tests()
    reset_coverage_queue_for_tests()
