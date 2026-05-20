"""PLSS/MLRS CadNSDI loc-keyed cache (independent of bundle AOI digest)."""

from __future__ import annotations

from pathlib import Path

from peaky_finders.plss_mlrs_fetch import (
    cadnsdi_slugs_needing_refresh,
    loc_stamp,
    read_plss_mlrs_loc_cache,
    write_plss_mlrs_loc_cache,
)
from peaky_finders.sites_job import Preset


def _tiny_preset() -> Preset:
    return Preset.model_validate(
        {
            "simulation": {
                "transmitter": {"height_m": 2.0, "gain_dbi": 0.0, "loss_db": 0.0},
                "receiver": {"height_m": 2.0, "gain_dbi": 0.0, "loss_db": 0.0},
            },
            "display": {},
            "sites": {
                "a": {"name": "A", "loc": [39.0, -117.0]},
                "b": {"name": "B", "loc": [40.0, -118.0]},
            },
        }
    )


def test_loc_stamp_stable() -> None:
    assert loc_stamp(39.0, -117.0) == "39.000000,-117.000000"


def test_cadnsdi_slugs_needing_refresh_new_loc() -> None:
    preset = _tiny_preset()
    need = cadnsdi_slugs_needing_refresh(preset, set(), force_all=False)
    assert need == {"a", "b"}


def test_cadnsdi_slugs_needing_refresh_cached_loc() -> None:
    preset = _tiny_preset()
    cached = {loc_stamp(preset.sites[s].lat, preset.sites[s].lon) for s in preset.sites}
    assert cadnsdi_slugs_needing_refresh(preset, cached, force_all=False) == set()


def test_cadnsdi_slugs_needing_refresh_moved_site() -> None:
    preset = _tiny_preset()
    cached = {loc_stamp(preset.sites["b"].lat, preset.sites["b"].lon)}
    assert cadnsdi_slugs_needing_refresh(preset, cached, force_all=False) == {"a"}


def test_cadnsdi_force_all() -> None:
    preset = _tiny_preset()
    cached = {loc_stamp(preset.sites[s].lat, preset.sites[s].lon) for s in preset.sites}
    assert cadnsdi_slugs_needing_refresh(preset, cached, force_all=True) == set(preset.sites.keys())


def test_loc_cache_roundtrip(tmp_path: Path) -> None:
    d = {
        "39.000000,-117.000000": {"plss": "NV; Sec. 1", "mlrs": "NV123"},
        "40.000000,-118.000000": {"plss": "NV; Sec. 2", "mlrs": "NV456"},
    }
    write_plss_mlrs_loc_cache(tmp_path, d.copy())
    assert read_plss_mlrs_loc_cache(tmp_path) == d
