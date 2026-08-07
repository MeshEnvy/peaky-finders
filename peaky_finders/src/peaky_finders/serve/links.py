"""On-demand mutual site link checks for ``peaky serve``."""

from __future__ import annotations

import json
import math
import threading
from itertools import combinations
from pathlib import Path
from typing import Mapping

from pydantic import ValidationError
from shapely.geometry.base import BaseGeometry

from peaky_finders.core.links.manual import manual_link_slug_pairs
from peaky_finders.core.links.viewshed import (
    footprint_covers_point,
    load_coords_viewshed_footprint,
    load_site_viewshed_footprint,
    mutual_viewshed_link,
)
from peaky_finders.core.preset import (
    Preset,
    SiteEntry,
    load_preset_for_coverage,
)
from peaky_finders.core.preset.paths import resolved_preset_cache_dir
from peaky_finders.core.rf.mapping import resolved_site_tx_height_m
from peaky_finders.serve.viewshed_engine import get_viewshed_engine
from peaky_finders.serve.viewshed_sim import SERVE_VIEWSHED_PREVIEW_RASTER_DIMENSION, ViewshedSimOverrides

_links_eval_lock = threading.Lock()

# Ready payloads from background warm — GET must stay fast (no GPKG I/O).
# Keyed by link-relevant fingerprint (coords / sim / manual pairs), not config mtime —
# renaming a site must not drop the mesh until a needless re-warm finishes.
_links_payload_cache: dict[str, tuple[object, dict[str, object]]] = {}
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


def _links_disk_cache_path(project_dir: Path) -> Path:
    return resolved_preset_cache_dir(project_dir / "config.yaml") / "links" / "mesh.json"


def _fingerprint_json(fingerprint: tuple[object, ...]) -> str:
    return json.dumps(fingerprint, default=str, separators=(",", ":"))


