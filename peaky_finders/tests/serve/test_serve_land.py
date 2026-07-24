"""Land GDB overlay registry and serve API."""

from __future__ import annotations

import json
from http.client import HTTPConnection
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Polygon

from peaky_finders.core.preset import (
    LandLayerEntry,
    LandLayerRole,
    LandLayerStyle,
    LandSidebar,
    LandSidebarFolder,
    load_preset,
    read_preset_document,
)
from peaky_finders.core.project.scaffold import scaffold_project
from peaky_finders.serve.land import (
    add_land_source,
    aoi_digest,
    delete_land_source,
    ensure_layer_geojson,
    list_land_payload,
    normalize_land_sidebar,
    patch_land_sidebar,
    patch_land_source,
    purge_clipped_serve_caches,
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


def _write_clip_test_gdbs(project_dir: Path) -> tuple[str, str]:
    data_dir = project_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    aoi_gdf = gpd.GeoDataFrame(
        geometry=[
            Polygon(
                [
                    (-119.45, 39.52),
                    (-119.42, 39.52),
                    (-119.42, 39.55),
                    (-119.45, 39.55),
                ]
            )
        ],
        crs="EPSG:4326",
    )
    big_gdf = gpd.GeoDataFrame(
        geometry=[
            Polygon(
                [
                    (-119.5, 39.5),
                    (-119.3, 39.5),
                    (-119.3, 39.7),
                    (-119.5, 39.7),
                ]
            )
        ],
        crs="EPSG:4326",
    )
    aoi_path = data_dir / "aoi.gdb"
    big_path = data_dir / "big.gdb"
    aoi_gdf.to_file(aoi_path, layer="aoi", driver="OpenFileGDB")
    big_gdf.to_file(big_path, layer="big", driver="OpenFileGDB")
    return "data/aoi.gdb", "data/big.gdb"


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


def test_ogr_where_for_land_layer_exclude_filters(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo"
    project_dir.mkdir()
    rel = _write_test_gdb(project_dir, with_agency=True)
    gdb_path = resolve_land_gdb_path(project_dir, rel)
    entry = LandLayerEntry(
        name="poly",
        exclude=[{"field": "NAME", "values": ["Private"]}],
        label_field="NAME",
    )
    where = ogr_where_for_land_layer(entry)
    assert where is not None
    assert "Private" in where
    gdf = gpd.read_file(gdb_path, layer="poly", where=where)
    assert len(gdf) == 1
    assert gdf.iloc[0]["NAME"] == "Bureau of Land Management"


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
    assert preset.land.sidebar is not None
    assert preset.land.sidebar.unfiled_sources == ["test-parcel"]

    updated = patch_land_source(
        preset_path,
        "test-parcel",
        label="Renamed",
    )
    assert updated["label"] == "Renamed"

    delete_land_source(preset_path, "test-parcel")
    preset = load_preset(preset_path)
    assert preset.land.sources == {}
    assert preset.land.sidebar is None or preset.land.sidebar.unfiled_sources == []


def test_normalize_land_sidebar_prunes_and_appends(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    scaffold_project("demo", parent=projects_dir)
    project_dir = projects_dir / "demo"
    preset_path = project_dir / "config.yaml"
    rel = _write_test_gdb(project_dir)
    add_land_source(preset_path, path=rel, layers=[LandLayerEntry(name="poly")], source_id="parcel-a")
    add_land_source(
        preset_path,
        path=rel,
        layers=[LandLayerEntry(name="poly")],
        source_id="parcel-b",
    )

    preset = load_preset(preset_path)
    preset.land.sidebar = LandSidebar.model_construct(
        folders=[
            LandSidebarFolder.model_construct(
                id="federal",
                label="Federal",
                sources=["parcel-a", "stale-src"],
            ),
        ],
        unfiled_sources=["other-stale"],
    )
    sidebar = normalize_land_sidebar(preset)
    assert sidebar.folders[0].sources == ["parcel-a"]
    assert sidebar.unfiled_sources == ["parcel-b"]


def test_patch_land_sidebar_round_trip(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    scaffold_project("demo", parent=projects_dir)
    project_dir = projects_dir / "demo"
    preset_path = project_dir / "config.yaml"
    rel = _write_test_gdb(project_dir)
    add_land_source(preset_path, path=rel, layers=[LandLayerEntry(name="poly")], source_id="parcel-a")
    add_land_source(
        preset_path,
        path=rel,
        layers=[LandLayerEntry(name="poly")],
        source_id="parcel-b",
    )

    result = patch_land_sidebar(
        preset_path,
        LandSidebar(
            folders=[LandSidebarFolder(id="refs", label="Reference", sources=["parcel-b"])],
            unfiled_sources=["parcel-a"],
        ),
    )
    assert result["folders"][0]["sources"] == ["parcel-b"]
    assert result["unfiledSources"] == ["parcel-a"]

    doc = read_preset_document(preset_path)
    assert doc["land"]["sidebar"]["folders"][0]["id"] == "refs"
    assert doc["land"]["sidebar"]["unfiled_sources"] == ["parcel-a"]


def test_patch_land_sidebar_rejects_duplicate_source(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    scaffold_project("demo", parent=projects_dir)
    project_dir = projects_dir / "demo"
    preset_path = project_dir / "config.yaml"
    rel = _write_test_gdb(project_dir)
    add_land_source(preset_path, path=rel, layers=[LandLayerEntry(name="poly")], source_id="parcel-a")
    add_land_source(
        preset_path,
        path=rel,
        layers=[LandLayerEntry(name="poly")],
        source_id="parcel-b",
    )

    with pytest.raises(ValueError, match="duplicate land source"):
        patch_land_sidebar(
            preset_path,
            LandSidebar(
                folders=[LandSidebarFolder(id="refs", label="Reference", sources=["parcel-a"])],
                unfiled_sources=["parcel-a"],
            ),
        )


def test_list_land_payload_includes_sidebar(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    scaffold_project("demo", parent=projects_dir)
    project_dir = projects_dir / "demo"
    preset_path = project_dir / "config.yaml"
    rel = _write_test_gdb(project_dir)
    add_land_source(preset_path, path=rel, layers=[LandLayerEntry(name="poly")])

    payload = list_land_payload(preset_path)
    assert "sidebar" in payload
    assert payload["sidebar"]["unfiledSources"] == ["test-parcel"]


def test_land_sidebar_patch_http(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    scaffold_project("mesh-demo", parent=projects_dir)
    project_dir = projects_dir / "mesh-demo"
    preset_path = project_dir / "config.yaml"
    rel = _write_test_gdb(project_dir)
    add_land_source(preset_path, path=rel, layers=[LandLayerEntry(name="poly")], source_id="parcel-a")
    add_land_source(
        preset_path,
        path=rel,
        layers=[LandLayerEntry(name="poly")],
        source_id="parcel-b",
    )

    server, host, port, _thread = _start_server(projects_dir)
    try:
        body = json.dumps(
            {
                "folders": [
                    {"id": "refs", "label": "Reference", "sources": ["parcel-b"]},
                ],
                "unfiledSources": ["parcel-a"],
            }
        ).encode("utf-8")
        conn = HTTPConnection(host, port, timeout=5)
        conn.request(
            "PATCH",
            "/api/p/mesh-demo/land/sidebar",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        payload = json.loads(resp.read().decode("utf-8"))
        assert resp.status == 200
        assert payload["sidebar"]["folders"][0]["sources"] == ["parcel-b"]
        preset = load_preset(preset_path)
        assert preset.land.sidebar is not None
        assert preset.land.sidebar.folders[0].sources == ["parcel-b"]
    finally:
        server.shutdown()
        server.server_close()


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

    path1, bbox1, _digest1 = ensure_layer_geojson(preset_path, "test-parcel", "poly")
    path2, bbox2, _digest2 = ensure_layer_geojson(preset_path, "test-parcel", "poly")
    assert path1 == path2
    body, _digest = read_layer_geojson_bytes(preset_path, "test-parcel", "poly")
    payload = json.loads(body)
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
    path3, _, _digest3 = ensure_layer_geojson(preset_path, "test-parcel", "poly")
    body2, _ = read_layer_geojson_bytes(preset_path, "test-parcel", "poly")
    payload2 = json.loads(body2)
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


def test_land_import_excludes_private(tmp_path: Path) -> None:
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
                        "exclude": [{"field": "NAME", "values": ["Private"]}],
                        "labelField": "NAME",
                        "style": {"color": "#4a6cf7", "opacity": 0.48},
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
        assert payload["source"]["layers"][0]["exclude"][0]["values"] == ["Private"]

        conn = HTTPConnection(host, port, timeout=5)
        conn.request("GET", "/api/p/mesh-demo/land/sources/test-parcel/layers/poly/geojson")
        resp = conn.getresponse()
        assert resp.status == 200
        geojson = json.loads(resp.read().decode("utf-8"))
        labels = {feat["properties"].get("label") for feat in geojson["features"]}
        assert labels == {"Bureau of Land Management"}
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


def test_list_land_payload_includes_aoi_digest(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    scaffold_project("demo", parent=projects_dir)
    preset_path = projects_dir / "demo" / "config.yaml"
    payload = list_land_payload(preset_path)
    assert payload["aoiDigest"] == "none"


def test_aoi_clip_serve_unclipped_preview(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    scaffold_project("demo", parent=projects_dir)
    project_dir = projects_dir / "demo"
    preset_path = project_dir / "config.yaml"
    aoi_rel, big_rel = _write_clip_test_gdbs(project_dir)

    add_land_source(
        preset_path,
        path=aoi_rel,
        source_id="aoi-src",
        layers=[LandLayerEntry(name="aoi", role=LandLayerRole.AOI)],
    )
    add_land_source(
        preset_path,
        path=big_rel,
        source_id="big-src",
        layers=[LandLayerEntry(name="big")],
    )
    assert aoi_digest(preset_path) != "none"

    preview = ensure_layer_preview_geojson(
        project_dir,
        resolve_land_gdb_path(project_dir, big_rel),
        "big",
    )
    assert len(preview["features"]) == 1
    preview_bounds = gpd.GeoDataFrame.from_features(preview["features"], crs="EPSG:4326").total_bounds
    assert preview_bounds[0] < -119.45

    _path, bbox, _digest = ensure_layer_geojson(preset_path, "big-src", "big")
    assert bbox[0] >= -119.45 - 1e-6
    assert bbox[2] <= -119.42 + 1e-6


def test_aoi_change_purges_clipped_serve_cache(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    scaffold_project("demo", parent=projects_dir)
    project_dir = projects_dir / "demo"
    preset_path = project_dir / "config.yaml"
    aoi_rel, big_rel = _write_clip_test_gdbs(project_dir)

    add_land_source(
        preset_path,
        path=aoi_rel,
        source_id="aoi-src",
        layers=[LandLayerEntry(name="aoi", role=LandLayerRole.AOI)],
    )
    add_land_source(
        preset_path,
        path=big_rel,
        source_id="big-src",
        layers=[LandLayerEntry(name="big")],
    )
    cache_path, _, _ = ensure_layer_geojson(preset_path, "big-src", "big")
    assert cache_path.is_file()

    patch_land_source(
        preset_path,
        "aoi-src",
        layers=[LandLayerEntry(name="aoi")],
    )
    assert not cache_path.is_file()

    _path, bbox, _ = ensure_layer_geojson(preset_path, "big-src", "big")
    assert bbox[0] < -119.45


def test_purge_clipped_serve_caches_keeps_aoi_layers(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    scaffold_project("demo", parent=projects_dir)
    project_dir = projects_dir / "demo"
    preset_path = project_dir / "config.yaml"
    aoi_rel, big_rel = _write_clip_test_gdbs(project_dir)

    add_land_source(
        preset_path,
        path=aoi_rel,
        source_id="aoi-src",
        layers=[LandLayerEntry(name="aoi", role=LandLayerRole.AOI)],
    )
    add_land_source(
        preset_path,
        path=big_rel,
        source_id="big-src",
        layers=[LandLayerEntry(name="big")],
    )
    aoi_cache, _, _ = ensure_layer_geojson(preset_path, "aoi-src", "aoi")
    big_cache, _, _ = ensure_layer_geojson(preset_path, "big-src", "big")
    assert aoi_cache.is_file()
    assert big_cache.is_file()

    purge_clipped_serve_caches(preset_path)
    assert aoi_cache.is_file()
    assert not big_cache.is_file()


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
