"""Global RF profile catalogs under PEAKY_HOME."""

from __future__ import annotations

from pathlib import Path

import pytest

from peaky_finders.peaky_profiles import (
    ensure_peaky_home_profiles,
    load_environment_presets_catalog,
    load_modem_presets_catalog,
    peaky_home_environments_path,
    peaky_home_modems_path,
    profile_catalog_fingerprint_hex,
)
from peaky_finders.sites_job import peaky_home
from rf_fixtures import TEST_ENVIRONMENT_PRESET, TEST_MODEM_PRESET


def test_committed_fixture_catalogs_are_test_only() -> None:
    assert TEST_MODEM_PRESET in load_modem_presets_catalog()
    assert TEST_ENVIRONMENT_PRESET in load_environment_presets_catalog()


def test_ensure_peaky_home_profiles_seeds_from_bundled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PEAKY_HOME", str(tmp_path))
    ensure_peaky_home_profiles()
    assert peaky_home_modems_path().is_file()
    assert peaky_home_environments_path().is_file()
    assert "meshcore-us" in load_modem_presets_catalog()
    assert "nevada-desert" in load_environment_presets_catalog()


def test_profile_catalog_fingerprint_changes_with_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PEAKY_HOME", str(tmp_path))
    ensure_peaky_home_profiles()
    before = profile_catalog_fingerprint_hex()
    env_path = peaky_home_environments_path()
    env_path.write_text(
        env_path.read_text(encoding="utf-8") + "\n# touch\n",
        encoding="utf-8",
    )
    after = profile_catalog_fingerprint_hex()
    assert before != after


def test_peaky_home_modems_path_under_home() -> None:
    assert peaky_home_modems_path() == peaky_home() / "modems.yaml"
