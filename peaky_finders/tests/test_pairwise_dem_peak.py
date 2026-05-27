"""Skadi DEM global max inside WGS-84 polygon (pairwise overlap pins)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from rasterio.transform import from_bounds
from shapely.geometry import box

from peaky_finders import pairwise_dem_peak as dem_peak
from peaky_finders.pairwise_dem_peak import (
    global_max_skadi_elevation_in_polygon,
    skadi_binned_peaks_in_polygon,
)


def _install_fake_tile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    tile_name: str = "N39W115.hgt.gz",
    elev: np.ndarray,
    transform,
) -> Path:
    mirror = tmp_path / "dem"
    mirror.mkdir(parents=True, exist_ok=True)
    tile_path = mirror / tile_name
    tile_path.write_bytes(b"placeholder")
    aff = tuple(getattr(transform, attr) for attr in ("a", "b", "c", "d", "e", "f"))

    def _fake_cached(gz_resolved_posix: str) -> tuple[np.ndarray, tuple[float, ...]]:
        return elev, aff

    monkeypatch.setattr(dem_peak, "_cached_skadi_elev_affine", _fake_cached)
    return mirror


def test_global_max_returns_none_when_mirror_has_no_tiles(tmp_path: Path) -> None:
    g = box(-115.02, 39.01, -115.0, 39.02)
    assert global_max_skadi_elevation_in_polygon(g, tmp_path) is None


def test_skadi_binned_peaks_returns_highest_per_bin(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    transform = from_bounds(-115.0, 39.0, -114.0, 40.0, 21, 21)
    elev = np.full((21, 21), 2000, dtype=np.int32)
    elev[5, 5] = 2500
    elev[15, 15] = 3000
    mirror = _install_fake_tile(monkeypatch, tmp_path, elev=elev, transform=transform)

    search = box(-115.0, 39.0, -114.0, 40.0)
    peaks = skadi_binned_peaks_in_polygon(search, mirror, bin_size_m=1500.0)

    assert len(peaks) == 2
    assert peaks[0][2] == 3000.0
    assert peaks[1][2] == 2500.0


def test_skadi_binned_peaks_respects_search_polygon(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    transform = from_bounds(-115.0, 39.0, -114.0, 40.0, 21, 21)
    elev = np.full((21, 21), 2000, dtype=np.int32)
    elev[15, 5] = 2500
    elev[5, 15] = 3000
    mirror = _install_fake_tile(monkeypatch, tmp_path, elev=elev, transform=transform)

    # Southwest quadrant — only the 2500 m peak at row=15,col=5 should remain.
    search = box(-115.0, 39.0, -114.5, 39.5)
    peaks = skadi_binned_peaks_in_polygon(search, mirror, bin_size_m=1500.0)

    assert len(peaks) == 1
    assert peaks[0][2] == 2500.0


def test_skadi_binned_peaks_parallel_matches_serial(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    transform = from_bounds(-115.0, 39.0, -114.0, 40.0, 21, 21)
    elev_a = np.full((21, 21), 2000, dtype=np.int32)
    elev_a[5, 5] = 2500
    elev_b = np.full((21, 21), 2000, dtype=np.int32)
    elev_b[15, 15] = 3000

    mirror = tmp_path / "dem"
    mirror.mkdir(parents=True, exist_ok=True)
    (mirror / "N39W115.hgt.gz").write_bytes(b"a")
    (mirror / "N39W114.hgt.gz").write_bytes(b"b")
    aff = tuple(getattr(transform, attr) for attr in ("a", "b", "c", "d", "e", "f"))
    tile_elev = {
        str((mirror / "N39W115.hgt.gz").resolve()): (elev_a, aff),
        str((mirror / "N39W114.hgt.gz").resolve()): (elev_b, aff),
    }

    def _fake_cached(gz_resolved_posix: str) -> tuple[np.ndarray, tuple[float, ...]]:
        return tile_elev[gz_resolved_posix]

    monkeypatch.setattr(dem_peak, "_cached_skadi_elev_affine", _fake_cached)

    search = box(-115.0, 39.0, -114.0, 40.0)
    serial = skadi_binned_peaks_in_polygon(search, mirror, bin_size_m=1500.0, max_workers=1)
    parallel = skadi_binned_peaks_in_polygon(search, mirror, bin_size_m=1500.0, max_workers=4)

    assert serial == parallel
    assert serial
