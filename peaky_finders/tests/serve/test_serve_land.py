"""Land GDB overlay registry and serve API."""

from __future__ import annotations

import json
from http.client import HTTPConnection
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Polygon

from peaky_finders.core.preset import LandLayerEntry, LandLayerStyle, load_preset, read_preset_document
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
    ensure_layer_preview_geojson,
    gdf_to_feature_collection_geojson,
    list_data_gdbs,
    list_field_values,
    list_gdb_layers,
    list_layer_fields,
    ogr_where_for_land_layer,
    resolve_land_gdb_path,
    resolved_land_preview_cache_dir,
)
from test_serve_cli import _start_server


def _write_test_gdb(
    project_dir: Path,
    *,
    layer: str = "poly",
    with_agency: bool = False,
) -> str:
    data_dir = project_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    gdb_path = data_dir / "test-parcel.gdb"
    if with_agency:
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
    else:
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
    assert geojson["features"][0]["properties"]["style_key"] == "__default__"


def test_gdf_to_feature_collection_geojson_style_and_label_fields() -> None:
    gdf = gpd.GeoDataFrame(
        {"NAME": ["BLM"], "ABBR": ["BLM"]},
        geometry=[Polygon([(-119.5, 39.5), (-119.4, 39.5), (-119.4, 39.6), (-119.5, 39.6)])],
        crs="EPSG:4326",
    )
    geojson = gdf_to_feature_collection_geojson(gdf, label_field="NAME", style_field="ABBR")
    props = geojson["features"][0]["properties"]
    assert props["label"] == "BLM"
    assert props["style_key"] == "BLM"


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


def test_list_layer_fields_and_values(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo"
    project_dir.mkdir()
    rel = _write_test_gdb(project_dir, with_agency=True)
    gdb_path = resolve_land_gdb_path(project_dir, rel)
    fields = list_layer_fields(gdb_path, "poly")
    field_names = [row["name"] for row in fields["fields"]]
    assert "ABBR" in field_names
    assert fields["rowCount"] == 2
    values = list_field_values(gdb_path, "poly", "ABBR")
    assert {row["value"] for row in values["values"]} == {"BLM", "PVT"}


def test_ogr_where_for_land_layer_filters(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo"
    project_dir.mkdir()
    rel = _write_test_gdb(project_dir, with_agency=True)
    gdb_path = resolve_land_gdb_path(project_dir, rel)
    entry = LandLayerEntry(
        name="poly",
        include=[{"field": "ABBR", "values": ["BLM"]}],
        label_field="NAME",
        style_field="ABBR",
    )
    where = ogr_where_for_land_layer(entry)
    assert where is not None
    gdf = gpd.read_file(gdb_path, layer="poly", where=where)
    assert len(gdf) == 1
    assert gdf.iloc[0]["ABBR"] == "BLM"


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
        layers=[LandLayerEntry(name="poly")],
        label="Test parcels",
    )
    assert source["id"] == "test-parcel"
    assert source["layers"][0]["name"] == "poly"
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
        layers=[
            LandLayerEntry(
                name="poly",
                style=LandLayerStyle(color="#ff5500", opacity=0.35),
            )
        ],
    )
    assert source["layers"][0]["style"] == {"color": "#ff5500", "opacity": 0.35}

    updated = patch_land_source(
        preset_path,
        "test-parcel",
        layers=[
            LandLayerEntry(
                name="poly",
                style=LandLayerStyle(color="#4a6cf7", opacity=0.48),
            )
        ],
    )
    assert updated["layers"][0]["style"] == {"color": "#4a6cf7", "opacity": 0.48}

    doc = read_preset_document(preset_path)
    assert doc["land"]["sources"]["test-parcel"]["layers"][0]["style"]["color"] == "#4a6cf7"


