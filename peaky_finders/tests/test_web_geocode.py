"""Geocode ranking tests."""

from __future__ import annotations

from peaky_finders.web.geocode import geocode_place_ranked, rank_geocode_hits


def test_rank_geocode_hits_prefers_peak_over_street() -> None:
    hits = [
        {
            "display_name": "Mount Charleston Street, Reno, Nevada",
            "lat": 39.65,
            "lon": -119.87,
            "category": "highway",
            "type": "unclassified",
            "importance": 0.05,
        },
        {
            "display_name": "Charleston Peak, Spring Mountains, Nevada",
            "lat": 36.27,
            "lon": -115.69,
            "category": "natural",
            "type": "peak",
            "importance": 0.4,
        },
    ]
    ranked = rank_geocode_hits(hits, query="Charleston Peak Nevada")
    assert ranked[0]["type"] == "peak"
    assert ranked[0]["quality"] == "peak"
    assert ranked[1]["quality"] == "likely_street_not_peak"


def test_geocode_place_ranked_retries_without_bounded_box(monkeypatch) -> None:
    calls: list[bool | None] = []

    def fake_geocode(query: str, *, viewbox=None, bounded=False, countrycodes=None, limit=5, timeout_s=15.0):
        calls.append(viewbox is not None)
        if viewbox is not None:
            return []
        return [
            {
                "display_name": "Charleston Peak, Nevada",
                "lat": 36.27,
                "lon": -115.69,
                "category": "natural",
                "type": "peak",
                "importance": 0.4,
                "bbox": [-115.71, 36.25, -115.67, 36.29],
            }
        ]

    monkeypatch.setattr("peaky_finders.web.geocode.geocode_place", fake_geocode)
    payload = geocode_place_ranked("Charleston Peak, Nevada", viewbox=[-120, 39, -119, 40])
    assert calls == [True, False]
    assert payload["best"]["type"] == "peak"
