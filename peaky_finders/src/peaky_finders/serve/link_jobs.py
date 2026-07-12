"""Background site link analysis with viewshed warm-up."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Mapping

from peaky_finders.serve.events import ServeEvent, get_serve_event_hub
from peaky_finders.serve.links import (
    ServeLinksError,
    compute_project_site_links,
    store_project_site_links_cache,
)
from peaky_finders.serve.viewshed import resolve_serve_coverage_max_concurrent
from peaky_finders.serve.viewshed_engine import get_viewshed_engine
from peaky_finders.serve.viewshed_sim import ViewshedSimOverrides
from peaky_finders.core.preset import Preset, SiteEntry, load_preset_for_coverage

_active_site_links: set[str] = set()
_active_guard = threading.Lock()


def _publish_links(project_slug: str, payload: dict[str, object]) -> None:
    get_serve_event_hub().publish(project_slug, ServeEvent("links", payload))


def _load_preset(project_dir: Path) -> Preset:
    return load_preset_for_coverage(project_dir / "config.yaml")


def _ensure_site_footprints(
    project_dir: Path,
    preset: Preset,
    sites: Mapping[str, SiteEntry],
    *,
    verbose: bool = False,
) -> dict[str, object | None]:
    engine = get_viewshed_engine()
    sim = ViewshedSimOverrides()
    footprints: dict[str, object | None] = {}
    slugs = sorted(sites.keys())
    if not slugs:
        return footprints

    workers = min(len(slugs), resolve_serve_coverage_max_concurrent())
    if verbose:
        print(
            f"link jobs: warm {len(slugs)} viewshed footprint(s), workers={workers}",
            flush=True,
        )

    if workers <= 1:
        for slug in slugs:
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
            for slug in slugs
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
    """Queue viewshed warm + link analysis; publish ``links`` SSE when done."""
    with _active_guard:
        if project_slug in _active_site_links:
            return {"project": project_slug, "status": "queued"}
        _active_site_links.add(project_slug)

    _publish_links(project_slug, {"project": project_slug, "status": "running"})

    def _run() -> None:
        try:
            preset = _load_preset(project_dir)
            footprints = _ensure_site_footprints(
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
            )
            store_project_site_links_cache(project_dir, sites, payload)
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
