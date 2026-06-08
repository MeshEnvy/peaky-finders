"""``peaky new`` project scaffolding."""

from __future__ import annotations

from pathlib import Path

import pytest

from peaky_finders.new_cli import (
    build_new_parser,
    resolve_new_project_dir,
    run_new,
    scaffold_project,
    validate_project_slug,
)
from peaky_finders.sites_job import load_preset


def test_validate_project_slug_accepts_simple_names() -> None:
    assert validate_project_slug("nevada") == "nevada"
    assert validate_project_slug("my-region") == "my-region"
    assert validate_project_slug("area_2") == "area_2"


@pytest.mark.parametrize("slug", ["", "9bad", "../x", "a/b", ".", ".."])
def test_validate_project_slug_rejects_invalid(slug: str) -> None:
    with pytest.raises(ValueError):
        validate_project_slug(slug)


def test_scaffold_project_creates_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PEAKY_HOME", str(tmp_path))
    project_dir = scaffold_project("demo", cwd=tmp_path)
    assert project_dir == (tmp_path / "projects" / "demo").resolve()
    assert (project_dir / "config.yaml").is_file()
    assert (project_dir / "data" / "aoi").is_dir()
    assert (project_dir / "data" / "include").is_dir()
    assert (project_dir / "data" / "exclude").is_dir()
    load_preset(project_dir / "config.yaml")


def test_scaffold_project_refuses_existing_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PEAKY_HOME", str(tmp_path))
    scaffold_project("demo", cwd=tmp_path)
    with pytest.raises(FileExistsError):
        scaffold_project("demo", cwd=tmp_path)


def test_scaffold_project_force_overwrites_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PEAKY_HOME", str(tmp_path))
    project_dir = scaffold_project("demo", cwd=tmp_path)
    cfg = project_dir / "config.yaml"
    cfg.write_text("sites: {}\n", encoding="utf-8")
    scaffold_project("demo", force=True, cwd=tmp_path)
    load_preset(cfg)


def test_scaffold_project_here_uses_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PEAKY_HOME", str(tmp_path))
    here = tmp_path / "standalone"
    here.mkdir()
    project_dir = scaffold_project("ignored-slug", here=True, cwd=here)
    assert project_dir == here.resolve()
    assert (here / "config.yaml").is_file()


def test_resolve_new_project_dir_parent_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PEAKY_HOME", str(tmp_path))
    parent = tmp_path / "custom"
    assert resolve_new_project_dir("x", parent=parent, cwd=tmp_path) == (parent / "x").resolve()


def test_run_new_prints_next_steps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setenv("PEAKY_HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    parser = build_new_parser()
    args = parser.parse_args(["demo-region"])
    assert run_new(args) == 0
    out = capsys.readouterr().out
    assert "created project at" in out
    assert "cd projects/demo-region" in out
    assert (tmp_path / "projects" / "demo-region" / "config.yaml").is_file()


def test_run_new_invalid_slug_returns_2(capsys) -> None:
    parser = build_new_parser()
    args = parser.parse_args(["bad slug"])
    assert run_new(args) == 2
    assert "invalid project slug" in capsys.readouterr().err
