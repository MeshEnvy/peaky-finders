"""Aggregate KMZ helpers: bundle layer zips, doc.kml stacking, KML style roles."""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import test_preset_mapping as tpm
from shapely.geometry import box

from peaky_finders import bundle_build, kml_bundle
from peaky_finders.bundle_clips import (
    COMPOSITE_EXCLUDE_FORMAT,
    COMPOSITE_INCLUDE_FORMAT,
    list_exclude_layer_kmz_entries,
    list_include_layer_kmz_entries,
    read_composite_exclude_manifest,
    read_composite_include_manifest,
)
from peaky_finders.bundle_build import _clip_stem, resolve_land_use_gdb_path
from peaky_finders.sites_job import BundleConfig, GdbLayerGroup, GdbLayerSpec
from peaky_finders.splat_polygonize import (
    GX_DRAW_ORDER_MESH_DEPTH_D1,
    GX_DRAW_ORDER_MESH_PAIRWISE,
    GX_DRAW_ORDER_MESH_SITE_TO_SITE,
    GX_DRAW_ORDER_VIEWSHED_COVERAGE_POLYGON,
    GX_DRAW_ORDER_VIEWSHED_RASTER,
)


def test_mesh_gx_draw_order_band_above_viewsheds() -> None:
    """Google Earth stacks clamped features by gx:drawOrder; mesh must stay above viewsheds."""
    assert GX_DRAW_ORDER_VIEWSHED_RASTER == GX_DRAW_ORDER_VIEWSHED_COVERAGE_POLYGON
    assert GX_DRAW_ORDER_MESH_DEPTH_D1 > GX_DRAW_ORDER_VIEWSHED_COVERAGE_POLYGON
    assert GX_DRAW_ORDER_MESH_PAIRWISE > GX_DRAW_ORDER_VIEWSHED_COVERAGE_POLYGON
    assert GX_DRAW_ORDER_MESH_SITE_TO_SITE > GX_DRAW_ORDER_VIEWSHED_COVERAGE_POLYGON


def test_build_aggregate_document_kml_order_sites_then_bundle_tail() -> None:
    xml = kml_bundle.build_aggregate_document_kml(
        document_title="t",
        sites=[
            kml_bundle.AggregateSiteOverlay(
                slug="a",
                folder_name="A",
                overlay_href="sites/viewsheds/raster/a/splat.png",
                north=1.0,
                south=0.0,
                east=1.0,
                west=0.0,
                rotation=0.0,
                center_lat=0.5,
                center_lon=0.5,
                antenna_height_agl_m=2.0,
                pin_description="p",
            )
        ],
        bundle_network_links=[("aoi", "aoi/aoi.kml")],
    )
    i_sites = xml.find("<Folder>\n      <name>sites</name>")
    i_aoi = xml.find("<name>aoi</name>\n      <visibility>0</visibility>")
    assert i_sites != -1 and i_aoi != -1
    assert i_sites < i_aoi
    assert "<Folder>\n      <name>candidates</name>" not in xml


def test_build_aggregate_document_kml_layer_visibility_overrides() -> None:
    xml_on = kml_bundle.build_aggregate_document_kml(
        document_title="t",
        sites=[
            kml_bundle.AggregateSiteOverlay(
                slug="s",
                folder_name="S",
                overlay_href="sites/viewsheds/raster/s/splat.png",
                north=1.0,
                south=0.0,
                east=1.0,
                west=0.0,
                rotation=0.0,
                center_lat=0.5,
                center_lon=0.5,
                antenna_height_agl_m=2.0,
                pin_description="p",
            )
        ],
        bundle_network_links=[
            ("aoi", "aoi/aoi.kml"),
        ],
        layer_visibility=kml_bundle.KmzDocumentLayerVisibility(aoi=True, pins=False),
    )
    assert (
        "<NetworkLink>\n"
        "        <name>aoi</name>\n"
        "        <visibility>1</visibility>"
    ) in xml_on
    assert "<name>pins</name>\n        <visibility>0</visibility>" in xml_on

    xml_raster = kml_bundle.build_aggregate_document_kml(
        document_title="t",
        sites=[
            kml_bundle.AggregateSiteOverlay(
                slug="s",
                folder_name="S",
                overlay_href="sites/viewsheds/raster/s/splat.png",
                north=1.0,
                south=0.0,
                east=1.0,
                west=0.0,
                rotation=0.0,
                center_lat=0.5,
                center_lon=0.5,
                antenna_height_agl_m=2.0,
                pin_description="p",
            )
        ],
        layer_visibility=kml_bundle.KmzDocumentLayerVisibility(viewshed_raster=True),
    )
    assert (
        "<GroundOverlay>\n"
        "                <name>S</name>\n"
        "                <visibility>1</visibility>\n"
        "                <gx:drawOrder>0</gx:drawOrder>"
    ) in xml_raster
    assert 'xmlns:gx="http://www.google.com/kml/ext/2.2"' in xml_raster


