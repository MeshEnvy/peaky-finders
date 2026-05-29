"""Bidirectional RF link checks for the web map (splatter ray profile)."""

from __future__ import annotations

import hashlib
import math
from typing import Any

from peaky_finders.site_suggestions.rf_link import (
    ensure_dem_for_points,
    max_hop_range_m,
    mutual_hop_batch,
    rf_json_for_preset,
    splatter_session,
)
from peaky_finders.sites_job import Preset

_EARTH_R_M = 6_371_000.0
_COORD_DECIMALS = 6

_rf_link_cache: dict[str, bool] = {}


def clear_rf_link_cache() -> None:
    _rf_link_cache.clear()


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    rlat1, rlon1, rlat2, rlon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    dlat = rlat2 - rlat1
    dlon = rlon2 - rlon1
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    return 2 * _EARTH_R_M * math.asin(min(1.0, math.sqrt(a)))


def _round_coord(value: float) -> float:
    return round(float(value), _COORD_DECIMALS)


def _rf_cache_key(rf_key: str, lat_a: float, lon_a: float, lat_b: float, lon_b: float) -> str:
    a = (_round_coord(lat_a), _round_coord(lon_a))
    b = (_round_coord(lat_b), _round_coord(lon_b))
    if a <= b:
        pair = f"{a[0]},{a[1]}|{b[0]},{b[1]}"
    else:
        pair = f"{b[0]},{b[1]}|{a[0]},{a[1]}"
    return f"{rf_key}:{pair}"


def _canonical_slug_pair(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a <= b else (b, a)


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
    rf_json = rf_json_for_preset(preset)
    rf_key = hashlib.sha256(rf_json.encode()).hexdigest()[:16]
    max_hop_m = max_hop_range_m(preset)
    features: list[dict[str, Any]] = []
    pending: list[tuple[str, float, float, str, str]] = []

    for slug, site in sorted(preset.sites.items()):
        if slug == from_slug:
            continue
        lat_b = float(site.lat)
        lon_b = float(site.lon)
        if _haversine_m(from_lat, from_lon, lat_b, lon_b) > max_hop_m:
            continue
        cache_key = _rf_cache_key(rf_key, from_lat, from_lon, lat_b, lon_b)
        cached = _rf_link_cache.get(cache_key)
        if cached is True:
            label_b = site.name.strip() or slug
            slug_a, slug_b = _canonical_slug_pair(from_slug, slug)
            features.append(
                _line_feature(
                    slug_a=slug_a,
                    slug_b=slug_b,
                    lon_a=from_lon if slug_a == from_slug else lon_b,
                    lat_a=from_lat if slug_a == from_slug else lat_b,
                    lon_b=lon_b if slug_b == slug else from_lon,
                    lat_b=lat_b if slug_b == slug else from_lat,
                    label=f"{from_label} ↔ {label_b}",
                )
            )
            continue
        if cached is False:
            continue
        label_b = site.name.strip() or slug
        pending.append((slug, lat_b, lon_b, label_b, cache_key))

    if pending:
        session = splatter_session(verbose=False)
        points = [(from_lat, from_lon)]
        points.extend((lat_b, lon_b) for _, lat_b, lon_b, _, _ in pending)
        ensure_dem_for_points(session, points, buffer_m=5000.0)
        pairs = [(from_lat, from_lon, lat_b, lon_b) for _, lat_b, lon_b, _, _ in pending]
        viable = mutual_hop_batch(session, pairs, rf_json=rf_json)
        for (slug, lat_b, lon_b, label_b, cache_key), ok in zip(pending, viable, strict=True):
            _rf_link_cache[cache_key] = bool(ok)
            if not ok:
                continue
            slug_a, slug_b = _canonical_slug_pair(from_slug, slug)
            features.append(
                _line_feature(
                    slug_a=slug_a,
                    slug_b=slug_b,
                    lon_a=from_lon if slug_a == from_slug else lon_b,
                    lat_a=from_lat if slug_a == from_slug else lat_b,
                    lon_b=lon_b if slug_b == slug else from_lon,
                    lat_b=lat_b if slug_b == slug else from_lat,
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
