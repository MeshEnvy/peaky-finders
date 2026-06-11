"""Preset CLI path resolution (PEAKY_HOME layout, KMZ output, bundle data root)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from peaky_finders.sites_job import (
    load_preset,
    peaky_home,
    peaky_projects_dir,
    peaky_share_dir,
    repo_root,
    resolve_preset_yaml_arg,
    resolved_aggregate_kmz_path,
    resolved_preset_build_dir,
    resolved_preset_bundle_data_dir,
    resolved_preset_slug,
)


@pytest.fixture
def peaky_env(monkeypatch: pytest.MonkeyPatch) -> Path:
    home = repo_root()
    monkeypatch.setenv("PEAKY_HOME", str(home))
    monkeypatch.delenv("PEAKY_PROJECTS", raising=False)
    monkeypatch.delenv("PEAKY_SHARE", raising=False)
    return home


def test_peaky_home_defaults_to_peaky_home_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    home_dir = workspace / "peaky_home"
    (home_dir / "projects").mkdir(parents=True)
    monkeypatch.setattr("peaky_finders.sites_job.repo_root", lambda: workspace)
    monkeypatch.delenv("PEAKY_HOME", raising=False)
    assert peaky_home() == home_dir.resolve()


def test_peaky_home_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PEAKY_HOME", str(tmp_path))
    assert peaky_home() == tmp_path.resolve()
    assert peaky_projects_dir() == (tmp_path / "projects").resolve()
    assert peaky_share_dir() == (tmp_path / "share").resolve()


def test_peaky_projects_and_share_env_overrides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    projects = tmp_path / "custom-projects"
    share = tmp_path / "custom-share"
    monkeypatch.setenv("PEAKY_HOME", str(tmp_path))
    monkeypatch.setenv("PEAKY_PROJECTS", str(projects))
    monkeypatch.setenv("PEAKY_SHARE", str(share))
    assert peaky_projects_dir() == projects.resolve()
    assert peaky_share_dir() == share.resolve()


def test_resolve_project_slug_sample(peaky_test_home: Path) -> None:
    sample = peaky_test_home / "projects" / "sample" / "config.yaml"
    assert resolve_preset_yaml_arg("sample") == sample.resolve()


def test_resolved_preset_slug_for_project_config(peaky_test_home: Path) -> None:
    sample = peaky_test_home / "projects" / "sample" / "config.yaml"
    assert resolved_preset_slug(sample) == "sample"
    assert resolved_aggregate_kmz_path(sample) == (sample.parent / "build" / "sample.kmz").resolve()


def test_resolved_preset_build_dir(peaky_test_home: Path) -> None:
    sample = peaky_test_home / "projects" / "sample" / "config.yaml"
    assert resolved_preset_build_dir(sample) == (sample.parent / "build").resolve()


def test_resolved_preset_bundle_data_dir_uses_preset_data_dir(peaky_test_home: Path) -> None:
    sample = peaky_test_home / "projects" / "sample" / "config.yaml"
    preset = load_preset(sample)
    data_dir = resolved_preset_bundle_data_dir(preset_path=sample, preset=preset)
    assert data_dir == (sample.parent / "data").resolve()


def test_resolved_preset_bundle_data_dir_without_land_uses_preset_dir(
    tmp_path: Path,
) -> None:
    preset = SimpleNamespace(land=None)
    cfg = tmp_path / "projects" / "demo" / "config.yaml"
    cfg.parent.mkdir(parents=True)
    data_dir = resolved_preset_bundle_data_dir(preset_path=cfg, preset=preset)
    assert data_dir == (cfg.parent / "data").resolve()


def test_resolved_preset_bundle_data_dir_honors_cli_override(tmp_path: Path) -> None:
    preset = SimpleNamespace(land=None)
    override = tmp_path / "custom-data"
    data_dir = resolved_preset_bundle_data_dir(
        preset_path=tmp_path / "config.yaml",
        preset=preset,
        cli_override=override,
    )
    assert data_dir == override.resolve()
