"""Preset CLI path resolution (PEAKY_HOME layout, KMZ output, bundle data root)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from peaky_finders.sites_job import (
    BundleConfig,
    load_preset,
    peaky_home,
    peaky_projects_dir,
    repo_root,
    resolve_preset_yaml_arg,
    resolved_aggregate_kmz_path,
    resolved_inspect_tmp_parent,
    resolved_preset_build_dir,
    resolved_preset_build_tmp_dir,
    resolved_preset_bundle_data_dir,
    resolved_preset_slug,
)


@pytest.fixture
def peaky_env(monkeypatch: pytest.MonkeyPatch) -> Path:
    home = repo_root()
    monkeypatch.setenv("PEAKY_HOME", str(home))
    monkeypatch.delenv("PEAKY_PROJECTS", raising=False)
    return home


def test_peaky_home_defaults_to_repo_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PEAKY_HOME", raising=False)
    assert peaky_home() == repo_root()


def test_peaky_home_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PEAKY_HOME", str(tmp_path))
    assert peaky_home() == tmp_path.resolve()
    assert peaky_projects_dir() == (tmp_path / "projects").resolve()


def test_peaky_projects_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    projects = tmp_path / "custom-projects"
    monkeypatch.setenv("PEAKY_HOME", str(tmp_path))
    monkeypatch.setenv("PEAKY_PROJECTS", str(projects))
    assert peaky_projects_dir() == projects.resolve()


def test_resolve_project_slug_sample(peaky_test_home: Path) -> None:
    sample = peaky_test_home / "projects" / "sample" / "config.yaml"
    assert resolve_preset_yaml_arg("sample") == sample.resolve()


def test_resolved_preset_slug_for_project_config(peaky_test_home: Path) -> None:
    sample = peaky_test_home / "projects" / "sample" / "config.yaml"
    assert resolved_preset_slug(sample) == "sample"
    assert resolved_aggregate_kmz_path(sample) == (sample.parent / "sample.kmz").resolve()


def test_resolved_preset_build_dir_for_project_config(peaky_test_home: Path) -> None:
    sample = peaky_test_home / "projects" / "sample" / "config.yaml"
    assert resolved_preset_build_dir(sample) == (sample.parent / "build").resolve()
    assert resolved_preset_build_tmp_dir(sample) == (sample.parent / "build" / "tmp").resolve()


def test_resolved_inspect_tmp_parent_with_project_config(peaky_test_home: Path) -> None:
    sample = peaky_test_home / "projects" / "sample" / "config.yaml"
    data_gdb = sample.parent / "data" / "inspect_tmp_walk" / "probe.gdb"
    data_gdb.mkdir(parents=True, exist_ok=True)
    assert resolved_inspect_tmp_parent(data_gdb) == resolved_preset_build_tmp_dir(sample)


def test_resolved_inspect_tmp_parent_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PEAKY_HOME", str(tmp_path))
    p = tmp_path / "orphan" / "x.gdb"
    p.mkdir(parents=True)
    assert resolved_inspect_tmp_parent(p) == (tmp_path / "build" / "tmp").resolve()


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
