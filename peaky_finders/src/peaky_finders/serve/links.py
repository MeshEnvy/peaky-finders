"""On-demand mutual site link checks for ``peaky serve``."""

from __future__ import annotations

import math
import threading
from itertools import combinations
from pathlib import Path
from typing import Mapping

from pydantic import ValidationError
from shapely.geometry.base import BaseGeometry

from peaky_finders.core.links.manual import manual_link_slug_pairs
from peaky_finders.core.links.viewshed import (
    load_coords_viewshed_footprint,
    load_site_viewshed_footprint,
    mutual_viewshed_link,
)
from peaky_finders.core.preset import (
    Preset,
    SiteEntry,
    load_preset_for_coverage,
)
from peaky_finders.serve.viewshed_engine import get_viewshed_engine
from peaky_finders.serve.viewshed_sim import ViewshedSimOverrides

_links_eval_lock = threading.Lock()

# Ready payloads from background warm — GET must stay fast (no GPKG I/O).
_links_payload_cache: dict[str, tuple[float, frozenset[str], dict[str, object]]] = {}
_links_payload_cache_lock = threading.Lock()


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


def _project_cache_key(project_dir: Path) -> str:
    return str(Path(project_dir).expanduser().resolve())


def _config_mtime(project_dir: Path) -> float:
    try:
        return (Path(project_dir) / "config.yaml").stat().st_mtime
    except OSError:
        return 0.0


def store_project_site_links_cache(
    project_dir: Path,
    sites: Mapping[str, SiteEntry],
    payload: dict[str, object],
) -> None:
    """Remember a warm-computed links payload for fast GET responses."""
    key = _project_cache_key(project_dir)
    entry = (_config_mtime(project_dir), frozenset(sites.keys()), payload)
    with _links_payload_cache_lock:
        _links_payload_cache[key] = entry


def reset_project_site_links_cache_for_tests() -> None:
    with _links_payload_cache_lock:
        _links_payload_cache.clear()


def _cached_project_site_links(
    project_dir: Path,
    sites: Mapping[str, SiteEntry],
) -> dict[str, object] | None:
    key = _project_cache_key(project_dir)
    mtime = _config_mtime(project_dir)
    site_keys = frozenset(sites.keys())
    with _links_payload_cache_lock:
        hit = _links_payload_cache.get(key)
        if hit is None:
            return None
        cached_mtime, cached_sites, payload = hit
        if cached_mtime != mtime or cached_sites != site_keys:
            return None
        if payload.get("status") != "ready":
            return None
        return payload


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _max_hop_range_m(preset: Preset) -> float:
    return float(preset.simulation.radius_km) * 1000.0


def _pair_within_hop_range(
    preset: Preset,
    *,
    lat_a: float,
    lon_a: float,
    lat_b: float,
    lon_b: float,
) -> bool:
    return _haversine_m(lat_a, lon_a, lat_b, lon_b) <= _max_hop_range_m(preset)


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
    lat_a, lon_a = float(site_a.lat), float(site_a.lon)
    lat_b, lon_b = float(site_b.lat), float(site_b.lon)
    distance_km = round(_haversine_m(lat_a, lon_a, lat_b, lon_b) / 1000.0, 1)
    return {
        "type": "Feature",
        "geometry": {
            "type": "LineString",
            "coordinates": [
                [lon_a, lat_a],
                [lon_b, lat_b],
            ],
        },
        "properties": {"a": a, "b": b, "manual": manual, "distance_km": distance_km},
    }


def _read_cached_footprints(
    project_dir: Path,
    preset: Preset,
    sites: Mapping[str, SiteEntry],
) -> dict[str, BaseGeometry | None]:
    """Read existing fresh footprints only — never vectorize on the request thread."""
    preset_path = project_dir / "config.yaml"
    return {
        slug: load_site_viewshed_footprint(
            preset_path, preset, site, ensure=False
        )
        for slug, site in sites.items()
    }


def _viewshed_footprints_complete(
    footprints: Mapping[str, BaseGeometry | None],
    sites: Mapping[str, SiteEntry],
) -> bool:
    return all(footprints.get(slug) is not None for slug in sites)


def compute_project_site_links(
    project_dir: Path,
    sites: Mapping[str, SiteEntry],
    *,
    preset: Preset | None = None,
    footprints: Mapping[str, BaseGeometry | None] | None = None,
) -> dict[str, object]:
    """Build link records from preset manual pairs and viewshed footprints."""
    preset = preset or _load_links_preset(project_dir)
    manual_pairs = _manual_link_pairs(preset)
    slug_list = sorted(sites.keys())
    fp = footprints if footprints is not None else _read_cached_footprints(project_dir, preset, sites)

    records: list[dict[str, object]] = []
    features: list[dict[str, object]] = []

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

        fp_a = fp.get(slug_a)
        fp_b = fp.get(slug_b)
        if fp_a is None or fp_b is None:
            continue
        if not mutual_viewshed_link(
            fp_a,
            fp_b,
            lat_a=lat_a,
            lon_a=lon_a,
            lat_b=lat_b,
            lon_b=lon_b,
        ):
            continue

        records.append(_link_record(slug_a=slug_a, slug_b=slug_b, linked=True, manual=False))
        features.append(
            _line_feature(
                slug_a=slug_a,
                slug_b=slug_b,
                site_a=site_a,
                site_b=site_b,
                manual=False,
            )
        )

    records.sort(key=lambda row: (str(row["a"]), str(row["b"])))
    status = "ready" if _viewshed_footprints_complete(fp, sites) else "pending"
    return {
        "status": status,
        "links": records,
        "geojson": {"type": "FeatureCollection", "features": features},
    }


