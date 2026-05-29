"""PLSS/MLRS cache, input keys, and incremental build freshness."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import pytest

from fixture_paths import SAMPLE_PROJECT_CONFIG
from peaky_finders.build_keys import write_build_key
from peaky_finders.plss_mlrs_fetch import (
    loc_plss_resolved,
    loc_stamp,
    plss_bundle_build_stale,
    plss_bundle_key_path,
    plss_mlrs_loc_cache_path,
    plss_sites_loc_digest,
    populate_preset_plss_mlrs_file,
    read_plss_mlrs_loc_cache,
    refresh_plss_mlrs_for_bundle,
    write_plss_mlrs_loc_cache,
)
from peaky_finders.preset_stamps import stamp_file_is_current, write_stamp
from peaky_finders.sites_job import dump_preset_yaml_document, load_preset, read_preset_yaml_tree


def test_loc_stamp_stable() -> None:
    assert loc_stamp(39.0, -117.0) == "39.000000,-117.000000"


def test_loc_cache_roundtrip(tmp_path: Path) -> None:
    d = {
        "39.000000,-117.000000": {"plss": "NV; Sec. 1", "mlrs": "NV123"},
        "40.000000,-118.000000": {"plss": "NV; Sec. 2", "mlrs": "NV456"},
    }
    write_plss_mlrs_loc_cache(tmp_path, d.copy())
    assert read_plss_mlrs_loc_cache(tmp_path) == d


def test_loc_plss_resolved() -> None:
    assert loc_plss_resolved({"plss": "NV", "mlrs": ""})
    assert loc_plss_resolved({"plss": "", "mlrs": "NV123"})
    assert not loc_plss_resolved({"plss": "", "mlrs": ""})


def test_plss_bundle_build_stale_when_key_missing(tmp_path: Path) -> None:
    preset = load_preset(SAMPLE_PROJECT_CONFIG)
    assert plss_bundle_build_stale(cache_base=tmp_path, preset=preset)


def test_plss_bundle_build_fresh_after_refresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    preset_path = tmp_path / "job.yaml"
    preset_path.write_text(
        SAMPLE_PROJECT_CONFIG.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    preset = load_preset(preset_path)

    def fake_plss(_lon: float, _lat: float, **_: object) -> tuple[str, str]:
        return "NV; Sec. 1", "NV123"

    monkeypatch.setattr("peaky_finders.plss_mlrs_fetch.plss_mlrs_for_point", fake_plss)
    refresh_plss_mlrs_for_bundle(
        preset_path=preset_path,
        cache_base=tmp_path,
        preset=preset,
    )
    preset = load_preset(preset_path)
    assert not plss_bundle_build_stale(cache_base=tmp_path, preset=preset)
    assert plss_bundle_key_path(tmp_path).is_file()
    assert plss_mlrs_loc_cache_path(tmp_path).is_file()


def test_populate_uses_cache_without_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    preset_path = tmp_path / "job.yaml"
    preset_path.write_text(
        SAMPLE_PROJECT_CONFIG.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    preset = load_preset(preset_path)
    loc_cache: dict[str, dict[str, str]] = {}
    for ent in preset.sites.values():
        loc_cache[loc_stamp(ent.lat, ent.lon)] = {"plss": "cached", "mlrs": "CACHED"}

    def boom(_lon: float, _lat: float, **_: object) -> tuple[str, str]:
        raise AssertionError("CadNSDI should not be queried for resolved locs")

    monkeypatch.setattr("peaky_finders.plss_mlrs_fetch.plss_mlrs_for_point", boom)
    network_count = populate_preset_plss_mlrs_file(
        preset_path,
        site_slugs=set(preset.sites.keys()),
        loc_cache=loc_cache,
        force_network=False,
    )
    assert network_count == 0


def test_populate_fetches_only_unresolved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    preset_path = tmp_path / "job.yaml"
    preset_path.write_text(
        SAMPLE_PROJECT_CONFIG.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    preset = load_preset(preset_path)
    slugs = sorted(preset.sites.keys())
    resolved_slug = slugs[0]
    unresolved_slug = slugs[1]
    resolved_ent = preset.sites[resolved_slug]
    loc_cache = {
        loc_stamp(resolved_ent.lat, resolved_ent.lon): {"plss": "cached", "mlrs": "CACHED"},
    }

    calls: list[tuple[float, float]] = []

    def fake_plss(lon: float, lat: float, **_: object) -> tuple[str, str]:
        calls.append((lon, lat))
        return "NV; Sec. 2", "NV456"

    monkeypatch.setattr("peaky_finders.plss_mlrs_fetch.plss_mlrs_for_point", fake_plss)
    network_count = populate_preset_plss_mlrs_file(
        preset_path,
        site_slugs={resolved_slug, unresolved_slug},
        loc_cache=loc_cache,
        force_network=False,
    )
    assert network_count == 1
    assert len(calls) == 1
    unresolved_ent = preset.sites[unresolved_slug]
    assert calls[0] == (unresolved_ent.lon, unresolved_ent.lat)


def test_stamp_current_after_yaml_touch_without_content_change(tmp_path: Path) -> None:
    preset_path = tmp_path / "job.yaml"
    preset_path.write_text(
        SAMPLE_PROJECT_CONFIG.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    write_stamp("simulation", preset_path, quiet=True)
    before = preset_path.read_text(encoding="utf-8")
    time.sleep(0.02)
    preset_path.write_text(before, encoding="utf-8")
    assert stamp_file_is_current("simulation", preset_path)


def test_plss_sites_loc_digest_changes_when_coords_change(tmp_path: Path) -> None:
    preset_path = tmp_path / "job.yaml"
    preset_path.write_text(
        SAMPLE_PROJECT_CONFIG.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    preset = load_preset(preset_path)
    before = plss_sites_loc_digest(preset)
    y, root = read_preset_yaml_tree(preset_path)
    hub = root["sites"]["hub"]
    hub["loc"] = [float(hub["loc"][0]) + 0.01, float(hub["loc"][1])]
    dump_preset_yaml_document(y, root, preset_path)
    preset = load_preset(preset_path)
    assert plss_sites_loc_digest(preset) != before


def test_plss_bundle_stale_when_coords_change_after_refresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    preset_path = tmp_path / "job.yaml"
    preset_path.write_text(
        SAMPLE_PROJECT_CONFIG.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    preset = load_preset(preset_path)

    monkeypatch.setattr(
        "peaky_finders.plss_mlrs_fetch.plss_mlrs_for_point",
        lambda _lon, _lat, **_: ("NV; Sec. 1", "NV123"),
    )
    refresh_plss_mlrs_for_bundle(
        preset_path=preset_path,
        cache_base=tmp_path,
        preset=preset,
    )
    write_build_key(plss_bundle_key_path(tmp_path), "0" * 64)
    preset = load_preset(preset_path)
    assert plss_bundle_build_stale(cache_base=tmp_path, preset=preset)