def test_build_aggregate_document_kml_default_hidden_folders_and_order() -> None:
    xml = kml_bundle.build_aggregate_document_kml(
        document_title="t",
        sites=[
            kml_bundle.AggregateSiteOverlay(
                slug="s",
                folder_name="S",
                overlay_href="sites/viewsheds/raster/s/splat.png",
                north=1.0,
                south=0.0,
                east=1.0,
                west=0.0,
                rotation=0.0,
                center_lat=0.5,
                center_lon=0.5,
                antenna_height_agl_m=2.0,
                pin_description="p",
            )
        ],
        bundle_network_links=[
            ("aoi", "aoi/aoi.kml"),
            ("include", "include/include.kml"),
            ("exclude", "exclude/exclude.kml"),
            ("eligible", "eligible/x.kml"),
        ],
        reference_bundle_links=[("blm_districts", "reference/districts.kml", False)],
    )
    markers = [
        "<Folder>\n      <name>sites</name>",
        "<name>eligible</name>\n      <visibility>0</visibility>",
        "<name>exclude</name>\n      <visibility>0</visibility>",
        "<name>include</name>\n      <visibility>0</visibility>",
        "<name>blm_districts</name>\n      <visibility>0</visibility>",
        "<name>aoi</name>\n      <visibility>0</visibility>",
    ]
    positions = [xml.find(m) for m in markers]
    assert all(p != -1 for p in positions)
    assert positions == sorted(positions)

    r = xml.index("<Folder>\n      <name>sites</name>")
    sites_tail = xml[r:]
    j = sites_tail.index("<name>raster</name>")
    assert sites_tail[j : j + 80].find("<visibility>0</visibility>") != -1
    # Google Earth ignores parent Folder visibility for merged NetworkLink content; gate each link.
    assert (
        "<NetworkLink>\n"
        "        <name>eligible</name>\n"
        "        <visibility>0</visibility>"
    ) in xml


def test_build_aggregate_document_kml_mesh_pairwise_when_network_links_set() -> None:
    xml = kml_bundle.build_aggregate_document_kml(
        document_title="t",
        sites=[
            kml_bundle.AggregateSiteOverlay(
                slug="s",
                folder_name="S",
                overlay_href="sites/viewsheds/raster/s/splat.png",
                north=1.0,
                south=0.0,
                east=1.0,
                west=0.0,
                rotation=0.0,
                center_lat=0.5,
                center_lon=0.5,
                antenna_height_agl_m=2.0,
                pin_description="p",
            )
        ],
        mesh_pairwise_network_links=[
            ("A <-> B", "sites/mesh/coverage/pairwise/a__vs__b.kml"),
            ("X <-> Y", "sites/mesh/coverage/pairwise/foo.kml"),
        ],
    )
    assert "<name>mesh</name>" in xml
    assert "<name>pairwise</name>" in xml
    assert "<name>A &lt;-&gt; B</name>" in xml
    assert "<name>X &lt;-&gt; Y</name>" in xml
    assert "sites/mesh/coverage/pairwise/a__vs__b.kml" in xml
    assert "sites/mesh/coverage/pairwise/foo.kml" in xml
    i_viewsheds = xml.find("<name>viewsheds</name>")
    i_mesh = xml.find("<name>mesh</name>")
    assert i_viewsheds != -1 and i_mesh != -1
    assert i_viewsheds < i_mesh
    tail = xml[i_mesh:]
    assert tail.find("<name>pins</name>\n        <visibility>1</visibility>") != -1


