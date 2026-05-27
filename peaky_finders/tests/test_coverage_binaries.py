"""Tests for cwd config.yaml resolution and optional coverage binaries."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from peaky_finders.sites_job import require_cwd_config_yaml, resolved_skadi_mirror_dir


def test_require_cwd_config_yaml_aborts_when_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(FileNotFoundError, match="config.yaml not found"):
        require_cwd_config_yaml()


def test_require_cwd_config_yaml_returns_path(tmp_path: Path, monkeypatch) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("simulation:\n  provider: los\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert require_cwd_config_yaml() == cfg.resolve()


def test_resolved_skadi_mirror_dir_honors_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SPLAT_CACHE", str(tmp_path / "mirror"))
    assert resolved_skadi_mirror_dir() == (tmp_path / "mirror").resolve()


@pytest.mark.skipif(shutil.which("splatter") is None, reason="splatter not on PATH")
def test_splatter_binary_exposes_run_batch() -> None:
    r = subprocess.run(
        ["splatter", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0
    assert "run-batch" in r.stdout
