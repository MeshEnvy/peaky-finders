"""Web chat DEM query helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import numpy as np
from rasterio.transform import from_bounds
from shapely.geometry import box

from peaky_finders import pairwise_dem_peak as dem_peak
from peaky_finders.web.chat_dem import clear_dem_highest_cache, query_project_dem_highest, snap_peak_to_local_dem


def test_query_project_dem_highest_uses_cache() -> None:
    clear_dem_highest_cache()
    cached = {"project": "nevada", "lat": 1.0, "lon": 2.0, "elev_m": 100.0}
    with patch(
        "peaky_finders.web.chat_dem._dem_highest_by_project",
        {"nevada": cached},
    ):
        result = query_project_dem_highest(project_slug="nevada")
    assert result["cached"] is True
    assert result["lat"] == 1.0


def test_query_project_dem_highest_returns_peak() -> None:
    clear_dem_highest_cache()
    fake_geom = box(-120, 35, -114, 42)
    with patch(
        "peaky_finders.web.chat_dem.peaky_projects_dir",
    ) as root_fn, patch(
        "peaky_finders.web.chat_dem.load_preset",
    ) as load_preset, patch(
        "peaky_finders.web.chat_dem.resolved_project_dem_dir",
        return_value=Path("/dem"),
    ), patch(
        "peaky_finders.web.chat_dem.project_dem_search_geometry",
        return_value=(fake_geom, "project bundle AOI"),
    ), patch(
        "peaky_finders.web.chat_dem.global_max_skadi_elevation_in_polygon",
        return_value=(-118.325, 37.846, 4005.0),
    ), patch(
        "peaky_finders.web.chat_dem.reverse_geocode_label",
        return_value="Boundary Peak",
    ):
        root_fn.return_value = Path("/projects")
        preset_path = Path("/projects/nevada/config.yaml")
        with patch.object(Path, "is_file", return_value=True):
            load_preset.return_value = object()
            result = query_project_dem_highest(project_slug="nevada")

    assert result["elev_m"] == 4005.0
    assert result["place_label"] == "Boundary Peak"
    assert result["scope"] == "project bundle AOI"
