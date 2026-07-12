"""Background site link analysis with viewshed warm-up."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Mapping

from shapely.geometry.base import BaseGeometry

from peaky_finders.core.preset import Preset, SiteEntry, load_preset_for_coverage
from peaky_finders.serve.events import ServeEvent, get_serve_event_hub
from peaky_finders.serve.links import (
    ServeLinksError,
    compute_project_site_links,
    get_cached_project_site_links,
    store_project_site_links_cache,
)
from peaky_finders.serve.viewshed import resolve_serve_coverage_max_concurrent
from peaky_finders.serve.viewshed_engine import get_viewshed_engine
from peaky_finders.serve.viewshed_sim import ViewshedSimOverrides

_active_site_links: set[str] = set()
_active_guard = threading.Lock()


def _publish_links(project_slug: str, payload: dict[str, object]) -> None:
    get_serve_event_hub().publish(project_slug, ServeEvent("links", payload))


def _load_preset(project_dir: Path) -> Preset:
    return load_preset_for_coverage(project_dir / "config.yaml")


def _read_existing_footprints(
    project_dir: Path,
    preset: Preset,
    sites: Mapping[str, SiteEntry],
    *,
    verbose: bool = False,
) -> dict[str, BaseGeometry | None]:
    """Parallel disk/memory footprint read — never generate coverage."""
    engine = get_viewshed_engine()
    sim = ViewshedSimOverrides()
    footprints: dict[str, BaseGeometry | None] = {}
    slugs = sorted(sites.keys())
    if not slugs:
        return footprints

    workers = min(len(slugs), resolve_serve_coverage_max_concurrent())
    if verbose:
        print(
            f"link jobs: read {len(slugs)} existing footprint(s), workers={workers}",
            flush=True,
        )

    def _one(slug: str) -> tuple[str, BaseGeometry | None]:
        return slug, engine.read_site_footprint(
            project_dir,
            preset,
            sites[slug],
            sim=sim,
            verbose=verbose,
        )

    if workers <= 1:
        for slug in slugs:
            s, fp = _one(slug)
            footprints[s] = fp
        return footprints

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(_one, slug): slug for slug in slugs}
        done = 0
        for fut in as_completed(futs):
            slug, fp = fut.result()
            footprints[slug] = fp
            done += 1
            if verbose and (done == len(slugs) or done % 20 == 0):
                have = sum(1 for v in footprints.values() if v is not None)
                print(
                    f"link jobs: footprints [{done}/{len(slugs)}] have={have}",
                    flush=True,
                )
    return footprints


def _ensure_missing_footprints(
    project_dir: Path,
    preset: Preset,
    sites: Mapping[str, SiteEntry],
    footprints: dict[str, BaseGeometry | None],
    *,
    verbose: bool = False,
) -> dict[str, BaseGeometry | None]:
    """Generate coverage only for sites still missing a footprint."""
    engine = get_viewshed_engine()
    sim = ViewshedSimOverrides()
    missing = sorted(slug for slug, fp in footprints.items() if fp is None and slug in sites)
    if not missing:
        return footprints

    workers = min(len(missing), resolve_serve_coverage_max_concurrent())
    if verbose:
        print(
            f"link jobs: ensure {len(missing)} missing footprint(s), workers={workers}",
            flush=True,
        )

    if workers <= 1:
        for slug in missing:
            footprints[slug] = engine.ensure_site_footprint(
                project_dir,
                preset,
                slug,
                sites[slug],
                sim=sim,
                verbose=verbose,
            )
        return footprints

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {
            pool.submit(
                engine.ensure_site_footprint,
                project_dir,
                preset,
                slug,
                sites[slug],
                sim=sim,
                verbose=verbose,
            ): slug
            for slug in missing
        }
        for fut in as_completed(futs):
            slug = futs[fut]
            footprints[slug] = fut.result()
    return footprints


def warm_project_site_links(
    project_slug: str,
    project_dir: Path,
    sites: Mapping[str, SiteEntry],
    *,
    verbose: bool = False,
) -> dict[str, object]:
    """Queue link analysis; publish ``links`` SSE when done.

    Fast path: return cached ready payload when link inputs are unchanged.
    Otherwise: read existing footprints in parallel, publish ready mesh, then
    optionally generate only missing viewsheds and republish.
    """
    try:
        preset = _load_preset(project_dir)
    except (OSError, ValueError) as exc:
        return {"project": project_slug, "status": "error", "error": str(exc)}

    cached = get_cached_project_site_links(project_dir, sites, preset=preset)
    if cached is not None:
        return {"project": project_slug, "status": "ready", **cached}

    with _active_guard:
        if project_slug in _active_site_links:
            return {"project": project_slug, "status": "queued"}
        _active_site_links.add(project_slug)

    _publish_links(project_slug, {"project": project_slug, "status": "running"})

    def _run() -> None:
        try:
            footprints = _read_existing_footprints(
                project_dir,
                preset,
                sites,
                verbose=verbose,
            )
            payload = compute_project_site_links(
                project_dir,
                sites,
                preset=preset,
                footprints=footprints,
                analysis_complete=True,
            )
            store_project_site_links_cache(
                project_dir, sites, payload, preset=preset
            )
            _publish_links(
                project_slug,
                {"project": project_slug, "status": "ready", **payload},
            )

            missing = sum(1 for fp in footprints.values() if fp is None)
            if missing:
                if verbose:
                    print(
                        f"link jobs: {missing} site(s) lack footprints; ensuring",
                        flush=True,
                    )
                footprints = _ensure_missing_footprints(
                    project_dir,
                    preset,
                    sites,
                    footprints,
                    verbose=verbose,
                )
                payload = compute_project_site_links(
                    project_dir,
                    sites,
                    preset=preset,
                    footprints=footprints,
                    analysis_complete=True,
                )
                store_project_site_links_cache(
                    project_dir, sites, payload, preset=preset
                )
                _publish_links(
                    project_slug,
                    {"project": project_slug, "status": "ready", **payload},
                )
        except (ServeLinksError, OSError, ValueError) as exc:
            _publish_links(
                project_slug,
                {"project": project_slug, "status": "error", "error": str(exc)},
            )
        finally:
            with _active_guard:
                _active_site_links.discard(project_slug)

    threading.Thread(
        target=_run,
        name=f"site-links-{project_slug}",
        daemon=True,
    ).start()
    return {"project": project_slug, "status": "queued"}


def reset_link_jobs_for_tests() -> None:
    from peaky_finders.serve.links import reset_project_site_links_cache_for_tests

    with _active_guard:
        _active_site_links.clear()
    reset_project_site_links_cache_for_tests()
