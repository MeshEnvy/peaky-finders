"""Land GDB overlay registry and serve API."""

from __future__ import annotations

import json
from http.client import HTTPConnection
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Polygon

from peaky_finders.core.project.scaffold import scaffold_project
from peaky_finders.serve.land import (
    add_land_source,
    delete_land_source,
    ensure_layer_geojson,
    list_land_payload,
    patch_land_source,
    read_layer_geojson_bytes,
)
from peaky_finders.serve.land_import import (
    gdf_to_feature_collection_geojson,
    list_data_gdbs,
    list_gdb_layers,
    resolve_land_gdb_path,
)
from peaky_finders.core.preset import LandLayerStyle, load_preset, read_preset_document
from test_serve_cli import _start_server


def _write_test_gdb(project_dir: Path, *, layer: str = "poly") -> str:
    data_dir = project_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    gdb_path = data_dir / "test-parcel.gdb"
    gdf = gpd.GeoDataFrame(
        {"name": ["alpha", "beta"]},
        geometry=[
            Polygon([(-119.5, 39.5), (-119.4, 39.5), (-119.4, 39.6), (-119.5, 39.6)]),
            Polygon([(-119.3, 39.6), (-119.2, 39.6), (-119.2, 39.7), (-119.3, 39.7)]),
        ],
        crs="EPSG:4326",
    )
    gdf.to_file(gdb_path, layer=layer, driver="OpenFileGDB")
    return "data/test-parcel.gdb"


def test_gdf_to_feature_collection_geojson_strips_non_json_attrs() -> None:
    gdf = gpd.GeoDataFrame(
        {"created": [pd.Timestamp("2020-01-01")]},
        geometry=[Polygon([(-119.5, 39.5), (-119.4, 39.5), (-119.4, 39.6), (-119.5, 39.6)])],
        crs="EPSG:4326",
    )
    geojson = gdf_to_feature_collection_geojson(gdf)
    assert geojson["type"] == "FeatureCollection"
    assert len(geojson["features"]) == 1


