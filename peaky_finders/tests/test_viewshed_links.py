"""Mutual site–site link LineStrings (RF + ``sees``)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

from peaky_finders import kml_bundle
from peaky_finders.splat_polygonize import GX_DRAW_ORDER_MESH_SITE_TO_SITE
from peaky_finders.viewshed_links import (
    mutual_sees_slug_pairs,
    mutual_site_link_slug_pairs,
    site_links_geojson,
    write_site_links_kml,
)


@dataclass
class _FakeSite:
    name: str
    lat: float
    lon: float
    participates_in_rf: bool = True


@dataclass
class _FakePreset:
    sites: dict[str, _FakeSite]


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


def test_site_links_geojson_rf_pairs() -> None:
    preset = _FakePreset(
        sites={
            "a": _FakeSite("Alpha", 39.015, -115.015),
            "b": _FakeSite("Bravo", 39.018, -115.018),
        }
    )
    with patch(
        "peaky_finders.viewshed_links.rf_mutual_link_slug_pairs",
        return_value=[("a", "b")],
    ):
        gj = site_links_geojson(
            preset=preset,  # type: ignore[arg-type]
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


def test_write_site_links_rf_pairs(tmp_path: Path) -> None:
    preset = _FakePreset(
        sites={
            "a": _FakeSite("Alpha", 39.015, -115.015),
            "b": _FakeSite("Bravo", 39.018, -115.018),
        }
    )
    out = tmp_path / "links.kml"
    with patch(
        "peaky_finders.viewshed_links.rf_mutual_link_slug_pairs",
        return_value=[("a", "b")],
    ):
        ok = write_site_links_kml(
            preset=preset,  # type: ignore[arg-type]
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


def test_write_site_links_no_rf_or_sees(tmp_path: Path) -> None:
    preset = _FakePreset(
        sites={
            "a": _FakeSite("Alpha", 39.05, -115.95),
            "b": _FakeSite("Bravo", 39.05, -113.95),
        }
    )
    out = tmp_path / "links.kml"
    with patch(
        "peaky_finders.viewshed_links.rf_mutual_link_slug_pairs",
        return_value=[],
    ):
        assert not write_site_links_kml(
            preset=preset,  # type: ignore[arg-type]
            sites=[
                _overlay(slug="a", lat=39.05, lon=-115.95),
                _overlay(slug="b", lat=39.05, lon=-113.95),
            ],
            out_kml=out,
        )


def test_write_site_links_single_site_no_pairs(tmp_path: Path) -> None:
    preset = _FakePreset(sites={"only": _FakeSite("Only", 39.01, -115.02)})
    out = tmp_path / "links.kml"
    with patch(
        "peaky_finders.viewshed_links.rf_mutual_link_slug_pairs",
        return_value=[],
    ):
        assert not write_site_links_kml(
            preset=preset,  # type: ignore[arg-type]
            sites=[_overlay(slug="only", lat=39.01, lon=-115.02)],
            out_kml=out,
        )


def test_mutual_sees_slug_pairs_requires_both_directions() -> None:
    assert mutual_sees_slug_pairs({"a": ["b"], "b": ["a"]}) == [("a", "b")]
    assert mutual_sees_slug_pairs({"a": ["b"], "b": []}) == []
    assert mutual_sees_slug_pairs({"a": ["b"], "b": ["c"], "c": ["b"]}) == [("b", "c")]


def test_mutual_site_link_slug_pairs_skips_goals() -> None:
    preset = _FakePreset(
        sites={
            "hub": _FakeSite("Hub", 39.0, -119.0),
            "target": _FakeSite("Target", 39.1, -119.1, participates_in_rf=False),
        }
    )
    with patch(
        "peaky_finders.viewshed_links.rf_mutual_link_slug_pairs",
        return_value=[("hub", "target")],
    ):
        pairs = mutual_site_link_slug_pairs(preset=preset, sees_by_slug={})  # type: ignore[arg-type]
    assert pairs == []


def test_write_site_links_mutual_sees_without_rf(tmp_path: Path) -> None:
    preset = _FakePreset(
        sites={
            "a": _FakeSite("Alpha", 39.05, -115.95),
            "b": _FakeSite("Bravo", 39.05, -113.95),
        }
    )
    out = tmp_path / "links.kml"
    with patch(
        "peaky_finders.viewshed_links.rf_mutual_link_slug_pairs",
        return_value=[],
    ):
        ok = write_site_links_kml(
            preset=preset,  # type: ignore[arg-type]
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
