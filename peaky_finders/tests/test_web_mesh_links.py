"""Web mesh link GeoJSON for project refresh."""

from __future__ import annotations

from peaky_finders.web.mesh_links import _merge_link_features


def test_merge_link_features_prefers_first_source() -> None:
    a = {"properties": {"id": "x--y", "source": "footprint"}, "type": "Feature"}
    b = {"properties": {"id": "x--y", "source": "rf"}, "type": "Feature"}
    merged = _merge_link_features([a], [b])
    assert len(merged) == 1
    assert merged[0]["properties"]["source"] == "footprint"


def test_merge_link_features_unions_distinct_ids() -> None:
    footprint = [
        {
            "type": "Feature",
            "properties": {"id": "site-a--site-b", "from": "site-a", "to": "site-b"},
            "geometry": {"type": "LineString", "coordinates": [[-119.0, 39.0], [-119.1, 39.1]]},
        }
    ]
    rf_only = [
        {
            "type": "Feature",
            "properties": {"id": "site-a--site-c", "from": "site-a", "to": "site-c", "source": "rf"},
            "geometry": {"type": "LineString", "coordinates": [[-119.0, 39.0], [-118.0, 39.0]]},
        }
    ]
    merged = _merge_link_features(footprint, rf_only)
    assert {f["properties"]["id"] for f in merged} == {"site-a--site-b", "site-a--site-c"}