def load_project_site_links(
    project_dir: Path,
    sites: Mapping[str, SiteEntry],
    *,
    verbose: bool = False,
) -> dict[str, object]:
    """Return warm-cached links, or a fast manual-only ``pending`` payload.

    Must not read ``splat.gpkg`` on the request thread — Nevada-scale footprint I/O
    exceeds Waitress ``channel_timeout`` and drops the response. Viewshed pairs are
    filled by ``warm_project_site_links`` + SSE.
    """
    del verbose
    cached = _cached_project_site_links(project_dir, sites)
    if cached is not None:
        return cached
    return compute_project_site_links(
        project_dir,
        sites,
        footprints={slug: None for slug in sites},
    )


def evaluate_site_pair_linked(
    project_dir: Path,
    slug_a: str,
    slug_b: str,
    sites: Mapping[str, SiteEntry],
    *,
    verbose: bool = False,
) -> dict[str, object]:
    """Return whether two sites share a mutual viewshed link or preset ``links`` entry."""
    a_slug, b_slug = canonical_site_pair(slug_a, slug_b)
    if a_slug == b_slug:
        return _link_record(slug_a=a_slug, slug_b=b_slug, linked=False, manual=False)

    if a_slug not in sites or b_slug not in sites:
        raise ServeLinksError(f"unknown site slug(s): {slug_a!r}, {slug_b!r}")

    preset = _load_links_preset(project_dir)

    if (a_slug, b_slug) in _manual_link_pairs(preset):
        return _link_record(slug_a=a_slug, slug_b=b_slug, linked=True, manual=True)

    site_a = sites[a_slug]
    site_b = sites[b_slug]
    lat_a, lon_a = float(site_a.lat), float(site_a.lon)
    lat_b, lon_b = float(site_b.lat), float(site_b.lon)
    if not _pair_within_hop_range(preset, lat_a=lat_a, lon_a=lon_a, lat_b=lat_b, lon_b=lon_b):
        return _link_record(slug_a=a_slug, slug_b=b_slug, linked=False, manual=False)

    engine = get_viewshed_engine()
    sim = ViewshedSimOverrides()
    with _links_eval_lock:
        if verbose:
            print(f"serve links: viewshed pair {a_slug} ↔ {b_slug}", flush=True)
        fp_a = engine.ensure_site_footprint(
            project_dir, preset, a_slug, site_a, sim=sim, verbose=verbose
        )
        fp_b = engine.ensure_site_footprint(
            project_dir, preset, b_slug, site_b, sim=sim, verbose=verbose
        )
        linked = mutual_viewshed_link(
            fp_a,
            fp_b,
            lat_a=lat_a,
            lon_a=lon_a,
            lat_b=lat_b,
            lon_b=lon_b,
        )
    return _link_record(slug_a=a_slug, slug_b=b_slug, linked=linked, manual=False)


def load_coords_site_links(
    project_dir: Path,
    lat: float,
    lon: float,
    sites: Mapping[str, SiteEntry],
    *,
    exclude_site_slug: str | None = None,
    verbose: bool = False,
) -> list[dict[str, object]]:
    """Evaluate viewshed links from draft coordinates to each in-range site."""
    if not (-90.0 <= lat <= 90.0):
        raise ServeLinksError(f"lat out of bounds: {lat}")
    if not (-180.0 <= lon <= 180.0):
        raise ServeLinksError(f"lon out of bounds: {lon}")

    preset = _load_links_preset(project_dir)
    exclude = str(exclude_site_slug).strip() if exclude_site_slug else ""
    candidates: list[tuple[str, float, float]] = []
    for slug, site in sorted(sites.items()):
        if exclude and slug == exclude:
            continue
        site_lat, site_lon = float(site.lat), float(site.lon)
        if not _pair_within_hop_range(
            preset,
            lat_a=lat,
            lon_a=lon,
            lat_b=site_lat,
            lon_b=site_lon,
        ):
            continue
        candidates.append((slug, site_lat, site_lon))

    if not candidates:
        return []

    engine = get_viewshed_engine()
    sim = ViewshedSimOverrides()
    records: list[dict[str, object]] = []
    with _links_eval_lock:
        if verbose:
            print(
                f"serve links prefetch: {len(candidates)} viewshed pair(s) at ({lat:.6f}, {lon:.6f})",
                flush=True,
            )
        draft_fp = engine.ensure_coords_footprint(
            project_dir,
            preset,
            lat=lat,
            lon=lon,
            sim=sim,
            verbose=verbose,
        )
        for slug, site_lat, site_lon in candidates:
            site_fp = engine.ensure_site_footprint(
                project_dir,
                preset,
                slug,
                sites[slug],
                sim=sim,
                verbose=verbose,
            )
            if not mutual_viewshed_link(
                draft_fp,
                site_fp,
                lat_a=lat,
                lon_a=lon,
                lat_b=site_lat,
                lon_b=site_lon,
            ):
                continue
            dist_km = _haversine_m(lat, lon, site_lat, site_lon) / 1000.0
            records.append(
                {
                    "slug": slug,
                    "linked": True,
                    "manual": False,
                    "distance_km": round(dist_km, 1),
                }
            )

    records.sort(key=lambda row: (float(row["distance_km"]), str(row["slug"])))
    return records
