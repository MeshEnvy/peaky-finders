"""Background site link analysis driven by project warm scheduler."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Mapping

from peaky_finders.core.preset import SiteEntry
from peaky_finders.serve.links import get_cached_project_site_links
from peaky_finders.serve.project_warm_scheduler import (
    PRIORITY_VIEWPORT,
    bump_project_priorities,
    ensure_project_warm,
)

_active_site_links: set[str] = set()
_active_guard = threading.Lock()


def warm_project_site_links(
    project_slug: str,
    project_dir: Path,
    sites: Mapping[str, SiteEntry],
    *,
    verbose: bool = False,
    priority_slugs: list[str] | None = None,
) -> dict[str, object]:
    """Ensure background warm is running; optionally bump priority slugs."""
    del verbose
    try:
        cached = get_cached_project_site_links(project_dir, sites)
        if cached is not None and not priority_slugs:
            return {"project": project_slug, "status": "ready", **cached}
    except (OSError, ValueError) as exc:
        return {"project": project_slug, "status": "error", "error": str(exc)}

    result = ensure_project_warm(project_slug, project_dir, sites=sites)
    if priority_slugs:
        bump = bump_project_priorities(
            project_slug,
            project_dir,
            priority_slugs,
            priority=PRIORITY_VIEWPORT,
            sites=sites,
        )
        result = {**result, **bump}
    return result


def reset_link_jobs_for_tests() -> None:
    from peaky_finders.serve.coverage_queue import reset_coverage_queue_for_tests
    from peaky_finders.serve.links import reset_project_site_links_cache_for_tests
    from peaky_finders.serve.project_warm_scheduler import reset_project_warm_scheduler_for_tests

    with _active_guard:
        _active_site_links.clear()
    reset_project_site_links_cache_for_tests()
    reset_project_warm_scheduler_for_tests()
    reset_coverage_queue_for_tests()
