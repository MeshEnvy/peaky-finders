"""Footprint read/vectorize helpers for link warm (avoids link_jobs ↔ scheduler cycle)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

from shapely.geometry.base import BaseGeometry

from peaky_finders.core.preset import Preset, SiteEntry
from peaky_finders.serve.viewshed_engine import get_viewshed_engine
from peaky_finders.serve.viewshed_sim import ViewshedSimOverrides

FootprintProgressFn = Callable[[str, int, int, str], None]


def read_existing_footprints(
    project_dir: Path,
    preset: Preset,
    sites: Mapping[str, SiteEntry],
    *,
    verbose: bool = False,
    progress: FootprintProgressFn | None = None,
) -> dict[str, BaseGeometry | None]:
    """Read footprints from memory/disk — sequential GPKG opens (SQLite-safe)."""
    engine = get_viewshed_engine()
    sim = ViewshedSimOverrides()
    footprints: dict[str, BaseGeometry | None] = {}
    slugs = sorted(sites.keys())
    if not slugs:
        return footprints

    total = len(slugs)
    if verbose:
        print(
            f"link footprints: read {total} existing footprint(s)",
            flush=True,
        )

    for idx, slug in enumerate(slugs):
        if progress is not None:
            progress("read_footprints", idx + 1, total, slug)
        try:
            footprints[slug] = engine.read_site_footprint(
                project_dir,
                preset,
                sites[slug],
                sim=sim,
                verbose=verbose,
            )
        except OSError:
            footprints[slug] = None
    return footprints


def vectorize_missing_footprints(
    project_dir: Path,
    preset: Preset,
    sites: Mapping[str, SiteEntry],
    footprints: dict[str, BaseGeometry | None],
    *,
    verbose: bool = False,
    progress: FootprintProgressFn | None = None,
) -> dict[str, BaseGeometry | None]:
    """Build GPKG footprints from cached PPM artifacts — never run splatter."""
    engine = get_viewshed_engine()
    sim = ViewshedSimOverrides()
    out = dict(footprints)
    missing = sorted(slug for slug, fp in footprints.items() if fp is None and slug in sites)
    if not missing:
        return out

    total = len(missing)
    if verbose:
        print(
            f"link footprints: vectorize {total} missing footprint(s)",
            flush=True,
        )

    for idx, slug in enumerate(missing):
        if progress is not None:
            progress("vectorize_footprints", idx + 1, total, slug)
        fp = engine.vectorize_site_footprint(
            project_dir,
            preset,
            sites[slug],
            sim=sim,
            verbose=verbose,
        )
        if fp is not None:
            out[slug] = fp
    return out
