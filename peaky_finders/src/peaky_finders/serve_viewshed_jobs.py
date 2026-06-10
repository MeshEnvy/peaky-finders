"""Background viewshed warm jobs with SSE notifications."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from peaky_finders.serve_events import ServeEvent, get_serve_event_hub
from peaky_finders.serve_viewshed import (
    DRAFT_VIEWSHED_SLUG,
    ServeViewshedError,
    _preview_site_at,
    ensure_coords_viewshed_overlay,
    ensure_site_viewshed_overlay,
    site_viewshed_overlay_if_ready,
)
from peaky_finders.serve_viewshed_sim import ViewshedSimOverrides, viewshed_sim_query_string
from peaky_finders.sites_job import SiteEntry

_active_warm: set[str] = set()
_active_guard = threading.Lock()


@dataclass
class _SiteWarmState:
    generation: int
    warm_key: str


_site_warm: dict[str, _SiteWarmState] = {}


def _warm_key(project_slug: str, site_key: str, sim: ViewshedSimOverrides) -> str:
    qs = viewshed_sim_query_string(sim)
    return f"{project_slug}:{site_key}:{qs}"


def _site_slot(project_slug: str, site_key: str) -> str:
    return f"{project_slug}:{site_key}"


def _bump_site_generation(project_slug: str, site_key: str, warm_key: str) -> int:
    slot = _site_slot(project_slug, site_key)
    prev = _site_warm.get(slot)
    generation = (prev.generation + 1) if prev is not None else 1
    _site_warm[slot] = _SiteWarmState(generation=generation, warm_key=warm_key)
    return generation


def _is_current_generation(project_slug: str, site_key: str, generation: int) -> bool:
    state = _site_warm.get(_site_slot(project_slug, site_key))
    return state is not None and state.generation == generation


def _publish_viewshed(project_slug: str, payload: dict[str, object]) -> None:
    get_serve_event_hub().publish(project_slug, ServeEvent("viewshed", payload))


def _overlay_ready_payload(
    project_slug: str,
    overlay: dict[str, object],
) -> dict[str, object]:
    return {"project": project_slug, "status": "ready", **overlay}


def _schedule_warm(
    project_slug: str,
    site_key: str,
    site_slug: str,
    warm_key: str,
    runner: Callable[[], dict[str, object]],
) -> dict[str, object]:
    with _active_guard:
        if warm_key in _active_warm:
            return {"project": project_slug, "slug": site_slug, "status": "queued"}
        generation = _bump_site_generation(project_slug, site_key, warm_key)
        _active_warm.add(warm_key)

    _publish_viewshed(
        project_slug,
        {"project": project_slug, "slug": site_slug, "status": "queued"},
    )

    def _run() -> None:
        try:
            if not _is_current_generation(project_slug, site_key, generation):
                return
            _publish_viewshed(
                project_slug,
                {"project": project_slug, "slug": site_slug, "status": "running"},
            )
            overlay = runner()
            if not _is_current_generation(project_slug, site_key, generation):
                return
            _publish_viewshed(project_slug, _overlay_ready_payload(project_slug, overlay))
        except ServeViewshedError as exc:
            if not _is_current_generation(project_slug, site_key, generation):
                return
            _publish_viewshed(
                project_slug,
                {
                    "project": project_slug,
                    "slug": site_slug,
                    "status": "error",
                    "error": str(exc),
                },
            )
        finally:
            with _active_guard:
                _active_warm.discard(warm_key)

    threading.Thread(
        target=_run,
        name=f"viewshed-warm-{site_slug}",
        daemon=True,
    ).start()
    return {"project": project_slug, "slug": site_slug, "status": "queued"}


def warm_site_viewshed(
    project_slug: str,
    project_dir: Path,
    site_slug: str,
    site: SiteEntry,
    *,
    sim_overrides: ViewshedSimOverrides | None = None,
    verbose: bool = False,
) -> dict[str, object]:
    """Queue viewshed generation when needed; publish lifecycle on SSE."""
    sim = sim_overrides or ViewshedSimOverrides()
    overlay = site_viewshed_overlay_if_ready(
        project_slug,
        project_dir,
        site_slug,
        site,
        sim_overrides=sim,
    )
    if overlay is not None:
        with _active_guard:
            warm_key = _warm_key(project_slug, site_slug, sim)
            _bump_site_generation(project_slug, site_slug, warm_key)
        payload = _overlay_ready_payload(project_slug, overlay)
        _publish_viewshed(project_slug, payload)
        return payload

    warm_key = _warm_key(project_slug, site_slug, sim)

    def _runner() -> dict[str, object]:
        return ensure_site_viewshed_overlay(
            project_slug,
            project_dir,
            site_slug,
            site,
            sim_overrides=sim,
            verbose=verbose,
        )

    return _schedule_warm(project_slug, site_slug, site_slug, warm_key, _runner)


def warm_coords_viewshed(
    project_slug: str,
    project_dir: Path,
    lat: float,
    lon: float,
    *,
    sim_overrides: ViewshedSimOverrides | None = None,
    verbose: bool = False,
) -> dict[str, object]:
    """Queue draft-site viewshed warm at coordinates."""
    sim = sim_overrides or ViewshedSimOverrides()
    site = _preview_site_at(lat, lon)
    site_key = f"{DRAFT_VIEWSHED_SLUG}:{lat:.6f}:{lon:.6f}"
    overlay = site_viewshed_overlay_if_ready(
        project_slug,
        project_dir,
        DRAFT_VIEWSHED_SLUG,
        site,
        sim_overrides=sim,
    )
    if overlay is not None:
        with _active_guard:
            warm_key = _warm_key(project_slug, site_key, sim)
            _bump_site_generation(project_slug, site_key, warm_key)
        payload = _overlay_ready_payload(project_slug, {**overlay, "lat": lat, "lon": lon})
        _publish_viewshed(project_slug, payload)
        return payload

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

    return _schedule_warm(project_slug, site_key, DRAFT_VIEWSHED_SLUG, warm_key, _runner)


def reset_viewshed_warm_jobs_for_tests() -> None:
    """Clear in-flight warm state (tests only)."""
    with _active_guard:
        _active_warm.clear()
        _site_warm.clear()
