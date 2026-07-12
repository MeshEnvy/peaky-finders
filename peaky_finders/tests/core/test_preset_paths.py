"""Preset path resolution (PEAKY_HOME layout, viewshed root)."""

from __future__ import annotations

from pathlib import Path

import pytest

from peaky_finders.core.preset import (
    peaky_home,
    peaky_projects_dir,
    peaky_share_dir,
    resolve_preset_yaml_arg,
    resolved_preset_cache_dir,
    resolved_preset_slug,
    resolved_viewshed_root,
)
from peaky_finders.core.preset.paths import repo_root


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
    monkeypatch.setattr("peaky_finders.core.preset.paths.repo_root", lambda: workspace)
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


def test_resolved_preset_cache_dir(peaky_test_home: Path) -> None:
    sample = peaky_test_home / "projects" / "sample" / "config.yaml"
    assert resolved_preset_cache_dir(sample) == (sample.parent / ".peaky" / "cache").resolve()


def test_resolved_viewshed_root(peaky_test_home: Path) -> None:
    sample = peaky_test_home / "projects" / "sample" / "config.yaml"
    assert resolved_viewshed_root(sample) == (sample.parent / ".peaky" / "cache" / "viewsheds").resolve()