def test_build_aggregate_document_kml_mesh_order_edges_pairwise_before_pins() -> None:
    xml = kml_bundle.build_aggregate_document_kml(
        document_title="t",
        sites=[
            kml_bundle.AggregateSiteOverlay(
                slug="s",
                folder_name="S",
                overlay_href="sites/viewsheds/raster/s/splat.png",
                north=1.0,
                south=0.0,
                east=1.0,
                west=0.0,
                rotation=0.0,
                center_lat=0.5,
                center_lon=0.5,
                antenna_height_agl_m=2.0,
                pin_description="p",
            )
        ],
        mesh_edges_href="sites/mesh/edges/site_to_site.kml",
        mesh_pairwise_network_links=[("Pair AB", "sites/mesh/coverage/pairwise/a__vs__b.kml")],
        mesh_pairwise_eligible_network_links=[
            ("Pair eligible", "sites/mesh/coverage/pairwise_eligible/a__vs__b.kml"),
        ],
    )
    assert "sites/mesh/edges/site_to_site.kml" in xml
    i_ve = xml.find("<name>viewsheds</name>")
    i_mesh = xml.find("<name>mesh</name>")
    i_edges = xml.find("<name>edges</name>")
    i_cov = xml.find("<name>coverage</name>")
    i_pins = xml.find("<name>pins</name>\n        <visibility>1</visibility>")
    assert i_ve != -1 and i_mesh != -1 and i_edges != -1 and i_cov != -1 and i_pins != -1
    assert i_ve < i_mesh < i_pins
    assert i_edges < i_cov
    t = xml[i_mesh:]
    assert t.find("<name>edges</name>") < t.find("<name>coverage</name>")


def test_build_aggregate_document_kml_mesh_depth_grouped_by_band() -> None:
    xml = kml_bundle.build_aggregate_document_kml(
        document_title="t",
        sites=[
            kml_bundle.AggregateSiteOverlay(
                slug="s",
                folder_name="S",
                overlay_href="sites/viewsheds/raster/s/splat.png",
                north=1.0,
                south=0.0,
                east=1.0,
                west=0.0,
                rotation=0.0,
                center_lat=0.5,
                center_lon=0.5,
                antenna_height_agl_m=2.0,
                pin_description="p",
            )
        ],
        mesh_depth_network_links=[
            (
                "d1_unique",
                "Alpha",
                "sites/mesh/coverage/depth/d1_unique/a.kml",
                "mesh_depth_d1_unique",
            ),
            (
                "d1_unique",
                "Beta",
                "sites/mesh/coverage/depth/d1_unique/b.kml",
                "mesh_depth_d1_unique",
            ),
        ],
    )
    assert "Coverage depth: 1 site" in xml
    assert "sites/mesh/coverage/depth/d1_unique/a.kml" in xml
    assert "Alpha" in xml and "Beta" in xml


def test_kml_build_style_icon_tints_with_line_color() -> None:
    st = bundle_build._kml_build_style_element(
        "id",
        "ff112233",
        "00000000",
        polygon_fill=False,
        ns="",
    )
    ic = st.find("IconStyle")
    assert ic is not None
    col = ic.find("color")
    assert col is not None and col.text == "ff112233"


