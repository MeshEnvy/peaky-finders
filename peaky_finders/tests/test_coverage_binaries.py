"""Tests for cwd config.yaml resolution and the splatter PyO3 extension."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from peaky_finders.sites_job import require_cwd_config_yaml, resolved_skadi_mirror_dir


def test_require_cwd_config_yaml_aborts_when_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(FileNotFoundError, match="config.yaml not found"):
        require_cwd_config_yaml()


def test_require_cwd_config_yaml_returns_path(tmp_path: Path, monkeypatch) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("simulation:\n  provider: splatter\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert require_cwd_config_yaml() == cfg.resolve()


def test_resolved_skadi_mirror_dir_honors_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SPLAT_CACHE", str(tmp_path / "mirror"))
    assert resolved_skadi_mirror_dir() == (tmp_path / "mirror").resolve()


def test_splatter_extension_exposes_session_and_batch_api() -> None:
    import splatter
    from splatter._core import Session

    assert hasattr(Session, "run")
    assert hasattr(Session, "run_batch")
    assert hasattr(Session, "link_mutual_viable")
    assert hasattr(Session, "link_mutual_batch")
    assert splatter.SPLAT_CACHE_SCHEMA_VERSION == 7


def test_splatter_input_sha256_matches_python_golden() -> None:
    from splatter import input_sha256

    from peaky_finders.models import SplatCoverageRequest
    from peaky_finders.splat_input_hash import splat_input_sha256

    golden = "cb6f3b7668a317b53d8c08ef264908b2240da832cb4eef0be54fa6216713a1e0"
    repo = Path(__file__).resolve().parents[1]
    fixture = repo / "tests" / "fixtures" / "splat_request_hash_fixture.json"
    req = SplatCoverageRequest.model_validate_json(fixture.read_text(encoding="utf-8"))
    payload = json.dumps(req.model_dump(mode="json"))
    assert input_sha256(payload) == golden
    assert splat_input_sha256(req) == golden
