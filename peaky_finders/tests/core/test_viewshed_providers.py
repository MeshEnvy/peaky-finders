"""Viewshed coverage provider registry."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from peaky_finders.core.preset import CoverageProvider
from peaky_finders.core.viewshed.pipeline import run_viewshed_coverage, run_viewshed_site
from peaky_finders.core.viewshed.providers.registry import get_viewshed_coverage_provider


def test_coverage_provider_enum_includes_splat_and_splatter() -> None:
    assert CoverageProvider.SPLAT.value == "splat"
    assert CoverageProvider.SPLATTER.value == "splatter"


def test_get_viewshed_coverage_provider_returns_distinct_instances() -> None:
    splat = get_viewshed_coverage_provider(CoverageProvider.SPLAT)
    splatter = get_viewshed_coverage_provider(CoverageProvider.SPLATTER)
    assert splat.name == CoverageProvider.SPLAT
    assert splatter.name == CoverageProvider.SPLATTER


def test_run_viewshed_site_dispatches_to_provider(tmp_path: Path) -> None:
    wd = tmp_path / "ws"
    wd.mkdir()
    mock_provider = MagicMock()
    mock_provider.run_site.return_value = 0

    with patch(
        "peaky_finders.core.viewshed.pipeline.get_viewshed_coverage_provider",
        return_value=mock_provider,
    ):
        rc = run_viewshed_site(
            provider=CoverageProvider.SPLAT,
            data_dir=wd,
            coverage_verbose=True,
        )

    assert rc == 0
    mock_provider.run_site.assert_called_once_with(data_dir=wd, coverage_verbose=True)


def test_run_viewshed_coverage_passes_provider(tmp_path: Path) -> None:
    wd = tmp_path / "ws"
    wd.mkdir()

    with patch("peaky_finders.core.viewshed.pipeline.run_viewshed_site", return_value=0) as mock_run:
        rc = run_viewshed_coverage(
            site_name="hub",
            provider=CoverageProvider.SPLATTER,
            data_dir=wd,
        )

    assert rc == 0
    mock_run.assert_called_once_with(
        provider=CoverageProvider.SPLATTER,
        data_dir=wd,
        coverage_verbose=False,
    )


def test_splat_provider_requires_request_json(tmp_path: Path) -> None:
    provider = get_viewshed_coverage_provider(CoverageProvider.SPLAT)
    with pytest.raises(FileNotFoundError, match="request.json"):
        provider.run_site(data_dir=tmp_path)
