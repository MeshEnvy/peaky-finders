"""RF link helpers for the web map."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import patch

from peaky_finders.web.rf_links import clear_rf_link_cache, rf_link_line_features_from_site


@dataclass
class _FakeSite:
    name: str
    lat: float
    lon: float


@dataclass
class _FakePreset:
    sites: dict[str, _FakeSite]
    simulation: object


def test_rf_link_line_features_uses_cache() -> None:
    clear_rf_link_cache()
    preset = _FakePreset(
        sites={
            "site-a": _FakeSite("Alpha", 39.0, -119.0),
            "site-b": _FakeSite("Bravo", 39.1, -119.1),
        },
        simulation=object(),
    )
    with patch("peaky_finders.web.rf_links.max_hop_range_m", return_value=200_000.0), patch(
        "peaky_finders.web.rf_links.rf_json_for_preset",
        return_value='{"rf":"test"}',
    ), patch(
        "peaky_finders.web.rf_links.splatter_session",
    ) as session_fn, patch(
        "peaky_finders.web.rf_links.ensure_dem_for_points",
    ), patch(
        "peaky_finders.web.rf_links.mutual_hop_batch",
        return_value=[True],
    ) as batch:
        first = rf_link_line_features_from_site(
            preset,  # type: ignore[arg-type]
            from_slug="site-a",
            from_lat=39.0,
            from_lon=-119.0,
            from_label="Alpha",
        )
        second = rf_link_line_features_from_site(
            preset,  # type: ignore[arg-type]
            from_slug="site-a",
            from_lat=39.0,
            from_lon=-119.0,
            from_label="Alpha",
        )

    assert len(first) == 1
    assert first[0]["properties"]["id"] == "site-a--site-b"
    assert first[0]["properties"]["source"] == "rf"
    assert second == first
    batch.assert_called_once()
    session_fn.assert_called_once()


def test_rf_link_line_features_skips_out_of_range() -> None:
    clear_rf_link_cache()
    preset = _FakePreset(
        sites={"site-b": _FakeSite("Bravo", 50.0, -100.0)},
        simulation=object(),
    )
    with patch("peaky_finders.web.rf_links.max_hop_range_m", return_value=1000.0), patch(
        "peaky_finders.web.rf_links.rf_json_for_preset",
        return_value='{"rf":"test"}',
    ), patch("peaky_finders.web.rf_links.mutual_hop_batch") as batch:
        features = rf_link_line_features_from_site(
            preset,  # type: ignore[arg-type]
            from_slug="site-a",
            from_lat=39.0,
            from_lon=-119.0,
            from_label="Alpha",
        )

    assert features == []
    batch.assert_not_called()
