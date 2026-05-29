"""On-disk workspace layout types for site suggestions and viewshed batching."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PlannedComposite:
    role: str
    sha: str
    union_gpkg: Path
    manifest: Path


@dataclass(frozen=True)
class PlannedViewshedWorkspace:
    digest: str
    workdir: Path
    output_ppm: Path
    splat_png: Path
    splat_gpkg: Path
    request_json: Path
    site_slugs: tuple[str, ...]


@dataclass(frozen=True)
class WorkspacePlan:
    """Resolved bundle composite paths and viewshed roots for suggestion work."""

    composites: tuple[PlannedComposite, ...]
    viewshed_workspaces: tuple[PlannedViewshedWorkspace, ...]
    viewsheds_root: Path
    splat_tiles_root: Path
