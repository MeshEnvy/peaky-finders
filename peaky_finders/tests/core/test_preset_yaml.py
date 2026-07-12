"""Preset YAML loader and rejection of legacy JSON preset paths."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from peaky_finders.core.preset import (
    dump_preset_yaml_document,
    load_preset,
    load_preset_sites,
    read_preset_yaml_tree,
    require_preset_yaml_path,
    resolved_coverage_dispatcher_max_workers,
)

from fixture_paths import MINIMAL_PRESET_YAML as FIXTURE_MINIMAL


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


def test_resolved_coverage_dispatcher_max_workers_defaults() -> None:
    preset = load_preset(FIXTURE_MINIMAL)
    assert preset.simulation.max_workers.splatter == 1
    assert resolved_coverage_dispatcher_max_workers(preset) == 1


def test_preset_yaml_roundtrip_preserves_comments(tmp_path: Path) -> None:
    preset_path = tmp_path / "with_comments.yaml"
    text = FIXTURE_MINIMAL.read_text(encoding="utf-8")
    assert "# Minimal preset" in text
    preset_path.write_text(text, encoding="utf-8")

    yaml_rt, root = read_preset_yaml_tree(preset_path)
    sites = root["sites"]
    assert sites is not None
    sites["test-site"]["description"] = "keep"

    dump_preset_yaml_document(yaml_rt, root, preset_path)
    rewritten = preset_path.read_text(encoding="utf-8")
    assert "# Minimal preset" in rewritten


def test_load_preset_rejects_site_type(tmp_path: Path) -> None:
    preset_path = tmp_path / "config.yaml"
    preset_path.write_text(
        """
simulation:
  modem: fixture-modem
  environment: fixture-desert
  transmitter: {height_m: 2.0, gain_dbi: 3.0, loss_db: 2.0}
  receiver: {height_m: 2.0, gain_dbi: 3.0, loss_db: 2.0}
display:
  colormap: rainbow
  min_dbm: -130.0
  max_dbm: -80.0
sites:
  hub:
    name: Hub
    loc: [39.5, -119.5]
  russel-bridge:
    type: goal
    name: Russell Bridge
    loc: [39.6, -119.4]
links: []
""".strip(),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="type is removed"):
        load_preset_sites(preset_path)
