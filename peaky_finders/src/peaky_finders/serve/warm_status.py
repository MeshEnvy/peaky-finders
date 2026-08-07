"""Aggregate background warm / coverage status for ``peaky serve`` UI."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from peaky_finders.core.preset import SiteEntry
from peaky_finders.serve.coverage_queue import get_coverage_queue
from peaky_finders.serve.links import get_cached_project_site_links
from peaky_finders.serve.preset_cache import load_serve_project_context
from peaky_finders.serve.project_warm_scheduler import get_links_warm_progress, is_project_warm_active


def project_warm_status_payload(
    project_slug: str,
    project_dir: Path,
    sites: Mapping[str, SiteEntry] | None = None,
) -> dict[str, object]:
    """Snapshot of viewshed coverage queue, link warm, and cached link mesh."""
    if sites is None:
        sites = load_serve_project_context(project_dir).sites

    site_map = dict(sites)
    queue = get_coverage_queue().snapshot()

    cached_links = get_cached_project_site_links(project_dir, site_map)
    if cached_links is not None:
        links_status = str(cached_links.get("status") or "ready")
        features = cached_links.get("geojson")
        link_features = (
            len(features.get("features", []))  # type: ignore[union-attr]
            if isinstance(features, dict)
            else 0
        )
        links_source = "cache"
    else:
        links_status = "pending"
        link_features = 0
        links_source = "none"

    links_progress = get_links_warm_progress(project_slug)
    if links_progress is not None:
        links_source = "warming"
    warm_active = is_project_warm_active(project_slug)

    busy = (
        warm_active
        or queue["inflight"] > 0
        or queue["queued"] > 0
        or links_progress is not None
        or links_status != "ready"
    )

    return {
        "project": project_slug,
        "busy": busy,
        "sites_total": len(site_map),
        "project_warm": {"active": warm_active},
        "coverage_queue": queue,
        "links": {
            "status": links_status,
            "features": link_features,
            "source": links_source,
            "progress": links_progress,
        },
    }
