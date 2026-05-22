"""PLSS/MLRS ``by_loc.json`` keyed by site coordinates."""

from __future__ import annotations

from pathlib import Path

from peaky_finders.plss_mlrs_fetch import loc_stamp, read_plss_mlrs_loc_cache, write_plss_mlrs_loc_cache


def test_loc_stamp_stable() -> None:
    assert loc_stamp(39.0, -117.0) == "39.000000,-117.000000"


def test_loc_cache_roundtrip(tmp_path: Path) -> None:
    d = {
        "39.000000,-117.000000": {"plss": "NV; Sec. 1", "mlrs": "NV123"},
        "40.000000,-118.000000": {"plss": "NV; Sec. 2", "mlrs": "NV456"},
    }
    write_plss_mlrs_loc_cache(tmp_path, d.copy())
    assert read_plss_mlrs_loc_cache(tmp_path) == d