def _write_disk_links_cache(
    project_dir: Path,
    fingerprint: tuple[object, ...],
    payload: dict[str, object],
) -> None:
    path = _links_disk_cache_path(project_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(
                {"fingerprint": _fingerprint_json(fingerprint), "payload": payload},
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        tmp.replace(path)
    except OSError:
        pass


def _read_disk_links_cache(
    project_dir: Path,
    fingerprint: tuple[object, ...],
) -> dict[str, object] | None:
    path = _links_disk_cache_path(project_dir)
    if not path.is_file():
        return None
    try:
        wire = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(wire, dict):
        return None
    if wire.get("fingerprint") != _fingerprint_json(fingerprint):
        return None
    payload = wire.get("payload")
    if not isinstance(payload, dict):
        return None
    if payload.get("status") != "ready":
        return None
    return payload


def _delete_disk_links_cache(project_dir: Path) -> None:
    path = _links_disk_cache_path(project_dir)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def links_input_fingerprint(
    sites: Mapping[str, SiteEntry],
    *,
    preset: Preset,
) -> tuple[object, ...]:
    """Stable key for when RF/manual link geometry can change.

    Ignores display-only preset edits (site name, tags, comments).
    """
    sim = preset.simulation
    site_rows = tuple(
        sorted(
            (
                str(slug),
                round(float(site.lat), 7),
                round(float(site.lon), 7),
                round(resolved_site_tx_height_m(preset, site), 3),
            )
            for slug, site in sites.items()
        )
    )
    manual = tuple(sorted(_manual_link_pairs(preset)))
    modem = sim.modem if isinstance(sim.modem, str) else repr(sim.modem)
    environment = (
        sim.environment if isinstance(sim.environment, str) else repr(sim.environment)
    )
    tx = sim.transmitter if isinstance(sim.transmitter, dict) else {}
    rx = sim.receiver if isinstance(sim.receiver, dict) else {}
    return (
        float(sim.radius_km),
        int(sim.raster_dimension),
        str(modem or ""),
        str(environment or ""),
        float(tx.get("height_m", 0.0) or 0.0),
        float(rx.get("height_m", 0.0) or 0.0),
        site_rows,
        manual,
    )


def store_project_site_links_cache(
    project_dir: Path,
    sites: Mapping[str, SiteEntry],
    payload: dict[str, object],
    *,
    preset: Preset | None = None,
) -> None:
    """Remember a warm-computed links payload for fast GET responses."""
    if payload.get("status") != "ready":
        return
    features = payload.get("geojson")
    feature_count = (
        len(features.get("features", []))  # type: ignore[union-attr]
        if isinstance(features, dict)
        else 0
    )
    linked_count = sum(
        1
        for row in payload.get("links", [])
        if isinstance(row, dict) and row.get("linked") is not False
    )
    if feature_count == 0 and linked_count == 0:
        return
    preset = preset or _load_links_preset(project_dir)
    key = _project_cache_key(project_dir)
    entry = (links_input_fingerprint(sites, preset=preset), payload)
    with _links_payload_cache_lock:
        _links_payload_cache[key] = entry
    _write_disk_links_cache(project_dir, entry[0], payload)


def reset_project_site_links_cache_for_tests() -> None:
    with _links_payload_cache_lock:
        _links_payload_cache.clear()


def invalidate_project_site_links_cache(project_dir: Path) -> None:
    """Drop warm cache so the next GET does not serve a stale partial mesh."""
    key = _project_cache_key(project_dir)
    with _links_payload_cache_lock:
        _links_payload_cache.pop(key, None)
    _delete_disk_links_cache(project_dir)


def _cached_project_site_links(
    project_dir: Path,
    sites: Mapping[str, SiteEntry],
    *,
    preset: Preset | None = None,
) -> dict[str, object] | None:
    preset = preset or _load_links_preset(project_dir)
    key = _project_cache_key(project_dir)
    fingerprint = links_input_fingerprint(sites, preset=preset)
    with _links_payload_cache_lock:
        hit = _links_payload_cache.get(key)
        if hit is not None:
            cached_fp, payload = hit
            if cached_fp == fingerprint and payload.get("status") == "ready":
                return payload
    disk = _read_disk_links_cache(project_dir, fingerprint)
    if disk is not None:
        with _links_payload_cache_lock:
            _links_payload_cache[key] = (fingerprint, disk)
        return disk
    return None


def get_cached_project_site_links(
    project_dir: Path,
    sites: Mapping[str, SiteEntry],
    *,
    preset: Preset | None = None,
) -> dict[str, object] | None:
    """Return warm-cached ready payload when link inputs are unchanged."""
    return _cached_project_site_links(project_dir, sites, preset=preset)


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
    strength: str = "strong",
) -> dict[str, object]:
    a, b = canonical_site_pair(slug_a, slug_b)
    return {"a": a, "b": b, "linked": linked, "manual": manual, "strength": strength}


def _pair_link_strength(
    fp_a: BaseGeometry | None,
    fp_b: BaseGeometry | None,
    *,
    lat_a: float,
    lon_a: float,
    lat_b: float,
    lon_b: float,
) -> str | None:
    """``strong`` when mutual cover, ``weak`` when one-way, else ``None``."""
    a_sees_b = fp_a is not None and footprint_covers_point(fp_a, lat=lat_b, lon=lon_b)
    b_sees_a = fp_b is not None and footprint_covers_point(fp_b, lat=lat_a, lon=lon_a)
    if a_sees_b and b_sees_a:
        return "strong"
    if a_sees_b or b_sees_a:
        return "weak"
    return None


def _line_feature(
    *,
    slug_a: str,
    slug_b: str,
    site_a: SiteEntry,
    site_b: SiteEntry,
    manual: bool,
    strength: str = "strong",
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
        "properties": {
            "a": a,
            "b": b,
            "manual": manual,
            "strength": strength,
            "distance_km": distance_km,
        },
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
    analysis_complete: bool = False,
) -> dict[str, object]:
    """Build link records from preset manual pairs and viewshed footprints.

    When *analysis_complete* is true (warm finished a footprint pass), status is
    ``ready`` even if some sites still lack footprints — missing sites simply
    contribute no RF edges until their viewsheds exist.
    """
    preset = preset or _load_links_preset(project_dir)
    manual_pairs = _manual_link_pairs(preset)
    slug_list = sorted(sites.keys())
    fp = footprints if footprints is not None else _read_cached_footprints(project_dir, preset, sites)

    records: list[dict[str, object]] = []
    features: list[dict[str, object]] = []

    for slug_a, slug_b in combinations(slug_list, 2):
        key = canonical_site_pair(slug_a, slug_b)
        if key in manual_pairs:
            records.append(
                _link_record(slug_a=slug_a, slug_b=slug_b, linked=True, manual=True, strength="strong")
            )
            features.append(
                _line_feature(
                    slug_a=slug_a,
                    slug_b=slug_b,
                    site_a=sites[slug_a],
                    site_b=sites[slug_b],
                    manual=True,
                    strength="strong",
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
        strength = _pair_link_strength(
            fp_a,
            fp_b,
            lat_a=lat_a,
            lon_a=lon_a,
            lat_b=lat_b,
            lon_b=lon_b,
        )
        if strength is None:
            continue

        records.append(
            _link_record(
                slug_a=slug_a,
                slug_b=slug_b,
                linked=True,
                manual=False,
                strength=strength,
            )
        )
        features.append(
            _line_feature(
                slug_a=slug_a,
                slug_b=slug_b,
                site_a=site_a,
                site_b=site_b,
                manual=False,
                strength=strength,
            )
        )

    records.sort(key=lambda row: (str(row["a"]), str(row["b"])))
    if analysis_complete:
        status = "ready"
    else:
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
    preset = _load_links_preset(project_dir)
    cached = _cached_project_site_links(project_dir, sites, preset=preset)
    if cached is not None:
        return cached
    return compute_project_site_links(
        project_dir,
        sites,
        preset=preset,
        footprints={slug: None for slug in sites},
    )


def compute_single_site_links(
    project_dir: Path,
    site_slug: str,
    sites: Mapping[str, SiteEntry],
    *,
    preset: Preset | None = None,
) -> dict[str, object]:
    """Evaluate one site's links against in-range neighbors using existing footprints only.

    Never runs splatter and never opens GPKGs for out-of-range sites, so it stays
    fast on large projects. Neighbors without a cached footprint are listed in
    ``missing`` instead of blocking.
    """
    slug = str(site_slug).strip()
    if slug not in sites:
        raise ServeLinksError(f"unknown site slug: {site_slug!r}")

    preset = preset or _load_links_preset(project_dir)
    manual_pairs = _manual_link_pairs(preset)
    center = sites[slug]
    lat_c, lon_c = float(center.lat), float(center.lon)

    engine = get_viewshed_engine()
    sim = ViewshedSimOverrides()
    center_fp = engine.read_site_footprint(project_dir, preset, center, sim=sim)
    if center_fp is None:
        center_fp = engine.vectorize_site_footprint(project_dir, preset, center, sim=sim)

    records: list[dict[str, object]] = []
    features: list[dict[str, object]] = []
    missing: list[str] = []

    for other_slug, other in sorted(sites.items()):
        if other_slug == slug:
            continue
        pair = canonical_site_pair(slug, other_slug)
        lat_o, lon_o = float(other.lat), float(other.lon)
        if pair in manual_pairs:
            records.append(
                _link_record(slug_a=slug, slug_b=other_slug, linked=True, manual=True, strength="strong")
            )
            features.append(
                _line_feature(
                    slug_a=slug,
                    slug_b=other_slug,
                    site_a=center,
                    site_b=other,
                    manual=True,
                    strength="strong",
                )
            )
            continue
        if not _pair_within_hop_range(preset, lat_a=lat_c, lon_a=lon_c, lat_b=lat_o, lon_b=lon_o):
            continue
        if center_fp is None:
            missing.append(other_slug)
            continue
        other_fp = engine.read_site_footprint(project_dir, preset, other, sim=sim)
        strength = _pair_link_strength(
            center_fp,
            other_fp,
            lat_a=lat_c,
            lon_a=lon_c,
            lat_b=lat_o,
            lon_b=lon_o,
        )
        if strength is None:
            continue
        records.append(
            _link_record(
                slug_a=slug,
                slug_b=other_slug,
                linked=True,
                manual=False,
                strength=strength,
            )
        )
        features.append(
            _line_feature(
                slug_a=slug,
                slug_b=other_slug,
                site_a=center,
                site_b=other,
                manual=False,
                strength=strength,
            )
        )

    records.sort(key=lambda row: (str(row["a"]), str(row["b"])))
    outbound_ready = center_fp is not None
    return {
        "status": "ready" if outbound_ready else "partial",
        "site": slug,
        "center_footprint": center_fp is not None,
        "outbound_ready": outbound_ready,
        "links": records,
        "geojson": {"type": "FeatureCollection", "features": features},
        "missing": sorted(missing),
    }


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
        return _link_record(slug_a=a_slug, slug_b=b_slug, linked=True, manual=True, strength="strong")

    site_a = sites[a_slug]
    site_b = sites[b_slug]
    lat_a, lon_a = float(site_a.lat), float(site_a.lon)
    lat_b, lon_b = float(site_b.lat), float(site_b.lon)
    if not _pair_within_hop_range(preset, lat_a=lat_a, lon_a=lon_a, lat_b=lat_b, lon_b=lon_b):
        return _link_record(slug_a=a_slug, slug_b=b_slug, linked=False, manual=False, strength="strong")

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
        strength = _pair_link_strength(
            fp_a,
            fp_b,
            lat_a=lat_a,
            lon_a=lon_a,
            lat_b=lat_b,
            lon_b=lon_b,
        )
        linked = strength is not None
        link_strength = strength if strength is not None else "strong"
    return _link_record(slug_a=a_slug, slug_b=b_slug, linked=linked, manual=False, strength=link_strength)


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
    preview_sim = ViewshedSimOverrides(raster_dimension=SERVE_VIEWSHED_PREVIEW_RASTER_DIMENSION)
    records: list[dict[str, object]] = []
    with _links_eval_lock:
        if verbose:
            print(
                f"serve links prefetch: {len(candidates)} viewshed pair(s) at ({lat:.6f}, {lon:.6f})",
                flush=True,
            )
        draft_fp = engine.read_coords_footprint(
            project_dir,
            preset,
            lat=lat,
            lon=lon,
            sim=preview_sim,
            verbose=verbose,
        )
        if draft_fp is None:
            draft_fp = engine.ensure_coords_footprint(
                project_dir,
                preset,
                lat=lat,
                lon=lon,
                sim=preview_sim,
                verbose=verbose,
            )
        for slug, site_lat, site_lon in candidates:
            site_fp = engine.read_site_footprint(
                project_dir,
                preset,
                sites[slug],
                verbose=verbose,
            )
            if site_fp is None:
                site_fp = engine.ensure_site_footprint(
                    project_dir,
                    preset,
                    slug,
                    sites[slug],
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