def test_list_data_gdbs_and_layers(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo"
    project_dir.mkdir()
    rel = _write_test_gdb(project_dir)
    paths = list_data_gdbs(project_dir)
    assert rel in paths
    gdb_path = resolve_land_gdb_path(project_dir, rel)
    layers = list_gdb_layers(gdb_path)
    assert len(layers) == 1
    assert layers[0].name == "poly"
    assert layers[0].count == 2


def test_resolve_land_gdb_path_rejects_outside_data(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo"
    project_dir.mkdir()
    _write_test_gdb(project_dir)
    outside = project_dir / "outside.gdb"
    outside.mkdir()
    with pytest.raises(ValueError, match="under data/"):
        resolve_land_gdb_path(project_dir, "outside.gdb")
    with pytest.raises(ValueError, match="\\.gdb"):
        resolve_land_gdb_path(project_dir, "data/not-a-gdb")


def test_add_patch_delete_land_source(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    scaffold_project("demo", parent=projects_dir)
    project_dir = projects_dir / "demo"
    preset_path = project_dir / "config.yaml"
    rel = _write_test_gdb(project_dir)

    source = add_land_source(
        preset_path,
        path=rel,
        layers=["poly"],
        label="Test parcels",
    )
    assert source["id"] == "test-parcel"
    assert source["label"] == "Test parcels"
    preset = load_preset(preset_path)
    assert "test-parcel" in preset.land.sources

    updated = patch_land_source(
        preset_path,
        "test-parcel",
        label="Renamed",
    )
    assert updated["label"] == "Renamed"

    delete_land_source(preset_path, "test-parcel")
    preset = load_preset(preset_path)
    assert preset.land.sources == {}


def test_land_layer_styles_round_trip(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    scaffold_project("demo", parent=projects_dir)
    project_dir = projects_dir / "demo"
    preset_path = project_dir / "config.yaml"
    rel = _write_test_gdb(project_dir)

    source = add_land_source(
        preset_path,
        path=rel,
        layers=["poly"],
        layer_styles={"poly": LandLayerStyle(color="#ff5500", opacity=0.35)},
    )
    assert source["layerStyles"]["poly"] == {"color": "#ff5500", "opacity": 0.35}

    updated = patch_land_source(
        preset_path,
        "test-parcel",
        layer_styles={"poly": LandLayerStyle(color="#4a6cf7", opacity=0.48)},
    )
    assert updated["layerStyles"]["poly"] == {"color": "#4a6cf7", "opacity": 0.48}

    doc = read_preset_document(preset_path)
    assert doc["land"]["sources"]["test-parcel"]["layer_styles"]["poly"]["color"] == "#4a6cf7"


def test_ensure_layer_geojson_caches(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    scaffold_project("demo", parent=projects_dir)
    project_dir = projects_dir / "demo"
    preset_path = project_dir / "config.yaml"
    rel = _write_test_gdb(project_dir)
    add_land_source(preset_path, path=rel, layers=["poly"])

    path1, bbox1 = ensure_layer_geojson(preset_path, "test-parcel", "poly")
    path2, bbox2 = ensure_layer_geojson(preset_path, "test-parcel", "poly")
    assert path1 == path2
    assert path1.is_file()
    assert bbox1 == bbox2
    payload = json.loads(read_layer_geojson_bytes(preset_path, "test-parcel", "poly"))
    assert payload["type"] == "FeatureCollection"
    assert len(payload["features"]) == 2


def test_land_import_preview_does_not_write_yaml(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    scaffold_project("mesh-demo", parent=projects_dir)
    project_dir = projects_dir / "mesh-demo"
    rel = _write_test_gdb(project_dir)
    preset_path = project_dir / "config.yaml"
    before = preset_path.read_text()

    server, host, port, _thread = _start_server(projects_dir)
    try:
        body = json.dumps({"path": rel}).encode("utf-8")
        conn = HTTPConnection(host, port, timeout=5)
        conn.request(
            "POST",
            "/api/p/mesh-demo/land/import/preview",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        payload = json.loads(resp.read().decode("utf-8"))
        assert resp.status == 200
        assert payload["path"] == rel
        assert len(payload["layers"]) == 1
        assert payload["layers"][0]["name"] == "poly"
    finally:
        server.shutdown()
        server.server_close()
    assert preset_path.read_text() == before


def test_land_import_and_geojson_endpoint(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    scaffold_project("mesh-demo", parent=projects_dir)
    project_dir = projects_dir / "mesh-demo"
    rel = _write_test_gdb(project_dir)

    server, host, port, _thread = _start_server(projects_dir)
    try:
        body = json.dumps(
            {
                "path": rel,
                "layers": ["poly"],
                "label": "Parcels",
                "layer_styles": {"poly": {"color": "#4a6cf7", "opacity": 0.48}},
            }
        ).encode("utf-8")
        conn = HTTPConnection(host, port, timeout=5)
        conn.request(
            "POST",
            "/api/p/mesh-demo/land/import",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        payload = json.loads(resp.read().decode("utf-8"))
        assert resp.status == 201
        assert payload["source"]["id"] == "test-parcel"

        conn = HTTPConnection(host, port, timeout=5)
        conn.request("GET", "/api/p/mesh-demo/land")
        resp = conn.getresponse()
        land_payload = json.loads(resp.read().decode("utf-8"))
        assert len(land_payload["sources"]) == 1

        conn = HTTPConnection(host, port, timeout=5)
        conn.request("GET", "/api/p/mesh-demo/land/sources/test-parcel/layers/poly/geojson")
        resp = conn.getresponse()
        assert resp.status == 200
        geojson = json.loads(resp.read().decode("utf-8"))
        assert geojson["type"] == "FeatureCollection"
    finally:
        server.shutdown()
        server.server_close()

    doc = read_preset_document(projects_dir / "mesh-demo" / "config.yaml")
    assert doc["land"]["sources"]["test-parcel"]["layers"] == ["poly"]


def test_land_patch_and_delete(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    scaffold_project("mesh-demo", parent=projects_dir)
    project_dir = projects_dir / "mesh-demo"
    rel = _write_test_gdb(project_dir)
    preset_path = project_dir / "config.yaml"
    add_land_source(preset_path, path=rel, layers=["poly"])

    server, host, port, _thread = _start_server(projects_dir)
    try:
        body = json.dumps({"label": "Updated label"}).encode("utf-8")
        conn = HTTPConnection(host, port, timeout=5)
        conn.request(
            "PATCH",
            "/api/p/mesh-demo/land/sources/test-parcel",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        payload = json.loads(resp.read().decode("utf-8"))
        assert resp.status == 200
        assert payload["source"]["label"] == "Updated label"

        conn = HTTPConnection(host, port, timeout=5)
        conn.request("DELETE", "/api/p/mesh-demo/land/sources/test-parcel")
        resp = conn.getresponse()
        assert resp.status == 200
        assert list_land_payload(preset_path)["sources"] == []
    finally:
        server.shutdown()
        server.server_close()


def test_land_import_rejects_invalid_path(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    scaffold_project("mesh-demo", parent=projects_dir)

    server, host, port, _thread = _start_server(projects_dir)
    try:
        body = json.dumps({"path": "config.yaml", "layers": ["poly"]}).encode("utf-8")
        conn = HTTPConnection(host, port, timeout=5)
        conn.request(
            "POST",
            "/api/p/mesh-demo/land/import",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        payload = json.loads(resp.read().decode("utf-8"))
        assert resp.status == 422
        assert "error" in payload
    finally:
        server.shutdown()
        server.server_close()
