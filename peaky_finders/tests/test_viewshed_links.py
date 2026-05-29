"""Mutual site–site viewshed link LineStrings."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
from shapely.geometry import box

from peaky_finders import kml_bundle
from peaky_finders.splat_polygonize import SPLAT_GPKG_NAME, GX_DRAW_ORDER_MESH_SITE_TO_SITE
from peaky_finders.viewshed_links import mutual_sees_slug_pairs, write_site_links_kml


def _write_coverage_gpkg(path: Path, geom) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    gpd.GeoDataFrame(geometry=[geom], crs="EPSG:4326").to_file(path, driver="GPKG", layer="coverage")


def _overlay(*, slug: str, lat: float, lon: float) -> kml_bundle.AggregateSiteOverlay:
    return kml_bundle.AggregateSiteOverlay(
        slug=slug,
        folder_name=slug.upper(),
        overlay_href=f"sites/viewsheds/raster/{slug}/splat.png",
        north=1.0,
        south=0.0,
        east=1.0,
        west=0.0,
        rotation=0.0,
        center_lat=lat,
        center_lon=lon,
        antenna_height_agl_m=2.125,
        pin_description="p",
    )


def test_site_links_geojson_mutual_overlap(tmp_path: Path) -> None:
    splats = tmp_path / "splats"
    for slug, poly in (
        ("a", box(-115.03, 39.00, -115.01, 39.02)),
        ("b", box(-115.02, 39.01, -115.00, 39.03)),
    ):
        _write_coverage_gpkg(splats / slug / SPLAT_GPKG_NAME, poly)

    gpkg_a = splats / "a" / SPLAT_GPKG_NAME
    gpkg_b = splats / "b" / SPLAT_GPKG_NAME
    from peaky_finders.viewshed_links import site_links_geojson

    gj = site_links_geojson(
        coverage_gpkg_by_slug={"a": gpkg_a, "b": gpkg_b},
        sites=[
            _overlay(slug="a", lat=39.015, lon=-115.015),
            _overlay(slug="b", lat=39.018, lon=-115.018),
        ],
    )
    assert gj["type"] == "FeatureCollection"
    assert len(gj["features"]) == 1
    feat = gj["features"][0]
    assert feat["geometry"]["type"] == "LineString"
    assert feat["properties"]["from"] == "a"
    assert feat["properties"]["to"] == "b"


def test_write_site_links_mutual_overlap(tmp_path: Path) -> None:
    splats = tmp_path / "splats"
    for slug, poly in (
        ("a", box(-115.03, 39.00, -115.01, 39.02)),
        ("b", box(-115.02, 39.01, -115.00, 39.03)),
    ):
        _write_coverage_gpkg(splats / slug / SPLAT_GPKG_NAME, poly)

    out = tmp_path / "links.kml"
    gpkg_a = splats / "a" / SPLAT_GPKG_NAME
    gpkg_b = splats / "b" / SPLAT_GPKG_NAME
    ok = write_site_links_kml(
        coverage_gpkg_by_slug={"a": gpkg_a, "b": gpkg_b},
        sites=[
            _overlay(slug="a", lat=39.015, lon=-115.015),
            _overlay(slug="b", lat=39.018, lon=-115.018),
        ],
        out_kml=out,
    )
    assert ok
    raw = out.read_text(encoding="utf-8")
    assert "<LineString>" in raw
    assert "drawOrder" in raw and str(GX_DRAW_ORDER_MESH_SITE_TO_SITE) in raw
    assert "<altitudeMode>relativeToGround</altitudeMode>" in raw
    assert "2.125000" in raw
    assert "A" in raw and "B" in raw


def test_write_site_links_disjoint_no_output(tmp_path: Path) -> None:
    splats = tmp_path / "splats"
    _write_coverage_gpkg(splats / "a" / SPLAT_GPKG_NAME, box(-116.0, 39.0, -115.9, 39.1))
    _write_coverage_gpkg(splats / "b" / SPLAT_GPKG_NAME, box(-114.0, 39.0, -113.9, 39.1))

    out = tmp_path / "links.kml"
    assert not write_site_links_kml(
        coverage_gpkg_by_slug={
            "a": splats / "a" / SPLAT_GPKG_NAME,
            "b": splats / "b" / SPLAT_GPKG_NAME,
        },
        sites=[
            _overlay(slug="a", lat=39.05, lon=-115.95),
            _overlay(slug="b", lat=39.05, lon=-113.95),
        ],
        out_kml=out,
    )


def test_write_site_links_single_site_no_pairs(tmp_path: Path) -> None:
    splats = tmp_path / "splats"
    _write_coverage_gpkg(splats / "only" / SPLAT_GPKG_NAME, box(-115.03, 39.00, -115.01, 39.02))
    out = tmp_path / "links.kml"
    only_gpkg = splats / "only" / SPLAT_GPKG_NAME
    assert not write_site_links_kml(
        coverage_gpkg_by_slug={"only": only_gpkg},
        sites=[_overlay(slug="only", lat=39.01, lon=-115.02)],
        out_kml=out,
    )


def test_mutual_sees_slug_pairs_requires_both_directions() -> None:
    assert mutual_sees_slug_pairs({"a": ["b"], "b": ["a"]}) == [("a", "b")]
    assert mutual_sees_slug_pairs({"a": ["b"], "b": []}) == []
    assert mutual_sees_slug_pairs({"a": ["b"], "b": ["c"], "c": ["b"]}) == [("b", "c")]


def test_write_site_links_mutual_sees_without_footprint_overlap(tmp_path: Path) -> None:
    splats = tmp_path / "splats"
    _write_coverage_gpkg(splats / "a" / SPLAT_GPKG_NAME, box(-116.0, 39.0, -115.9, 39.1))
    _write_coverage_gpkg(splats / "b" / SPLAT_GPKG_NAME, box(-114.0, 39.0, -113.9, 39.1))

    out = tmp_path / "links.kml"
    ok = write_site_links_kml(
        coverage_gpkg_by_slug={
            "a": splats / "a" / SPLAT_GPKG_NAME,
            "b": splats / "b" / SPLAT_GPKG_NAME,
        },
        sites=[
            _overlay(slug="a", lat=39.05, lon=-115.95),
            _overlay(slug="b", lat=39.05, lon=-113.95),
        ],
        sees_by_slug={"a": ["b"], "b": ["a"]},
        out_kml=out,
    )
    assert ok
    raw = out.read_text(encoding="utf-8")
    assert "<LineString>" in raw
    assert "A" in raw and "B" in raw
