"""Bidirectional RF link GeoJSON for the web map (splatter ray profile)."""

from __future__ import annotations

from typing import Any

from peaky_finders.site_suggestions.rf_link import (
    clear_rf_link_cache,
    rf_mutual_link_slug_pairs_from_coords,
    rf_mutual_link_slug_pairs_from_site,
)
from peaky_finders.sites_job import Preset

__all__ = [
    "clear_rf_link_cache",
    "rf_link_line_features_for_preset",
    "rf_link_line_features_from_coords",
    "rf_link_line_features_from_site",
]


def rf_link_line_features_for_preset(preset: Preset) -> list[dict[str, Any]]:
    """GeoJSON LineString features for all mutual RF links across preset sites."""
    merged: dict[str, dict[str, Any]] = {}
    for slug, site in sorted(preset.sites.items()):
        for feat in rf_link_line_features_from_site(
            preset,
            from_slug=slug,
            from_lat=float(site.lat),
            from_lon=float(site.lon),
            from_label=site.name.strip() or slug,
        ):
            feat_id = str(feat.get("properties", {}).get("id") or "")
            if feat_id:
                merged.setdefault(feat_id, feat)
    return sorted(merged.values(), key=lambda f: str(f.get("properties", {}).get("id", "")))


def rf_link_line_features_from_site(
    preset: Preset,
    *,
    from_slug: str,
    from_lat: float,
    from_lon: float,
    from_label: str,
) -> list[dict[str, Any]]:
    """GeoJSON LineString features for mutual RF links from one site to others."""
    return rf_link_line_features_from_coords(
        preset,
        from_slug=from_slug,
        from_lat=from_lat,
        from_lon=from_lon,
        from_label=from_label,
        pair_slugs=rf_mutual_link_slug_pairs_from_site(
            preset,
            from_slug=from_slug,
            from_lat=from_lat,
            from_lon=from_lon,
        ),
    )


def rf_link_line_features_from_coords(
    preset: Preset,
    *,
    from_slug: str,
    from_lat: float,
    from_lon: float,
    from_label: str,
    pair_slugs: list[tuple[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """GeoJSON LineString features for mutual RF links from coordinates to RF sites."""
    if pair_slugs is None:
        pair_slugs = rf_mutual_link_slug_pairs_from_coords(
            preset,
            from_slug=from_slug,
            from_lat=from_lat,
            from_lon=from_lon,
        )
    features: list[dict[str, Any]] = []
    for slug_a, slug_b in pair_slugs:
        other_slug = slug_b if slug_a == from_slug else slug_a
        other = preset.sites[other_slug]
        lat_b = float(other.lat)
        lon_b = float(other.lon)
        label_b = other.name.strip() or other_slug
        features.append(
            _line_feature(
                slug_a=slug_a,
                slug_b=slug_b,
                lon_a=from_lon if slug_a == from_slug else lon_b,
                lat_a=from_lat if slug_a == from_slug else lat_b,
                lon_b=lon_b if slug_b == other_slug else from_lon,
                lat_b=lat_b if slug_b == other_slug else from_lat,
                label=f"{from_label} ↔ {label_b}",
            )
        )
    return features


def _line_feature(
    *,
    slug_a: str,
    slug_b: str,
    lon_a: float,
    lat_a: float,
    lon_b: float,
    lat_b: float,
    label: str,
) -> dict[str, Any]:
    return {
        "type": "Feature",
        "properties": {
            "id": f"{slug_a}--{slug_b}",
            "from": slug_a,
            "to": slug_b,
            "label": label,
            "source": "rf",
        },
        "geometry": {
            "type": "LineString",
            "coordinates": [[lon_a, lat_a], [lon_b, lat_b]],
        },
    }