def test_geodataframe_kml_explodes_multipolygon_and_injects_ge_hints(tmp_path: Path) -> None:
    """Google Earth often skips part of a huge MultiGeometry; one Placemark per polygon + gx:drawOrder."""
    from shapely.geometry import MultiPolygon, box

    gdf = gpd.GeoDataFrame(
        geometry=[MultiPolygon([box(-1.2, -1.1, -0.1, -0.05), box(1.0, 1.0, 2.0, 2.0)])],
        crs="EPSG:4326",
    )
    elig_dir = tmp_path / "eligible_land_use"
    elig_dir.mkdir()
    kml = elig_dir / "eligible_land_use.kml"
    bundle_build._write_geodataframe_kml(
        gdf,
        kml,
        layer_label=bundle_build.ELIGIBLE_LAND_USE_LAYER,
        kml_overlay=None,
    )
    raw = kml.read_text(encoding="utf-8")
    assert raw.count("<Placemark ") == 2
    assert raw.count("clampToGround") == 2
    assert "drawOrder" in raw


def test_build_aggregate_document_kml_exclude_layers_parent_folder() -> None:
    xml = kml_bundle.build_aggregate_document_kml(
        document_title="t",
        sites=[
            kml_bundle.AggregateSiteOverlay(
                slug="s",
                folder_name="S",
                overlay_href="sites/viewsheds/raster/s/splat.png",
                north=1.0,
                south=0.0,
                east=1.0,
                west=0.0,
                rotation=0.0,
                center_lat=0.5,
                center_lon=0.5,
                antenna_height_agl_m=2.0,
                pin_description="p",
            )
        ],
        bundle_network_links=[
            ("aoi", "aoi/aoi.kml"),
            ("include", "include/include.kml"),
            ("eligible", "eligible/x.kml"),
        ],
        exclude_layer_network_links=[
            ("exclude/foo.gpkg::layer_a", "exclude/layers/a.kml"),
            ("exclude/bar.gpkg::layer_b", "exclude/layers/b.kml"),
        ],
        reference_bundle_links=[("blm_districts", "reference/districts.kml", False)],
    )
    assert "exclude/exclude.kml" not in xml
    assert '<Folder>\n      <name>exclude</name>' in xml
    assert "exclude/layers/a.kml" in xml and "exclude/layers/b.kml" in xml
    i_eligible = xml.find("<name>eligible</name>")
    i_exclude_folder = xml.find('<Folder>\n      <name>exclude</name>')
    assert i_eligible != -1 and i_exclude_folder != -1
    assert i_eligible < i_exclude_folder


def test_build_aggregate_document_kml_include_layers_parent_folder() -> None:
    xml = kml_bundle.build_aggregate_document_kml(
        document_title="t",
        sites=[
            kml_bundle.AggregateSiteOverlay(
                slug="s",
                folder_name="S",
                overlay_href="sites/viewsheds/raster/s/splat.png",
                north=1.0,
                south=0.0,
                east=1.0,
                west=0.0,
                rotation=0.0,
                center_lat=0.5,
                center_lon=0.5,
                antenna_height_agl_m=2.0,
                pin_description="p",
            )
        ],
        bundle_network_links=[
            ("aoi", "aoi/aoi.kml"),
            ("eligible", "eligible/x.kml"),
            ("exclude", "exclude/exclude.kml"),
        ],
        include_layer_network_links=[
            ("include/foo.gpkg::layer_a", "include/layers/a.kml"),
            ("include/bar.gpkg::layer_b", "include/layers/b.kml"),
        ],
        reference_bundle_links=[("blm_districts", "reference/districts.kml", False)],
    )
    assert "include/include.kml" not in xml
    assert '<Folder>\n      <name>include</name>' in xml
    assert "include/layers/a.kml" in xml and "include/layers/b.kml" in xml
    i_exclude = xml.find("<name>exclude</name>")
    i_include_folder = xml.find('<Folder>\n      <name>include</name>')
    assert i_exclude != -1 and i_include_folder != -1
    assert i_exclude < i_include_folder


