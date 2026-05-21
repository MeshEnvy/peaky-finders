"""Preset CLI path resolution (PEAKY_HOME layout, KMZ output, bundle data root)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from peaky_finders.sites_job import (
    BundleConfig,
    load_preset,
    peaky_cache_dir,
    peaky_home,
    peaky_projects_dir,
    repo_root,
    resolve_preset_yaml_arg,
    resolved_aggregate_kmz_path,
    resolved_cache_base,
    resolved_preset_bundle_data_dir,
    resolved_preset_slug,
)


@pytest.fixture
def peaky_env(monkeypatch: pytest.MonkeyPatch) -> Path:
    home = repo_root()
    monkeypatch.setenv("PEAKY_HOME", str(home))
    monkeypatch.delenv("PEAKY_PROJECTS", raising=False)
    monkeypatch.delenv("PEAKY_CACHE", raising=False)
    return home


def test_peaky_home_defaults_to_repo_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PEAKY_HOME", raising=False)
    assert peaky_home() == repo_root()


def test_peaky_home_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PEAKY_HOME", str(tmp_path))
    assert peaky_home() == tmp_path.resolve()
    assert peaky_projects_dir() == (tmp_path / "projects").resolve()
    assert peaky_cache_dir() == (tmp_path / ".cache").resolve()


def test_peaky_projects_and_cache_env_overrides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    projects = tmp_path / "custom-projects"
    cache = tmp_path / "custom-cache"
    monkeypatch.setenv("PEAKY_HOME", str(tmp_path))
    monkeypatch.setenv("PEAKY_PROJECTS", str(projects))
    monkeypatch.setenv("PEAKY_CACHE", str(cache))
    assert peaky_projects_dir() == projects.resolve()
    assert peaky_cache_dir() == cache.resolve()
    assert resolved_cache_base() == cache.resolve()


def test_resolve_project_slug_sample(peaky_test_home: Path) -> None:
    sample = peaky_test_home / "projects" / "sample" / "config.yaml"
    assert resolve_preset_yaml_arg("sample") == sample.resolve()


def test_resolved_preset_slug_for_project_config(peaky_test_home: Path) -> None:
    sample = peaky_test_home / "projects" / "sample" / "config.yaml"
    assert resolved_preset_slug(sample) == "sample"
    assert resolved_aggregate_kmz_path(sample) == (sample.parent / "sample.kmz").resolve()


def test_resolved_preset_bundle_data_dir_uses_inputs_root(peaky_test_home: Path) -> None:
    sample = peaky_test_home / "projects" / "sample" / "config.yaml"
    preset = load_preset(sample)
    data_dir = resolved_preset_bundle_data_dir(preset_path=sample, preset=preset)
    assert data_dir == (sample.parent / "data").resolve()


def test_resolved_preset_bundle_data_dir_defaults_to_peaky_home_data(
    peaky_env: Path,
) -> None:
    preset = SimpleNamespace(bundle=None)
    cfg = peaky_env / "job.yaml"
    data_dir = resolved_preset_bundle_data_dir(preset_path=cfg, preset=preset)
    assert data_dir == (peaky_env / "data").resolve()


def test_resolved_preset_bundle_data_dir_honors_cli_override(tmp_path: Path) -> None:
    preset = SimpleNamespace(bundle=BundleConfig.model_validate({"inputs_root": "data", "aoi": []}))
    override = tmp_path / "custom-data"
    data_dir = resolved_preset_bundle_data_dir(
        preset_path=tmp_path / "config.yaml",
        preset=preset,
        cli_override=override,
    )
    assert data_dir == override.resolve()
