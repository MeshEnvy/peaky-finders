"""Server-owned background viewshed + link warm with priority bumps."""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from pathlib import Path

from peaky_finders.core.preset import Preset, SiteEntry
from peaky_finders.core.links.viewshed import site_viewshed_workdir
from peaky_finders.serve.preset_cache import ServeProjectContext, load_serve_project_context
from peaky_finders.core.viewshed.polygonize import SPLAT_GPKG_NAME, SPLAT_OUTPUT_PPM_BASENAME
from peaky_finders.serve.coverage_queue import get_coverage_queue
from peaky_finders.serve.events import ServeEvent, get_serve_event_hub
from peaky_finders.serve.link_footprints import (
    read_existing_footprints,
    vectorize_missing_footprints,
)
from peaky_finders.serve.links import (
    ServeLinksError,
    _haversine_m,
    compute_project_site_links,
    invalidate_project_site_links_cache,
    store_project_site_links_cache,
)
from peaky_finders.serve.viewshed import (
    ServeViewshedError,
    site_viewshed_overlay_if_ready,
)
from peaky_finders.serve.viewshed_engine import (
    footprint_cache_key,
    get_viewshed_engine,
    site_footprint_digest,
)
from peaky_finders.serve.viewshed_sim import ViewshedSimOverrides

PRIORITY_INTERACTIVE = 0
PRIORITY_VIEWPORT = 10
PRIORITY_LINK_ADJACENT = 20
PRIORITY_BACKGROUND = 100

_running: set[str] = set()
_running_guard = threading.Lock()
_links_refresh_timers: dict[str, threading.Timer] = {}
_links_refresh_guard = threading.Lock()
_last_links_payload: dict[str, str] = {}
_last_links_guard = threading.Lock()
_bump_timers: dict[str, threading.Timer] = {}
_bump_pending: dict[str, tuple[set[str], int]] = {}
_bump_guard = threading.Lock()

LINKS_REFRESH_DEBOUNCE_S = 0.4
BUMP_DEBOUNCE_S = 0.05


def _project_context(project_dir: Path) -> ServeProjectContext:
    return load_serve_project_context(project_dir)


def _site_coverage_key(project_dir: Path, preset: Preset, site: SiteEntry) -> str:
    sim = ViewshedSimOverrides()
    digest = site_footprint_digest(preset, site, sim=sim)
    return footprint_cache_key(project_dir, digest)


def _site_coverage_artifacts_exist(
    project_dir: Path,
    preset: Preset,
    site: SiteEntry,
) -> bool:
    """Fast existence check — no GPKG open."""
    wd = site_viewshed_workdir(project_dir / "config.yaml", preset, site)
    if (wd / SPLAT_GPKG_NAME).is_file():
        return True
    if (wd / "splat.png").is_file():
        return True
    return (wd / SPLAT_OUTPUT_PPM_BASENAME).is_file()


def _publish_viewshed(project_slug: str, payload: dict[str, object]) -> None:
    get_serve_event_hub().publish(project_slug, ServeEvent("viewshed", payload))


def _publish_links(project_slug: str, payload: dict[str, object]) -> None:
    get_serve_event_hub().publish(project_slug, ServeEvent("links", payload))


def _hop_neighbors(
    preset: Preset,
    sites: Mapping[str, SiteEntry],
    center_slugs: set[str],
) -> set[str]:
    out: set[str] = set()
    max_m = float(preset.simulation.radius_km) * 1000.0
    for center in center_slugs:
        if center not in sites:
            continue
        site_c = sites[center]
        lat_c, lon_c = float(site_c.lat), float(site_c.lon)
        for slug, site in sites.items():
            if slug == center:
                continue
            lat, lon = float(site.lat), float(site.lon)
            if _haversine_m(lat_c, lon_c, lat, lon) <= max_m:
                out.add(slug)
    return out


def _refresh_project_links(
    project_slug: str,
    project_dir: Path,
    *,
    verbose: bool = False,
) -> None:
    try:
        ctx = _project_context(project_dir)
        preset = ctx.preset
        sites = dict(ctx.sites)
        if not sites:
            return

        footprints = read_existing_footprints(
            project_dir,
            preset,
            sites,
            verbose=verbose,
        )
        footprints = vectorize_missing_footprints(
            project_dir,
            preset,
            sites,
            footprints,
            verbose=verbose,
        )
        complete = all(footprints.get(slug) is not None for slug in sites)
        payload = compute_project_site_links(
            project_dir,
            sites,
            preset=preset,
            footprints=footprints,
            analysis_complete=complete,
        )
        if complete:
            store_project_site_links_cache(project_dir, sites, payload, preset=preset)

        wire = {"project": project_slug, **payload}
        if complete:
            wire["status"] = "ready"
        digest = json.dumps(wire, sort_keys=True, separators=(",", ":"))
        with _last_links_guard:
            if _last_links_payload.get(project_slug) == digest:
                return
            _last_links_payload[project_slug] = digest
        _publish_links(project_slug, wire)
    except (ServeLinksError, OSError, ValueError) as exc:
        _publish_links(
            project_slug,
            {"project": project_slug, "status": "error", "error": str(exc)},
        )