def test_build_aggregate_document_kml_exclude_layers_folder_visibility() -> None:
    xml = kml_bundle.build_aggregate_document_kml(
        document_title="t",
        sites=[
            kml_bundle.AggregateSiteOverlay(
                slug="s",
                folder_name="S",
                overlay_href="sites/viewsheds/raster/s/splat.png",
                north=1.0,
                south=0.0,
                east=1.0,
                west=0.0,
                rotation=0.0,
                center_lat=0.5,
                center_lon=0.5,
                antenna_height_agl_m=2.0,
                pin_description="p",
            )
        ],
        exclude_layer_network_links=[("Wilderness layer", "exclude/layers/w.kml")],
        layer_visibility=kml_bundle.KmzDocumentLayerVisibility(exclude=True),
    )
    assert "<Folder>\n      <name>exclude</name>\n      <visibility>1</visibility>" in xml
    assert (
        "<NetworkLink>\n"
        "        <name>Wilderness layer</name>\n"
        "        <visibility>1</visibility>"
    ) in xml


def test_build_aggregate_document_kml_include_layers_folder_visibility() -> None:
    xml = kml_bundle.build_aggregate_document_kml(
        document_title="t",
        sites=[
            kml_bundle.AggregateSiteOverlay(
                slug="s",
                folder_name="S",
                overlay_href="sites/viewsheds/raster/s/splat.png",
                north=1.0,
                south=0.0,
                east=1.0,
                west=0.0,
                rotation=0.0,
                center_lat=0.5,
                center_lon=0.5,
                antenna_height_agl_m=2.0,
                pin_description="p",
            )
        ],
        include_layer_network_links=[("SMA layer", "include/layers/x.kml")],
        layer_visibility=kml_bundle.KmzDocumentLayerVisibility(include=True),
    )
    assert "<Folder>\n      <name>include</name>\n      <visibility>1</visibility>" in xml
    assert (
        "<NetworkLink>\n"
        "        <name>SMA layer</name>\n"
        "        <visibility>1</visibility>"
    ) in xml


def test_read_composite_exclude_manifest_sorts_clip_shas(tmp_path: Path) -> None:
    clips_root = tmp_path / "clips"
    manifest_dir = clips_root / "exclude" / "abcd1234deadbeef"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "manifest.json").write_text(
        json.dumps({"format": COMPOSITE_EXCLUDE_FORMAT, "clips": ["zzz", "aaa"], "aoi_mask": "x"}),
        encoding="utf-8",
    )
    assert read_composite_exclude_manifest(clips_root, "abcd1234deadbeef") == ["aaa", "zzz"]


def test_read_composite_include_manifest_sorts_clip_shas(tmp_path: Path) -> None:
    clips_root = tmp_path / "clips"
    manifest_dir = clips_root / "include" / "feedbeef4321abcd"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "manifest.json").write_text(
        json.dumps({"format": COMPOSITE_INCLUDE_FORMAT, "clips": ["zzz", "aaa"], "aoi_mask": "x"}),
        encoding="utf-8",
    )
    assert read_composite_include_manifest(clips_root, "feedbeef4321abcd") == ["aaa", "zzz"]


