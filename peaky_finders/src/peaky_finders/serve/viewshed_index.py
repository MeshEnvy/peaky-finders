"""Bulk viewshed cache index for fast map load."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from peaky_finders.core.preset import Preset, SiteEntry
from peaky_finders.core.preset.paths import resolved_viewshed_root
from peaky_finders.core.rf.mapping import preset_to_request
from peaky_finders.core.viewshed.kml import load_bounds_from_manifest
from peaky_finders.core.viewshed.workspace import (
    resolved_viewshed_workdir,
    viewshed_request_digest_matches,
    viewshed_workspace_digest,
)
from peaky_finders.serve.viewshed import (
    image_coordinates_from_bbox,
    viewshed_cache_png_api_path,
)
from peaky_finders.serve.viewshed_sim import ViewshedSimOverrides
from peaky_finders.core.viewshed.raster import splat_png_is_valid_fast


def _bounds_from_workdir(workdir: Path) -> dict[str, float] | None:
    """Manifest-first bounds read — no GDAL on the hot path."""
    wd = Path(workdir).expanduser().resolve()
    bounds = load_bounds_from_manifest(wd / "manifest.json")
    if bounds is not None:
        return bounds
    return None


def _site_index_entry(
    project_slug: str,
    site_slug: str,
    site: SiteEntry,
    *,
    preset: Preset,
    viewshed_root: Path,
    sim: ViewshedSimOverrides,
) -> dict[str, object]:
    req = preset_to_request(
        preset,
        float(site.lat),
        float(site.lon),
        site=site,
        radius_km=sim.radius_km,
        raster_dimension=sim.raster_dimension,
    )
    digest = viewshed_workspace_digest(request=req)
    workdir = resolved_viewshed_workdir(digest=digest, viewshed_root=viewshed_root)
    png = workdir / "splat.png"
    if not png.is_file():
        return {"slug": site_slug, "ready": False, "digest": digest}
    if not viewshed_request_digest_matches(workdir, expected_workspace_digest=digest):
        return {"slug": site_slug, "ready": False, "digest": digest}
    if not splat_png_is_valid_fast(png):
        return {"slug": site_slug, "ready": False, "digest": digest}
    bounds = _bounds_from_workdir(workdir)
    if bounds is None:
        return {"slug": site_slug, "ready": False, "digest": digest}
    return {
        "slug": site_slug,
        "ready": True,
        "digest": digest,
        "url": viewshed_cache_png_api_path(project_slug, digest),
        "coordinates": image_coordinates_from_bbox(bounds),
    }


def build_viewshed_index(
    project_slug: str,
    project_dir: Path,
    sites: Mapping[str, SiteEntry],
    *,
    preset: Preset,
    sim: ViewshedSimOverrides | None = None,
) -> dict[str, object]:
    """Return ready/missing viewshed overlay metadata for all preset sites."""
    sim = sim or ViewshedSimOverrides()
    viewshed_root = resolved_viewshed_root(project_dir / "config.yaml")
    entries: dict[str, object] = {}
    ready_count = 0
    for slug, site in sorted(sites.items()):
        row = _site_index_entry(
            project_slug,
            slug,
            site,
            preset=preset,
            viewshed_root=viewshed_root,
            sim=sim,
        )
        entries[slug] = row
        if row.get("ready"):
            ready_count += 1
    return {
        "project": project_slug,
        "sites": entries,
        "ready_count": ready_count,
        "total": len(sites),
        "sim": {
            "radius_km": sim.radius_km,
            "raster_dimension": sim.raster_dimension,
        },
    }


def resolve_viewshed_cache_png(project_dir: Path, digest: str) -> Path | None:
    """Return ``splat.png`` for an immutable digest workspace when valid."""
    label = str(digest).strip().lower()
    if not label or len(label) != 64:
        return None
    viewshed_root = resolved_viewshed_root(project_dir / "config.yaml")
    workdir = resolved_viewshed_workdir(digest=label, viewshed_root=viewshed_root)
    png = workdir / "splat.png"
    if not png.is_file():
        return None
    if not viewshed_request_digest_matches(workdir, expected_workspace_digest=label):
        return None
    if not splat_png_is_valid_fast(png):
        return None
    return png.resolve()
