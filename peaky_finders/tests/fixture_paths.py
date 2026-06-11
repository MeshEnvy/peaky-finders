"""Shared paths to the isolated test ``PEAKY_HOME`` (not bundled package templates).

``peaky_finders/templates/`` ships production seeds copied to a real ``$PEAKY_HOME``.
``tests/fixtures/peaky_home/`` is a committed fake ``PEAKY_HOME`` for ``./peaky test`` only.
Defaults: ``projects/config.yaml``; sample project: ``projects/sample/config.yaml``.
RF catalogs use generic ``fixture-modem`` / ``fixture-desert`` only.
"""

from __future__ import annotations

from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = TESTS_DIR / "fixtures"
PEAKY_TEST_HOME = FIXTURES_DIR / "peaky_home"
SAMPLE_PROJECT_CONFIG = PEAKY_TEST_HOME / "projects" / "sample" / "config.yaml"
MINIMAL_PRESET_YAML = FIXTURES_DIR / "minimal_preset.yaml"
