"""On-demand mutual site link checks for ``peaky serve``."""

from __future__ import annotations

import math
import threading
from itertools import combinations
from pathlib import Path
from typing import Mapping

from pydantic import ValidationError

from peaky_finders.site_suggestions.rf_link import (
    ensure_dem_for_points,
    max_hop_range_m,
    mutual_hop_batch,
    mutual_hop_viable,
    rf_json_for_preset,
    splatter_session,
)
from peaky_finders.sites_job import Preset, SiteEntry, load_preset_for_coverage
from peaky_finders.viewshed_links import manual_link_slug_pairs

_links_eval_lock = threading.Lock()


class ServeLinksError(Exception):
    """Site link evaluation failed for serve."""


def canonical_site_pair(slug_a: str, slug_b: str) -> tuple[str, str]:
    a, b = str(slug_a).strip(), str(slug_b).strip()
    return (a, b) if a <= b else (b, a)


def _load_links_preset(project_dir: Path) -> Preset:
    try:
        return load_preset_for_coverage(project_dir / "config.yaml")
    except (ValueError, ValidationError) as e:
        raise ServeLinksError(f"invalid preset: {e}") from e


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _pair_within_hop_range(
    preset: Preset,
    *,
    lat_a: float,
    lon_a: float,
    lat_b: float,
    lon_b: float,
) -> bool:
    return _haversine_m(lat_a, lon_a, lat_b, lon_b) <= max_hop_range_m(preset)


def _manual_link_pairs(preset: Preset) -> set[tuple[str, str]]:
    return set(manual_link_slug_pairs(preset.links))


def _link_record(
    *,
    slug_a: str,
    slug_b: str,
    linked: bool,
    manual: bool,
) -> dict[str, object]:
    a, b = canonical_site_pair(slug_a, slug_b)
    return {"a": a, "b": b, "linked": linked, "manual": manual}


def _line_feature(
    *,
    slug_a: str,
    slug_b: str,
    site_a: SiteEntry,
    site_b: SiteEntry,
    manual: bool,
) -> dict[str, object]:
    a, b = canonical_site_pair(slug_a, slug_b)
    return {
        "type": "Feature",
        "geometry": {
            "type": "LineString",
            "coordinates": [
                [float(site_a.lon), float(site_a.lat)],
                [float(site_b.lon), float(site_b.lat)],
            ],
        },
        "properties": {"a": a, "b": b, "manual": manual},
    }


def evaluate_site_pair_linked(
    project_dir: Path,
    slug_a: str,
    slug_b: str,
    sites: Mapping[str, SiteEntry],
    *,
    verbose: bool = False,
) -> dict[str, object]:
    """Return whether two sites share a mutual RF hop or preset ``links`` entry."""
    a_slug, b_slug = canonical_site_pair(slug_a, slug_b)
    if a_slug == b_slug:
        return _link_record(slug_a=a_slug, slug_b=b_slug, linked=False, manual=False)

    if a_slug not in sites or b_slug not in sites:
        raise ServeLinksError(f"unknown site slug(s): {slug_a!r}, {slug_b!r}")

    preset = _load_links_preset(project_dir)
    if preset.land is None:
        raise ServeLinksError("preset land.* required for RF link checks")

    if (a_slug, b_slug) in _manual_link_pairs(preset):
        return _link_record(slug_a=a_slug, slug_b=b_slug, linked=True, manual=True)

    site_a = sites[a_slug]
    site_b = sites[b_slug]
    lat_a, lon_a = float(site_a.lat), float(site_a.lon)
    lat_b, lon_b = float(site_b.lat), float(site_b.lon)
    if not _pair_within_hop_range(preset, lat_a=lat_a, lon_a=lon_a, lat_b=lat_b, lon_b=lon_b):
        return _link_record(slug_a=a_slug, slug_b=b_slug, linked=False, manual=False)

    rf_json = rf_json_for_preset(preset)
    max_hop_m = max_hop_range_m(preset)
    with _links_eval_lock:
        session = splatter_session(verbose=verbose)
        if verbose:
            print(f"serve links: pair {a_slug} ↔ {b_slug}", flush=True)
        ensure_dem_for_points(
            session,
            [(lat_a, lon_a), (lat_b, lon_b)],
            buffer_m=max_hop_m * 0.05 + 5000.0,
        )
        linked = mutual_hop_viable(
            session,
            lat_a=lat_a,
            lon_a=lon_a,
            lat_b=lat_b,
            lon_b=lon_b,
            rf_json=rf_json,
        )
    return _link_record(slug_a=a_slug, slug_b=b_slug, linked=linked, manual=False)


