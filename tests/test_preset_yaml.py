"""Preset YAML loader and rejection of legacy JSON preset paths."""

from __future__ import annotations

from pathlib import Path

import pytest

from peaky_finders.sites_job import (
    CoverageProvider,
    dump_preset_yaml_document,
    load_preset,
    read_preset_yaml_tree,
    require_preset_yaml_path,
    resolved_coverage_dispatcher_max_workers,
)

FIXTURE_MINIMAL = Path(__file__).resolve().parent / "fixtures" / "minimal_preset.yaml"


def test_load_minimal_preset_yaml_fixture() -> None:
    preset = load_preset(FIXTURE_MINIMAL)
    assert preset.sites["test-site"].name == "Test Site"


def test_load_preset_rejects_json_suffix(tmp_path: Path) -> None:
    p = tmp_path / "job.json"
    p.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match=r"\.yaml|\.yml|JSON"):
        load_preset(p)


def test_require_preset_yaml_path_wrong_suffix() -> None:
    with pytest.raises(ValueError, match=r"\.yaml"):
        require_preset_yaml_path(Path("preset.txt"))


def test_resolved_coverage_dispatcher_max_workers_per_provider_defaults() -> None:
    preset = load_preset(FIXTURE_MINIMAL)
    assert preset.simulation.max_workers.los == 1
    assert preset.simulation.max_workers.splat == 8
    assert resolved_coverage_dispatcher_max_workers(preset) == 1
    splat = preset.model_copy(
        update={
            "simulation": preset.simulation.model_copy(update={"provider": CoverageProvider.SPLAT})
        }
    )
    assert resolved_coverage_dispatcher_max_workers(splat) == 8


def test_preset_yaml_roundtrip_preserves_comments(tmp_path: Path) -> None:
    preset_path = tmp_path / "with_comments.yaml"
    text = FIXTURE_MINIMAL.read_text(encoding="utf-8")
    assert "# Minimal preset" in text
    preset_path.write_text(text, encoding="utf-8")

    yaml_rt, root = read_preset_yaml_tree(preset_path)
    sites = root["sites"]
    assert sites is not None
    sites["test-site"]["rationale"] = "keep"

    dump_preset_yaml_document(yaml_rt, root, preset_path)
    rewritten = preset_path.read_text(encoding="utf-8")
    assert "# Minimal preset" in rewritten
