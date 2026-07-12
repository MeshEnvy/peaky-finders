"""PLSS loc cache for serve."""

from __future__ import annotations

from pathlib import Path

from peaky_finders.core.plss.fetch import (
    apply_plss_on_preset,
    loc_plss_resolved,
    loc_stamp,
    plss_loc_cache_path,
    read_plss_loc_cache,
    write_plss_loc_cache,
)
from peaky_finders.core.preset.io import read_preset_yaml_tree


def test_loc_stamp_stable() -> None:
    assert loc_stamp(39.0, -117.0) == "39.000000,-117.000000"


def test_loc_cache_roundtrip(tmp_path: Path) -> None:
    d = {
        "39.000000,-117.000000": {"plss": "NV210300N0230E0SN360ASENW"},
        "40.000000,-118.000000": {"plss": "NV210300N0230E0SN360ASENW"},
    }
    write_plss_loc_cache(tmp_path, d.copy())
    assert read_plss_loc_cache(tmp_path) == d


def test_loc_plss_resolved() -> None:
    assert loc_plss_resolved({"plss": "x"})
    assert not loc_plss_resolved({})


def test_plss_loc_cache_path(tmp_path: Path) -> None:
    assert plss_loc_cache_path(tmp_path) == tmp_path / "plss" / "by_loc.json"


def test_apply_plss_on_preset_updates_site(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "simulation:\n  radius_km: 10\n"
        "display: {}\n"
        "sites:\n  hub:\n    name: Hub\n    loc: [40.0, -119.0]\n",
        encoding="utf-8",
    )
    apply_plss_on_preset(cfg, "hub", "NV210300N0230E0SN360ASENW")
    _, root = read_preset_yaml_tree(cfg)
    assert root["sites"]["hub"]["plss"] == "NV210300N0230E0SN360ASENW"
