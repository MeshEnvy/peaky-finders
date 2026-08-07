"""Point-in-polygon land layer queries."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import Polygon

from peaky_finders.core.preset import LandAttributeFilter, LandLayerEntry, load_preset
from peaky_finders.core.project.scaffold import scaffold_project
from peaky_finders.serve.land import add_land_source
from peaky_finders.serve.land_query import (
    load_land_layer_index,
    point_hits,
    query_land_layer_index,
    resolve_land_layer_entry,
)


def _write_agency_gdb(project_dir: Path) -> str:
    data_dir = project_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    rel = "data/test-sma.gdb"
    gdf = gpd.GeoDataFrame(
        {
            "NAME": ["Bureau of Land Management", "Private"],
            "ABBR": ["BLM", "PVT"],
        },
        geometry=[
            Polygon([(-119.5, 39.5), (-119.4, 39.5), (-119.4, 39.6), (-119.5, 39.6)]),
            Polygon([(-119.3, 39.6), (-119.2, 39.6), (-119.2, 39.7), (-119.3, 39.7)]),
        ],
        crs="EPSG:4326",
    )
    gdf.to_file(project_dir / rel, driver="OpenFileGDB", layer="Land_Status_Dis")
    return rel


def _write_fo_geojson(project_dir: Path) -> str:
    data_dir = project_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    rel = "data/test-fo.geojson"
    gdf = gpd.GeoDataFrame(
        {"ADMU_NAME": ["Sierra Front Field Office", "Humboldt River Field Office"]},
        geometry=[
            Polygon([(-119.5, 39.5), (-119.45, 39.5), (-119.45, 39.55), (-119.5, 39.55)]),
            Polygon([(-119.3, 39.6), (-119.2, 39.6), (-119.2, 39.7), (-119.3, 39.7)]),
        ],
        crs="EPSG:4326",
    )
    gdf.to_file(project_dir / rel, driver="GeoJSON")
    return rel


@pytest.fixture
def land_query_project(tmp_path: Path) -> Path:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    scaffold_project("demo", parent=projects_dir)
    project_dir = projects_dir / "demo"
    sma_path = _write_agency_gdb(project_dir)
    fo_path = _write_fo_geojson(project_dir)
    preset_path = project_dir / "config.yaml"

    add_land_source(
        preset_path,
        source_id="test-sma",
        path=sma_path,
        layers=[
            LandLayerEntry(
                name="Land_Status_Dis",
                include=[LandAttributeFilter(field="ABBR", values=["BLM"])],
            )
        ],
    )
    add_land_source(
        preset_path,
        source_id="test-fo",
        path=fo_path,
        layers=[LandLayerEntry(name="test-fo")],
    )
    return preset_path


def test_point_hits_blm_land(land_query_project: Path) -> None:
    hits = point_hits(land_query_project, 39.55, -119.47, "test-sma", "Land_Status_Dis")
    assert len(hits) == 1
    assert hits[0]["ABBR"] == "BLM"


def test_point_hits_private_excluded_by_layer_filter(land_query_project: Path) -> None:
    hits = point_hits(land_query_project, 39.65, -119.25, "test-sma", "Land_Status_Dis")
    assert hits == []


def test_point_hits_blm_with_entry_override(land_query_project: Path) -> None:
    all_agencies = LandLayerEntry(
        name="Land_Status_Dis",
        include=[LandAttributeFilter(field="ABBR", values=["BLM", "PVT"])],
    )
    hits = point_hits(
        land_query_project,
        39.65,
        -119.25,
        "test-sma",
        entry=all_agencies,
    )
    assert len(hits) == 1
    assert hits[0]["ABBR"] == "PVT"


def test_point_hits_field_office_smallest_only(land_query_project: Path) -> None:
    index = load_land_layer_index(land_query_project, "test-fo", "test-fo")
    hits = query_land_layer_index(index, 39.52, -119.48, smallest_only=True)
    assert len(hits) == 1
    assert hits[0].properties["ADMU_NAME"] == "Sierra Front Field Office"


def test_resolve_land_layer_entry_single_layer(land_query_project: Path) -> None:
    preset = load_preset(land_query_project)
    entry = resolve_land_layer_entry(preset, "test-fo")
    assert entry.name == "test-fo"
