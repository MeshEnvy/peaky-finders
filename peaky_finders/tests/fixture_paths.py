"""Shared paths to committed test fixtures (no production presets)."""

from __future__ import annotations

from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = TESTS_DIR / "fixtures"
PEAKY_TEST_HOME = FIXTURES_DIR / "peaky_home"
SAMPLE_PROJECT_CONFIG = PEAKY_TEST_HOME / "projects" / "sample" / "config.yaml"
MINIMAL_PRESET_YAML = FIXTURES_DIR / "minimal_preset.yaml"