def test_ensure_layer_geojson_caches_with_filter(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    scaffold_project("demo", parent=projects_dir)
    project_dir = projects_dir / "demo"
    preset_path = project_dir / "config.yaml"
    rel = _write_test_gdb(project_dir, with_agency=True)
    add_land_source(
        preset_path,
        path=rel,
        layers=[
            LandLayerEntry(
                name="poly",
                include=[{"field": "ABBR", "values": ["BLM"]}],
                style_field="ABBR",
                style={"BLM": LandLayerStyle(color="#f4a261", opacity=0.55)},
            )
        ],
    )

    path1, bbox1 = ensure_layer_geojson(preset_path, "test-parcel", "poly")
    path2, bbox2 = ensure_layer_geojson(preset_path, "test-parcel", "poly")
    assert path1 == path2
    payload = json.loads(read_layer_geojson_bytes(preset_path, "test-parcel", "poly"))
    assert len(payload["features"]) == 1
    assert payload["features"][0]["properties"]["style_key"] == "BLM"

    patch_land_source(
        preset_path,
        "test-parcel",
        layers=[
            LandLayerEntry(
                name="poly",
                include=[{"field": "ABBR", "values": ["PVT"]}],
                style_field="ABBR",
            )
        ],
    )
    path3, _ = ensure_layer_geojson(preset_path, "test-parcel", "poly")
    payload2 = json.loads(read_layer_geojson_bytes(preset_path, "test-parcel", "poly"))
    assert len(payload2["features"]) == 1
    assert payload2["features"][0]["properties"]["style_key"] == "PVT"
    assert payload2["features"][0]["properties"]["style_key"] != payload["features"][0]["properties"]["style_key"]


def test_ensure_layer_preview_geojson_caches(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo"
    project_dir.mkdir()
    rel = _write_test_gdb(project_dir)
    gdb_path = resolve_land_gdb_path(project_dir, rel)
    cache_root = resolved_land_preview_cache_dir(project_dir)

    geojson1 = ensure_layer_preview_geojson(project_dir, gdb_path, "poly")
    geojson2 = ensure_layer_preview_geojson(project_dir, gdb_path, "poly")
    assert geojson1 == geojson2
    assert len(geojson1["features"]) == 2
    assert list(cache_root.rglob("*.geojson"))


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
        assert payload["layers"][0]["name"] == "poly"
    finally:
        server.shutdown()
        server.server_close()
    assert preset_path.read_text() == before


def test_land_fields_and_values_endpoints(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    scaffold_project("mesh-demo", parent=projects_dir)
    project_dir = projects_dir / "mesh-demo"
    rel = _write_test_gdb(project_dir, with_agency=True)

    server, host, port, _thread = _start_server(projects_dir)
    try:
        conn = HTTPConnection(host, port, timeout=5)
        conn.request(
            "GET",
            f"/api/p/mesh-demo/land/import/preview/fields?path={rel}&layer=poly",
        )
        resp = conn.getresponse()
        payload = json.loads(resp.read().decode("utf-8"))
        assert resp.status == 200
        assert any(row["name"] == "ABBR" for row in payload["fields"])

        conn = HTTPConnection(host, port, timeout=5)
        conn.request(
            "GET",
            f"/api/p/mesh-demo/land/import/preview/values?path={rel}&layer=poly&field=ABBR",
        )
        resp = conn.getresponse()
        payload = json.loads(resp.read().decode("utf-8"))
        assert resp.status == 200
        assert {row["value"] for row in payload["values"]} == {"BLM", "PVT"}
    finally:
        server.shutdown()
        server.server_close()


def test_land_import_and_geojson_endpoint(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    scaffold_project("mesh-demo", parent=projects_dir)
    project_dir = projects_dir / "mesh-demo"
    rel = _write_test_gdb(project_dir, with_agency=True)

    server, host, port, _thread = _start_server(projects_dir)
    try:
        body = json.dumps(
            {
                "path": rel,
                "layers": [
                    {
                        "name": "poly",
                        "include": [{"field": "ABBR", "values": ["BLM", "PVT"]}],
                        "labelField": "NAME",
                        "styleField": "ABBR",
                        "style": {
                            "BLM": {"color": "#f4a261", "opacity": 0.55},
                            "PVT": {"color": "#6c757d", "opacity": 0.55},
                        },
                    }
                ],
                "label": "Parcels",
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
        assert payload["source"]["layers"][0]["styleField"] == "ABBR"

        conn = HTTPConnection(host, port, timeout=5)
        conn.request("GET", "/api/p/mesh-demo/land/sources/test-parcel/layers/poly/geojson")
        resp = conn.getresponse()
        assert resp.status == 200
        geojson = json.loads(resp.read().decode("utf-8"))
        style_keys = {feat["properties"]["style_key"] for feat in geojson["features"]}
        assert style_keys == {"BLM", "PVT"}
    finally:
        server.shutdown()
        server.server_close()


def test_land_patch_and_delete(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    scaffold_project("mesh-demo", parent=projects_dir)
    project_dir = projects_dir / "mesh-demo"
    rel = _write_test_gdb(project_dir)
    preset_path = project_dir / "config.yaml"
    add_land_source(preset_path, path=rel, layers=[LandLayerEntry(name="poly")])

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
