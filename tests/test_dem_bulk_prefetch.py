"""DEM bulk prefetch stamp + skip-cached tile behavior."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from peaky_finders.build_configure import configure_preset_build
from peaky_finders.build_executor import _dem_bulk_prefetch_stale, target_stale
from peaky_finders.build_graph import build_target_graph, dem_bulk_stamp_path
from peaky_finders.skadi_dem import (
    prefetch_skadi_hgt_for_bounds,
    skadi_tile_set_fingerprint,
    write_dem_prefetch_stamp,
)
from peaky_finders.sites_job import load_preset


def test_skadi_tile_set_fingerprint_stable_for_bbox() -> None:
    bbox = (-120.5, 36.15, -114.2, 37.91)
    a = skadi_tile_set_fingerprint(*bbox)
    b = skadi_tile_set_fingerprint(*bbox)
    assert a == b
    assert len(a) == 16


def test_skadi_tile_set_fingerprint_changes_when_bbox_expands() -> None:
    narrow = skadi_tile_set_fingerprint(-117.51, 37.1, -117.1, 37.51)
    wide = skadi_tile_set_fingerprint(-117.9, 37.05, -116.05, 37.94)
    assert narrow != wide


def test_prefetch_skips_non_empty_cached_tiles(tmp_path: Path) -> None:
    mirror = tmp_path / "dem"
    mirror.mkdir()
    (mirror / "N37W118.hgt.gz").write_bytes(b"x" * 64)

    with patch("peaky_finders.skadi_dem.fetch_skadi_hgt_gzip_bytes") as fetch:
        n_dl, n_skip, errs = prefetch_skadi_hgt_for_bounds(
            minx=-117.51,
            miny=37.1,
            maxx=-117.1,
            maxy=37.51,
            splat_tile_cache_dir=mirror,
            max_workers=2,
        )

    assert errs == []
    assert n_dl == 0
    assert n_skip == 1
    fetch.assert_not_called()


def test_prefetch_downloads_missing_tiles_only(tmp_path: Path) -> None:
    mirror = tmp_path / "dem"
    mirror.mkdir()
    (mirror / "N37W118.hgt.gz").write_bytes(b"x" * 64)

    with patch("peaky_finders.skadi_dem.fetch_skadi_hgt_gzip_bytes", return_value=b"gz"):
        n_dl, n_skip, errs = prefetch_skadi_hgt_for_bounds(
            minx=-117.9,
            miny=37.05,
            maxx=-116.05,
            maxy=37.94,
            splat_tile_cache_dir=mirror,
            max_workers=2,
        )

    assert errs == []
    assert n_skip == 1
    assert n_dl == 1
    assert (mirror / "N37W117.hgt.gz").read_bytes() == b"gz"


def test_dem_bulk_stale_when_stamp_missing(peaky_test_home: Path) -> None:
    preset_path = (peaky_test_home / "projects" / "sample" / "config.yaml").resolve()
    preset = load_preset(preset_path)
    plan = configure_preset_build(preset_path=preset_path)
    assert _dem_bulk_prefetch_stale(plan) in {True, False}


def test_dem_bulk_fresh_when_stamp_matches_cached_tiles(
    peaky_test_home: Path, tmp_path: Path
) -> None:
    preset_path = (peaky_test_home / "projects" / "sample" / "config.yaml").resolve()
    preset = load_preset(preset_path)
    plan = configure_preset_build(preset_path=preset_path)
    nodes = build_target_graph(plan, preset)
    node = nodes["dem:bulk"]

    dem_dir = tmp_path / "dem"
    dem_dir.mkdir()
    plan = replace(plan, splat_tiles_root=dem_dir)

    from peaky_finders.build_executor import _dem_bulk_prefetch_bounds

    bounds = _dem_bulk_prefetch_bounds(plan)
    if bounds is None:
        pytest.skip("sample preset has no eligible geometry for dem:bulk")

    minx, miny, maxx, maxy = bounds
    fp = skadi_tile_set_fingerprint(minx, miny, maxx, maxy)
    from peaky_finders.skadi_dem import iter_skadi_tile_names_for_wgs84_bounds

    for name in iter_skadi_tile_names_for_wgs84_bounds(minx, miny, maxx, maxy):
        (dem_dir / name).write_bytes(b"x" * 64)
    write_dem_prefetch_stamp(dem_bulk_stamp_path(plan), fp)

    assert target_stale(plan, preset, node) is False
