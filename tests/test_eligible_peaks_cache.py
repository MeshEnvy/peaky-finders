"""Eligible peaks cache for site suggest."""

from __future__ import annotations

from pathlib import Path

import pytest
from shapely.geometry import box

from peaky_finders.site_suggestions.eligible_peaks_cache import (
    eligible_peaks_cache_digest,
    load_or_build_eligible_peaks,
    read_eligible_peaks_cache,
    write_eligible_peaks_cache,
)


def test_eligible_peaks_cache_roundtrip(tmp_path: Path) -> None:
    eligible = box(-115.0, 39.0, -114.0, 40.0)
    digest = eligible_peaks_cache_digest(
        eligible_sha="abc123",
        bin_size_m=1500.0,
        eligible_ll=eligible,
    )
    cache_path = tmp_path / "eligible_peaks" / f"{digest}.json"
    peaks = [(-115.5, 39.5, 3000.0), (-114.5, 39.5, 2500.0)]
    write_eligible_peaks_cache(
        cache_path,
        digest=digest,
        eligible_sha="abc123",
        bin_size_m=1500.0,
        eligible_ll=eligible,
        peaks_llz=peaks,
    )
    loaded = read_eligible_peaks_cache(cache_path)
    assert loaded == peaks


def test_load_or_build_eligible_peaks_uses_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    eligible = box(-115.0, 39.0, -114.0, 40.0)
    digest = eligible_peaks_cache_digest(
        eligible_sha="abc123",
        bin_size_m=1500.0,
        eligible_ll=eligible,
    )
    cache_path = tmp_path / "suggest" / "eligible_peaks" / f"{digest}.json"
    peaks = [(-115.5, 39.5, 3000.0)]
    write_eligible_peaks_cache(
        cache_path,
        digest=digest,
        eligible_sha="abc123",
        bin_size_m=1500.0,
        eligible_ll=eligible,
        peaks_llz=peaks,
    )

    def _fail_scan(*args, **kwargs):
        raise AssertionError("skadi scan should not run on cache hit")

    monkeypatch.setattr(
        "peaky_finders.site_suggestions.eligible_peaks_cache.skadi_binned_peaks_in_polygon",
        _fail_scan,
    )

    loaded, from_cache = load_or_build_eligible_peaks(
        suggest_root=tmp_path / "suggest",
        eligible_sha="abc123",
        eligible_ll=eligible,
        dem_mirror_root=tmp_path / "dem",
        bin_size_m=1500.0,
    )
    assert from_cache is True
    assert loaded == peaks
