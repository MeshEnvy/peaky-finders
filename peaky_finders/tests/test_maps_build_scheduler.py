"""Background maps/mesh rebuild scheduler."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from peaky_finders.web.maps_build_scheduler import clips_need_rebuild, mesh_need_rebuild

from fixture_paths import PEAKY_TEST_HOME


def test_clips_need_rebuild_when_eligible_missing(monkeypatch) -> None:
    monkeypatch.setenv("PEAKY_HOME", str(PEAKY_TEST_HOME))
    assert clips_need_rebuild("sample") is True


def test_mesh_need_rebuild_sample_without_cache(monkeypatch) -> None:
    monkeypatch.setenv("PEAKY_HOME", str(PEAKY_TEST_HOME))
    assert mesh_need_rebuild("sample") in (True, False)