def _schedule_links_refresh(
    project_slug: str,
    project_dir: Path,
    *,
    verbose: bool = False,
) -> None:
    def _run() -> None:
        _refresh_project_links(project_slug, project_dir, verbose=verbose)

    with _links_refresh_guard:
        existing = _links_refresh_timers.pop(project_slug, None)
        if existing is not None:
            existing.cancel()
        timer = threading.Timer(LINKS_REFRESH_DEBOUNCE_S, _run)
        timer.daemon = True
        _links_refresh_timers[project_slug] = timer
        timer.start()


def _warm_site_job(
    project_slug: str,
    project_dir: Path,
    site_slug: str,
    site: SiteEntry,
    *,
    verbose: bool = False,
) -> None:
    engine = get_viewshed_engine()
    preset = _project_context(project_dir).preset
    sim = ViewshedSimOverrides()
    engine.ensure_site_footprint(
        project_dir,
        preset,
        site_slug,
        site,
        sim=sim,
        verbose=verbose,
    )
    overlay = site_viewshed_overlay_if_ready(
        project_slug,
        project_dir,
        site_slug,
        site,
        sim_overrides=sim,
    )
    if overlay is not None:
        _publish_viewshed(
            project_slug,
            {"project": project_slug, "status": "ready", **overlay},
        )
    _schedule_links_refresh(project_slug, project_dir, verbose=verbose)


def _submit_site_warm(
    project_slug: str,
    project_dir: Path,
    preset: Preset,
    site_slug: str,
    site: SiteEntry,
    *,
    priority: int,
    verbose: bool = False,
) -> bool:
    key = _site_coverage_key(project_dir, preset, site)

    def _runner() -> None:
        _warm_site_job(project_slug, project_dir, site_slug, site, verbose=verbose)

    get_coverage_queue().ensure_submitted(key, _runner, priority=priority)
    return True


def _enqueue_site_if_missing(
    project_slug: str,
    project_dir: Path,
    preset: Preset,
    site_slug: str,
    site: SiteEntry,
    *,
    priority: int,
    verbose: bool = False,
    deep_check: bool = False,
) -> bool:
    """Queue splatter when coverage is absent. *deep_check* reads GPKG (background only)."""
    queue = get_coverage_queue()
    key = _site_coverage_key(project_dir, preset, site)
    if queue.is_active(key):
        return False

    if deep_check:
        engine = get_viewshed_engine()
        sim = ViewshedSimOverrides()
        if engine.read_site_footprint(project_dir, preset, site, sim=sim, verbose=verbose) is not None:
            overlay = site_viewshed_overlay_if_ready(
                project_slug,
                project_dir,
                site_slug,
                site,
                sim_overrides=sim,
            )
            if overlay is not None:
                _publish_viewshed(
                    project_slug,
                    {"project": project_slug, "status": "ready", **overlay},
                )
            return False
    elif _site_coverage_artifacts_exist(project_dir, preset, site):
        return False

    return _submit_site_warm(
        project_slug,
        project_dir,
        preset,
        site_slug,
        site,
        priority=priority,
        verbose=verbose,
    )


def _run_project_warm(
    project_slug: str,
    project_dir: Path,
    sites: Mapping[str, SiteEntry],
    *,
    verbose: bool = False,
) -> None:
    try:
        ctx = _project_context(project_dir)
        site_map = dict(sites)
        for site_slug, site in sorted(site_map.items()):
            _enqueue_site_if_missing(
                project_slug,
                project_dir,
                ctx.preset,
                site_slug,
                site,
                priority=PRIORITY_BACKGROUND,
                verbose=verbose,
                deep_check=True,
            )
        _schedule_links_refresh(project_slug, project_dir, verbose=verbose)
    except (ServeViewshedError, OSError, ValueError):
        with _running_guard:
            _running.discard(project_slug)


def ensure_project_warm(
    project_slug: str,
    project_dir: Path,
    sites: Mapping[str, SiteEntry] | None = None,
    *,
    verbose: bool = False,
) -> dict[str, object]:
    """Start background drain of all missing site footprints (idempotent, non-blocking)."""
    with _running_guard:
        if project_slug in _running:
            return {"project": project_slug, "status": "running"}
        _running.add(project_slug)

    if sites is not None:
        site_map = dict(sites)
    else:
        site_map = dict(_project_context(project_dir).sites)

    thread = threading.Thread(
        target=_run_project_warm,
        args=(project_slug, project_dir, site_map),
        kwargs={"verbose": verbose},
        name=f"project-warm-{project_slug}",
        daemon=True,
    )
    thread.start()
    return {"project": project_slug, "status": "running", "sites": len(site_map)}


