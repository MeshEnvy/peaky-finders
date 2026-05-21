"""Make ``peaky_finders`` importable in tests without an editable install."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from fixture_paths import PEAKY_TEST_HOME

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


@pytest.fixture
def peaky_test_home(monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated ``PEAKY_HOME`` with ``projects/sample/config.yaml`` (no repo Nevada preset)."""
    monkeypatch.setenv("PEAKY_HOME", str(PEAKY_TEST_HOME))
    monkeypatch.delenv("PEAKY_PROJECTS", raising=False)
    monkeypatch.delenv("PEAKY_CACHE", raising=False)
    return PEAKY_TEST_HOME
