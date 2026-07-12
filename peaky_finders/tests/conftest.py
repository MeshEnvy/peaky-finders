"""Make ``peaky_finders`` importable in tests without an editable install."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from fixture_paths import PEAKY_TEST_HOME

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


@pytest.fixture(autouse=True)
def _default_peaky_home_profiles(monkeypatch: pytest.MonkeyPatch) -> None:
    """Route global preset defaults and RF catalogs to committed test fixtures."""
    monkeypatch.setenv("PEAKY_HOME", str(PEAKY_TEST_HOME))
    from peaky_finders.core.home.bundled_templates import ensure_peaky_home

    ensure_peaky_home()


@pytest.fixture
def peaky_test_home(monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated ``PEAKY_HOME`` with ``projects/sample/config.yaml`` (no repo Nevada preset)."""
    monkeypatch.setenv("PEAKY_HOME", str(PEAKY_TEST_HOME))
    monkeypatch.delenv("PEAKY_PROJECTS", raising=False)
    monkeypatch.delenv("PEAKY_SHARE", raising=False)
    return PEAKY_TEST_HOME


@pytest.fixture(autouse=True)
def _reset_splatter_session() -> None:
    """Drop the process-wide splatter DEM session between tests."""
    try:
        from splatter import reset_session
    except ImportError:
        yield
        return
    reset_session()
    yield
    reset_session()
