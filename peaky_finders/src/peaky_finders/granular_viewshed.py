"""Programmatic alias for Makefile ``peaky viewshed`` (workspace-only + explicit phase)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Literal

from peaky_finders.cli import run_splat


def run_viewshed(
    site_slug: str,
    *,
    phase: Literal["request", "coverage", "raster", "footprint"],
    data_dir: Path | None = None,
) -> int:
    ns = SimpleNamespace(
        granular_viewshed_slug=site_slug,
        viewshed_workspace_only=True,
        viewshed_phase=phase,
        data_dir=data_dir,
        force=False,
        verbose=False,
        dem_workers=None,
        quiet=False,
    )
    return run_splat(ns)
