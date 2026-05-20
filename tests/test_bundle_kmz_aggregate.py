"""Aggregate KMZ helpers: bundle layer zips, doc.kml stacking, KML style roles."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd

from peaky_finders import bundle_build, kml_bundle
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


def test_bundle_land_use_layers_omits_installed_pins(tmp_path: Path) -> None:
    from peaky_finders.cli import _bundle_land_use_layers_for_kmz

    ip = tmp_path / "installed_pins"
    ip.mkdir()
    kml = ip / bundle_build.INSTALLED_PINS_KML_BASENAME
    kml.write_text("<?xml version='1.0'?><kml/>", encoding="utf-8")
    png = kml.with_suffix(".png")
    png.write_bytes(b"\x89PNG\r\n")

    nets, zpairs, _refs = _bundle_land_use_layers_for_kmz(tmp_path)
    assert not any(lbl == "installed_pins" for lbl, _ in nets)
    arcs = {a for _s, a in zpairs}
    assert "installed_pins/installed_pins.kml" not in arcs
    assert "installed_pins/installed_pins.png" not in arcs


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