def test_list_exclude_layer_kmz_entries_legacy(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    bundle_dir = tmp_path / "bundle"
    exc = bundle_dir / "exclude"
    exc.mkdir(parents=True)

    preset_path_str = "exclude/foo.gpkg"
    resolved = resolve_land_use_gdb_path(data_dir, preset_path_str)
    stem = _clip_stem(preset_path_str, resolved, "exclusion", None)
    fname = f"exclude_{stem}.gpkg"
    (exc / fname.replace(".gpkg", ".kml")).write_text("<kml/>", encoding="utf-8")

    manifest = {
        "format": "bundle_exclude_clip_manifest/v4",
        "items": [{"file": fname, "path": preset_path_str, "layer": "exclusion"}],
    }
    (exc / "clip_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    bundle = BundleConfig(
        aoi=[GdbLayerGroup(path="aoi/a.gpkg", layers=[GdbLayerSpec(name="la")])],
        include=[GdbLayerGroup(path="inc/i.gpkg", layers=[GdbLayerSpec(name="li")])],
        exclude=[],
    )
    preset = tpm._minimal_preset(bundle=bundle)

    rows = list_exclude_layer_kmz_entries(bundle_dir, preset=preset, data_dir=data_dir)
    assert len(rows) == 1
    assert rows[0][0] == "exclude/foo.gpkg::exclusion"
    assert rows[0][2] == f"exclude/layers/{stem}.kml"


def test_list_include_layer_kmz_entries_legacy(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    bundle_dir = tmp_path / "bundle"
    inc = bundle_dir / "include"
    inc.mkdir(parents=True)

    preset_path_str = "include/foo.gpkg"
    resolved = resolve_land_use_gdb_path(data_dir, preset_path_str)
    stem = _clip_stem(preset_path_str, resolved, "public_land", None)
    fname = f"include_{stem}.gpkg"
    (inc / fname.replace(".gpkg", ".kml")).write_text("<kml/>", encoding="utf-8")

    manifest = {
        "format": "bundle_include_clip_manifest/v4",
        "items": [{"file": fname, "path": preset_path_str, "layer": "public_land"}],
    }
    (inc / "clip_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    bundle = BundleConfig(
        aoi=[GdbLayerGroup(path="aoi/a.gpkg", layers=[GdbLayerSpec(name="la")])],
        include=[],
        exclude=[GdbLayerGroup(path="exclude/e.gpkg", layers=[GdbLayerSpec(name="lx")])],
    )
    preset = tpm._minimal_preset(bundle=bundle)

    rows = list_include_layer_kmz_entries(bundle_dir, preset=preset, data_dir=data_dir)
    assert len(rows) == 1
    assert rows[0][0] == "include/foo.gpkg::public_land"
    assert rows[0][2] == f"include/layers/{stem}.kml"


def test_kml_overlay_role_exclude_for_clip_cache_sidecar_path() -> None:
    """Clip-cache KML is ``clip.kml`` under ``clips/clip/<sha>/``; rely on GDB preset path prefix."""
    clip_kml = Path("/cache/clips/clip/deadbeef12345678/clip.kml")
    lab = "exclude/BLM_Natl_Wilderness_Areas.gdb::Wilderness_Polygons"
    assert bundle_build._kml_overlay_role(clip_kml, lab) == "exclude"
    nested = Path("/tmp/foo.kml")
    assert bundle_build._kml_overlay_role(nested, "vendor/exclude/bar.gpkg::layer_x") == "exclude"


def test_kml_overlay_role_include_for_clip_cache_sidecar_path() -> None:
    clip_kml = Path("/cache/clips/clip/cafe4321beef9876/clip.kml")
    lab = "include/SMA_WM.gdb::eligible_poly"
    assert bundle_build._kml_overlay_role(clip_kml, lab) == "include"
    nested = Path("/tmp/foo.kml")
    assert bundle_build._kml_overlay_role(nested, "vendor/include/bar.gpkg::layer_y") == "include"


def test_exclude_kml_placemark_balloon(tmp_path: Path) -> None:
    kml_dir = tmp_path / "exclude"
    kml_dir.mkdir(parents=True)
    kml_path = kml_dir / "layer.kml"
    gdf = gpd.GeoDataFrame(
        {
            "NAME": ["Peak Mesa"],
            "STATUS": ["Withdrawal"],
            "geometry": [box(-119.5, 39.1, -119.4, 39.2)],
        },
        crs="EPSG:4326",
    )
    bundle_build._write_geodataframe_kml(
        gdf,
        kml_path,
        layer_label="exclude/foo.gpkg::exclusion",
        kml_overlay=None,
    )
    raw = kml_path.read_text(encoding="utf-8")
    assert "<name>Peak Mesa</name>" in raw
    assert "Exclusion layer:" in raw and "exclude/foo.gpkg::exclusion" in raw
    assert "STATUS:" in raw


def test_include_kml_placemark_balloon(tmp_path: Path) -> None:
    kml_dir = tmp_path / "include"
    kml_dir.mkdir(parents=True)
    kml_path = kml_dir / "layer.kml"
    gdf = gpd.GeoDataFrame(
        {
            "NAME": ["Sector 7"],
            "ACRES": [1200.0],
            "geometry": [box(-118.5, 38.1, -118.4, 38.2)],
        },
        crs="EPSG:4326",
    )
    bundle_build._write_geodataframe_kml(
        gdf,
        kml_path,
        layer_label="include/foo.gpkg::public_land",
        kml_overlay=None,
    )
    raw = kml_path.read_text(encoding="utf-8")
    assert "<name>Sector 7</name>" in raw
    assert "Inclusion layer:" in raw and "include/foo.gpkg::public_land" in raw
    assert "ACRES:" in raw


def test_build_aggregate_document_kml_eligible_layers_parent_folder() -> None:
    xml = kml_bundle.build_aggregate_document_kml(
        document_title="t",
        sites=[
            kml_bundle.AggregateSiteOverlay(
                slug="s",
                folder_name="S",
                overlay_href="sites/viewsheds/raster/s/splat.png",
                north=1.0,
                south=0.0,
                east=1.0,
                west=0.0,
                rotation=0.0,
                center_lat=0.5,
                center_lon=0.5,
                antenna_height_agl_m=2.0,
                pin_description="p",
            )
        ],
        bundle_network_links=[
            ("aoi", "aoi/aoi.kml"),
            ("exclude", "exclude/exclude.kml"),
            ("include", "include/include.kml"),
        ],
        eligible_layer_network_links=[
            ("Eligible: foo", "eligible/layers/part_a.kml"),
            ("Eligible: bar", "eligible/layers/part_b.kml"),
        ],
        reference_bundle_links=[("blm_districts", "reference/districts.kml", False)],
    )
    assert "eligible/eligible_land_use.kml" not in xml
    assert '<Folder>\n      <name>eligible</name>' in xml
    assert "eligible/layers/part_a.kml" in xml and "eligible/layers/part_b.kml" in xml
    i_elig_folder = xml.find('<Folder>\n      <name>eligible</name>')
    i_exclude = xml.find("<name>exclude</name>")
    assert i_elig_folder != -1 and i_exclude != -1
    assert i_elig_folder < i_exclude


def test_kml_overlay_role_eligible_slices_use_disk_layout_not_include_label() -> None:
    slice_kml = Path("/work/clips/eligible/a1b2c3d412345678/layers/inc.kml")
    lab = "include/foo.gpkg::public_land"
    assert bundle_build._kml_overlay_role(slice_kml, lab) == "eligible"


def test_eligible_land_slice_trimmed_to_global_eligible_polygon(tmp_path: Path) -> None:
    eligible_ll = box(-118.5, 38.0, -118.2, 38.4)
    clip_ll = box(-118.4, 38.05, -117.95, 38.45)
    g_clip = gpd.GeoDataFrame(geometry=[clip_ll], crs="EPSG:4326").to_crs("EPSG:3857")
    clip_path = tmp_path / "inc.gpkg"
    g_clip.to_file(clip_path, driver="GPKG", layer="features")

    out = bundle_build.eligible_land_slice_from_include_clip_gpkg(eligible_ll, clip_path)
    assert not out.empty
    xmax = out.geometry.iloc[0].bounds[2]
    assert xmax <= -118.2 + 1e-6


def test_eligible_slice_under_layers_placemark_heading(tmp_path: Path) -> None:
    lyr = tmp_path / "clips" / "eligible" / "0123456789abcdef" / "layers"
    lyr.mkdir(parents=True)
    kml_path = lyr / "stem.kml"
    gdf = gpd.GeoDataFrame(
        {
            "NAME": ["Remainder"],
            "geometry": [box(-118.4, 38.1, -118.3, 38.15)],
        },
        crs="EPSG:4326",
    )
    bundle_build._write_geodataframe_kml(
        gdf,
        kml_path,
        layer_label="include/foo.gpkg::public_land",
        kml_overlay=None,
    )
    raw = kml_path.read_text(encoding="utf-8")
    assert "Eligible land (after exclusions):" in raw and "include/foo.gpkg::public_land" in raw
