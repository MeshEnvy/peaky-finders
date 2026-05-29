"""Site metadata backfill (PLSS, MLRS, Skadi elevation)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import yaml

from peaky_finders.site_metadata_enrich import (
    enrich_all_preset_sites,
    enrich_site_entry_metadata,
    fill_missing_site_metadata,
    populate_preset_missing_metadata,
    resolve_site_metadata,
)
from peaky_finders.sites_job import load_preset


def test_enrich_site_entry_fills_missing_fields() -> None:
    ent = {"name": "Goal", "type": "goal", "loc": [39.0, -117.0]}
    loc_cache = {
        "39.000000,-117.000000": {"plss": "NV; Sec. 1", "mlrs": "NV123"},
    }

    with patch(
        "peaky_finders.site_metadata_enrich.skadi_elevation_at_point",
        return_value=1842.5,
    ):
        changed, from_network = enrich_site_entry_metadata(
            ent,
            loc_cache=loc_cache,
            dem_dir=Path("/tmp/dem"),
            allow_network_plss=False,
        )

    assert changed is True
    assert from_network is False
    assert ent["plss"] == "NV; Sec. 1"
    assert ent["mlrs"] == "NV123"
    assert ent["elevation_m"] == 1842.5


def test_enrich_site_entry_skips_existing_fields() -> None:
    ent = {
        "name": "Goal",
        "type": "goal",
        "loc": [39.0, -117.0],
        "plss": "keep",
        "mlrs": "keep-mlrs",
        "elevation_m": 1200.0,
    }

    changed, from_network = enrich_site_entry_metadata(
        ent,
        loc_cache={},
        dem_dir=Path("/tmp/dem"),
        allow_network_plss=True,
    )

    assert changed is False
    assert from_network is False
    assert ent["plss"] == "keep"
    assert ent["elevation_m"] == 1200.0


def test_enrich_site_entry_force_overwrites_existing() -> None:
    ent = {
        "name": "Goal",
        "type": "goal",
        "loc": [39.0, -117.0],
        "plss": "old",
        "mlrs": "old-mlrs",
        "elevation_m": 900.0,
    }
    loc_cache = {
        "39.000000,-117.000000": {"plss": "NV; Sec. 1", "mlrs": "NV123"},
    }

    with patch(
        "peaky_finders.site_metadata_enrich.skadi_elevation_at_point",
        return_value=1842.5,
    ):
        changed, from_network = enrich_site_entry_metadata(
            ent,
            loc_cache=loc_cache,
            dem_dir=Path("/tmp/dem"),
            allow_network_plss=False,
            force=True,
        )

    assert changed is True
    assert from_network is False
    assert ent["plss"] == "NV; Sec. 1"
    assert ent["mlrs"] == "NV123"
    assert ent["elevation_m"] == 1842.5


def test_fill_missing_site_metadata_persists(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {
                "simulation": {
                    "provider": "los",
                    "radius_km": 10.0,
                    "modem_presets": {
                        "meshcore-us": {
                            "frequency_mhz": 910.525,
                            "bandwidth_khz": 62.5,
                            "spreading_factor": 7,
                            "coding_rate": 5,
                            "implementation_margin_db": 3.0,
                            "power_dbm": 22.0,
                            "sensitivity_dbm": -121.0,
                        }
                    },
                    "environment_presets": {
                        "test-desert": {
                            "climate": "desert",
                            "polarization": "vertical",
                            "clutter_height_m": 1.0,
                        }
                    },
                    "modem": "meshcore-us",
                    "environment": "test-desert",
                    "transmitter": {"height_m": 2.0, "gain_dbi": 3.0, "loss_db": 2.0},
                    "receiver": {"height_m": 2.0, "gain_dbi": 3.0, "loss_db": 2.0},
                },
                "display": {"colormap": "plasma", "min_dbm": -130.0, "max_dbm": -80.0},
                "sites": {
                    "goal-town": {
                        "type": "goal",
                        "name": "Goal Town",
                        "loc": [39.0, -117.0],
                    }
                },
            }
        )
    )

    with patch(
        "peaky_finders.site_metadata_enrich.lookup_plss_mlrs_for_loc",
        return_value=("NV; Sec. 9", "FRST123", True),
    ), patch(
        "peaky_finders.site_metadata_enrich.skadi_elevation_at_point",
        return_value=1500.0,
    ), patch(
        "peaky_finders.site_metadata_enrich.resolved_dem_mirror_for_preset",
        return_value=tmp_path / "dem",
    ), patch(
        "peaky_finders.site_metadata_enrich.read_plss_mlrs_loc_cache",
        return_value={},
    ), patch(
        "peaky_finders.site_metadata_enrich.write_plss_mlrs_loc_cache",
    ):
        assert fill_missing_site_metadata(cfg, "goal-town", allow_network_plss=True) is True

    preset = load_preset(cfg)
    ent = preset.sites["goal-town"]
    assert ent.plss == "NV; Sec. 9"
    assert ent.mlrs == "FRST123"
    assert ent.elevation_m == 1500.0


def test_enrich_all_preset_sites_writes_yaml_once(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {
                "simulation": {
                    "provider": "los",
                    "radius_km": 10.0,
                    "modem_presets": {
                        "meshcore-us": {
                            "frequency_mhz": 910.525,
                            "bandwidth_khz": 62.5,
                            "spreading_factor": 7,
                            "coding_rate": 5,
                            "implementation_margin_db": 3.0,
                            "power_dbm": 22.0,
                            "sensitivity_dbm": -121.0,
                        }
                    },
                    "environment_presets": {
                        "test-desert": {
                            "climate": "desert",
                            "polarization": "vertical",
                            "clutter_height_m": 1.0,
                        }
                    },
                    "modem": "meshcore-us",
                    "environment": "test-desert",
                    "transmitter": {"height_m": 2.0, "gain_dbi": 3.0, "loss_db": 2.0},
                    "receiver": {"height_m": 2.0, "gain_dbi": 3.0, "loss_db": 2.0},
                },
                "display": {"colormap": "plasma", "min_dbm": -130.0, "max_dbm": -80.0},
                "sites": {
                    "a": {"name": "A", "loc": [39.0, -117.0]},
                    "b": {"name": "B", "loc": [40.0, -118.0]},
                },
            }
        )
    )

    with patch(
        "peaky_finders.site_metadata_enrich.lookup_plss_mlrs_for_loc",
        side_effect=[
            ("NV; Sec. 1", "MLRS1", False),
            ("NV; Sec. 2", "MLRS2", False),
        ],
    ), patch(
        "peaky_finders.site_metadata_enrich.skadi_elevation_at_point",
        return_value=1200.0,
    ), patch(
        "peaky_finders.site_metadata_enrich.resolved_dem_mirror_for_preset",
        return_value=tmp_path / "dem",
    ), patch(
        "peaky_finders.site_metadata_enrich.read_plss_mlrs_loc_cache",
        return_value={},
    ), patch(
        "peaky_finders.site_metadata_enrich.write_plss_mlrs_loc_cache",
    ):
        network_count, updated = enrich_all_preset_sites(cfg, allow_network_plss=False)

    assert network_count == 0
    assert updated == 2
    preset = load_preset(cfg)
    assert preset.sites["a"].plss == "NV; Sec. 1"
    assert preset.sites["b"].elevation_m == 1200.0


def test_populate_preset_missing_metadata_batch(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {
                "simulation": {
                    "provider": "los",
                    "radius_km": 10.0,
                    "modem_presets": {
                        "meshcore-us": {
                            "frequency_mhz": 910.525,
                            "bandwidth_khz": 62.5,
                            "spreading_factor": 7,
                            "coding_rate": 5,
                            "implementation_margin_db": 3.0,
                            "power_dbm": 22.0,
                            "sensitivity_dbm": -121.0,
                        }
                    },
                    "environment_presets": {
                        "test-desert": {
                            "climate": "desert",
                            "polarization": "vertical",
                            "clutter_height_m": 1.0,
                        }
                    },
                    "modem": "meshcore-us",
                    "environment": "test-desert",
                    "transmitter": {"height_m": 2.0, "gain_dbi": 3.0, "loss_db": 2.0},
                    "receiver": {"height_m": 2.0, "gain_dbi": 3.0, "loss_db": 2.0},
                },
                "display": {"colormap": "plasma", "min_dbm": -130.0, "max_dbm": -80.0},
                    "sites": {
                        "a": {"name": "A", "loc": [39.0, -117.0]},
                        "b": {
                            "name": "B",
                            "loc": [40.0, -118.0],
                            "plss": "NV; Sec. 2",
                            "mlrs": "MLRS2",
                            "elevation_m": 900.0,
                        },
                    },
            }
        )
    )

    with patch(
        "peaky_finders.site_metadata_enrich.lookup_plss_mlrs_for_loc",
        side_effect=[
            ("NV; Sec. 1", "MLRS1", False),
        ],
    ), patch(
        "peaky_finders.site_metadata_enrich.skadi_elevation_at_point",
        return_value=1200.0,
    ), patch(
        "peaky_finders.site_metadata_enrich.resolved_dem_mirror_for_preset",
        return_value=tmp_path / "dem",
    ), patch(
        "peaky_finders.site_metadata_enrich.read_plss_mlrs_loc_cache",
        return_value={},
    ), patch(
        "peaky_finders.site_metadata_enrich.write_plss_mlrs_loc_cache",
    ):
        network_count, updated = populate_preset_missing_metadata(
            cfg,
            allow_network_plss=False,
        )

    assert network_count == 0
    assert updated == 1
    preset = load_preset(cfg)
    assert preset.sites["a"].elevation_m == 1200.0
    assert preset.sites["b"].elevation_m == 900.0
