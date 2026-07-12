"""Viewshed coverage provider protocol."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from peaky_finders.core.preset import CoverageProvider


class ViewshedCoverageProvider(Protocol):
    """Run RF coverage in a workspace directory (``request.json`` + ``output.ppm``)."""

    @property
    def name(self) -> CoverageProvider: ...

    def run_site(self, *, data_dir: Path, coverage_verbose: bool = False) -> int:
        """Run one workspace; return process exit code (0 = success)."""
        ...

    def run_batch(
        self,
        *,
        viewshed_root: Path,
        batch_jobs: int = 1,
        coverage_verbose: bool = False,
        requests_json: str | None = None,
    ) -> int:
        """Run many workspaces under *viewshed_root*; return exit code."""
        ...