def load_project_site_links(
    project_dir: Path,
    sites: Mapping[str, SiteEntry],
    *,
    verbose: bool = False,
) -> dict[str, object]:
    """Evaluate all site pairs and return linked pairs plus GeoJSON line features."""
    preset = _load_links_preset(project_dir)
    if preset.land is None:
        raise ServeLinksError("preset land.* required for RF link checks")

    manual_pairs = _manual_link_pairs(preset)
    slug_list = sorted(sites.keys())
    records: list[dict[str, object]] = []
    features: list[dict[str, object]] = []
    rf_pairs: list[tuple[str, str, float, float, float, float]] = []

    for slug_a, slug_b in combinations(slug_list, 2):
        key = canonical_site_pair(slug_a, slug_b)
        if key in manual_pairs:
            records.append(_link_record(slug_a=slug_a, slug_b=slug_b, linked=True, manual=True))
            features.append(
                _line_feature(
                    slug_a=slug_a,
                    slug_b=slug_b,
                    site_a=sites[slug_a],
                    site_b=sites[slug_b],
                    manual=True,
                )
            )
            continue

        site_a = sites[slug_a]
        site_b = sites[slug_b]
        lat_a, lon_a = float(site_a.lat), float(site_a.lon)
        lat_b, lon_b = float(site_b.lat), float(site_b.lon)
        if not _pair_within_hop_range(preset, lat_a=lat_a, lon_a=lon_a, lat_b=lat_b, lon_b=lon_b):
            continue
        rf_pairs.append((slug_a, slug_b, lat_a, lon_a, lat_b, lon_b))

    if rf_pairs:
        rf_json = rf_json_for_preset(preset)
        max_hop_m = max_hop_range_m(preset)
        points = [(lat_a, lon_a) for _, _, lat_a, lon_a, _, _ in rf_pairs]
        points.extend((lat_b, lon_b) for _, _, _, _, lat_b, lon_b in rf_pairs)
        coord_pairs = [(lat_a, lon_a, lat_b, lon_b) for _, _, lat_a, lon_a, lat_b, lon_b in rf_pairs]

        with _links_eval_lock:
            session = splatter_session(verbose=verbose)
            if verbose:
                print(
                    f"serve links: {len(rf_pairs)} RF pair(s), {len(slug_list)} site(s)",
                    flush=True,
                )
            ensure_dem_for_points(
                session,
                points,
                buffer_m=max_hop_m * 0.05 + 5000.0,
            )
            viable = mutual_hop_batch(session, coord_pairs, rf_json=rf_json)

        if len(viable) != len(rf_pairs):
            raise ServeLinksError(
                f"RF batch length mismatch: {len(viable)} results for {len(rf_pairs)} pair(s)"
            )

        for (slug_a, slug_b, lat_a, lon_a, lat_b, lon_b), ok in zip(rf_pairs, viable):
            if not ok:
                continue
            records.append(_link_record(slug_a=slug_a, slug_b=slug_b, linked=True, manual=False))
            features.append(
                _line_feature(
                    slug_a=slug_a,
                    slug_b=slug_b,
                    site_a=sites[slug_a],
                    site_b=sites[slug_b],
                    manual=False,
                )
            )

    records.sort(key=lambda row: (str(row["a"]), str(row["b"])))
    return {
        "links": records,
        "geojson": {"type": "FeatureCollection", "features": features},
    }