def bump_project_priorities(
    project_slug: str,
    project_dir: Path,
    slugs: list[str],
    *,
    priority: int = PRIORITY_VIEWPORT,
    sites: Mapping[str, SiteEntry] | None = None,
    preset: Preset | None = None,
    verbose: bool = False,
) -> dict[str, object]:
    """Bump queued footprint jobs; enqueue missing *target* slugs (no pre-warm stat I/O)."""
    ensure_project_warm(project_slug, project_dir, sites=sites, verbose=verbose)

    if preset is None or sites is None:
        ctx = _project_context(project_dir)
        preset = preset or ctx.preset
        site_map = dict(sites) if sites is not None else dict(ctx.sites)
    else:
        site_map = dict(sites)

    target = {str(s).strip() for s in slugs if str(s).strip()}
    # Hop-neighbor expansion only for explicit selection — viewport pans can touch 200+ sites.
    adjacent = (
        _hop_neighbors(preset, site_map, target)
        if priority == PRIORITY_INTERACTIVE
        else set()
    )

    queue = get_coverage_queue()
    bumped = 0
    submitted = 0

    for slug in sorted(target):
        site = site_map.get(slug)
        if site is None:
            continue
        key = _site_coverage_key(project_dir, preset, site)
        if queue.bump(key, priority):
            bumped += 1
        elif not queue.is_active(key):
            if _submit_site_warm(
                project_slug,
                project_dir,
                preset,
                slug,
                site,
                priority=priority,
                verbose=verbose,
            ):
                submitted += 1

    for slug in sorted(adjacent):
        if slug in target:
            continue
        site = site_map.get(slug)
        if site is None:
            continue
        key = _site_coverage_key(project_dir, preset, site)
        if queue.bump(key, PRIORITY_LINK_ADJACENT):
            bumped += 1

    return {
        "project": project_slug,
        "status": "ok",
        "bumped": bumped,
        "submitted": submitted,
        "priority": priority,
    }


def _flush_scheduled_bump(
    project_slug: str,
    project_dir: Path,
    *,
    verbose: bool = False,
) -> None:
    with _bump_guard:
        pending = _bump_pending.pop(project_slug, None)
        _bump_timers.pop(project_slug, None)
    if pending is None:
        return
    slugs, priority = pending
    bump_project_priorities(
        project_slug,
        project_dir,
        sorted(slugs),
        priority=priority,
        verbose=verbose,
    )


def schedule_bump_project_priorities(
    project_slug: str,
    project_dir: Path,
    slugs: list[str],
    *,
    priority: int = PRIORITY_VIEWPORT,
    verbose: bool = False,
) -> dict[str, object]:
    """Coalesce priority bumps off the HTTP thread (returns immediately)."""
    ensure_project_warm(project_slug, project_dir, verbose=verbose)
    incoming = {str(s).strip() for s in slugs if str(s).strip()}
    with _bump_guard:
        existing = _bump_pending.get(project_slug)
        merged = set(incoming)
        min_priority = priority
        if existing is not None:
            merged |= existing[0]
            min_priority = min(min_priority, existing[1])
        _bump_pending[project_slug] = (merged, min_priority)
        queued = len(merged)

        timer = _bump_timers.pop(project_slug, None)
        if timer is not None:
            timer.cancel()

        def _run() -> None:
            _flush_scheduled_bump(project_slug, project_dir, verbose=verbose)

        timer = threading.Timer(BUMP_DEBOUNCE_S, _run)
        timer.daemon = True
        _bump_timers[project_slug] = timer
        timer.start()

    return {
        "project": project_slug,
        "status": "accepted",
        "queued": queued,
        "priority": priority,
    }


def invalidate_site_coverage(
    project_slug: str,
    project_dir: Path,
    site_slug: str,
    site: SiteEntry,
    *,
    verbose: bool = False,
) -> None:
    """Re-enqueue one site after coord/height change."""
    invalidate_project_site_links_cache(project_dir)
    with _last_links_guard:
        _last_links_payload.pop(project_slug, None)
    preset = _project_context(project_dir).preset
    key = _site_coverage_key(project_dir, preset, site)
    get_coverage_queue().cancel(key)
    _submit_site_warm(
        project_slug,
        project_dir,
        preset,
        site_slug,
        site,
        priority=PRIORITY_INTERACTIVE,
        verbose=verbose,
    )
    _schedule_links_refresh(project_slug, project_dir, verbose=verbose)


def reset_project_warm_scheduler_for_tests() -> None:
    with _running_guard:
        _running.clear()
    with _links_refresh_guard:
        for timer in _links_refresh_timers.values():
            timer.cancel()
        _links_refresh_timers.clear()
    with _bump_guard:
        for timer in _bump_timers.values():
            timer.cancel()
        _bump_timers.clear()
        _bump_pending.clear()
    with _last_links_guard:
        _last_links_payload.clear()
