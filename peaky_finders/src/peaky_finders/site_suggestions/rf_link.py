"""Pairwise RF link checks via in-process splatter session."""

from __future__ import annotations

import hashlib
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING, Sequence

from peaky_finders.preset_mapping import preset_to_request
from peaky_finders.site_suggestions.log import SuggestProgressTicker
from peaky_finders.sites_job import Preset, ensure_skadi_mirror_dir

if TYPE_CHECKING:
    from splatter import Session

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


def rf_mutual_link_slug_pairs_from_coords(
    preset: Preset,
    *,
    from_slug: str,
    from_lat: float,
    from_lon: float,
) -> list[tuple[str, str]]:
    """Mutual RF-viable slug pairs from arbitrary coordinates (e.g. goals) to RF sites."""
    rf_json = rf_json_for_preset(preset)
    rf_key = hashlib.sha256(rf_json.encode()).hexdigest()[:16]
    max_hop_m = max_hop_range_m(preset)
    out: list[tuple[str, str]] = []
    pending: list[tuple[str, float, float, str]] = []

    for slug, site in sorted(preset.sites.items()):
        if slug == from_slug or not site.participates_in_rf:
            continue
        lat_b = float(site.lat)
        lon_b = float(site.lon)
        if _haversine_m(from_lat, from_lon, lat_b, lon_b) > max_hop_m:
            continue
        cache_key = _rf_cache_key(rf_key, from_lat, from_lon, lat_b, lon_b)
        cached = _rf_link_cache.get(cache_key)
        if cached is True:
            out.append(_canonical_slug_pair(from_slug, slug))
            continue
        if cached is False:
            continue
        pending.append((slug, lat_b, lon_b, cache_key))

    if pending:
        session = splatter_session(verbose=False)
        points = [(from_lat, from_lon)]
        points.extend((lat_b, lon_b) for _, lat_b, lon_b, _ in pending)
        ensure_dem_for_points(session, points, buffer_m=5000.0)
        pairs = [(from_lat, from_lon, lat_b, lon_b) for _, lat_b, lon_b, _ in pending]
        viable = mutual_hop_batch(session, pairs, rf_json=rf_json)
        for (slug, _lat_b, _lon_b, cache_key), ok in zip(pending, viable, strict=True):
            _rf_link_cache[cache_key] = bool(ok)
            if ok:
                out.append(_canonical_slug_pair(from_slug, slug))

    return out


def rf_mutual_link_slug_pairs_from_site(
    preset: Preset,
    *,
    from_slug: str,
    from_lat: float,
    from_lon: float,
) -> list[tuple[str, str]]:
    """Mutual RF-viable slug pairs involving ``from_slug`` (canonical order)."""
    from_site = preset.sites.get(from_slug)
    if from_site is None or not from_site.participates_in_rf:
        return []
    return rf_mutual_link_slug_pairs_from_coords(
        preset,
        from_slug=from_slug,
        from_lat=from_lat,
        from_lon=from_lon,
    )


def rf_mutual_link_slug_pairs(preset: Preset) -> list[tuple[str, str]]:
    """All mutual RF-viable site slug pairs for a preset (canonical order)."""
    merged: set[tuple[str, str]] = set()
    for slug, site in sorted(preset.sites.items()):
        if not site.participates_in_rf:
            continue
        merged.update(
            rf_mutual_link_slug_pairs_from_site(
                preset,
                from_slug=slug,
                from_lat=float(site.lat),
                from_lon=float(site.lon),
            )
        )
    return sorted(merged)


def propagation_request_json(preset: Preset, *, lat: float = 0.0, lon: float = 0.0) -> str:
    """JSON propagation contract for splatter link evaluation (position is arbitrary)."""
    req = preset_to_request(preset, float(lat), float(lon))
    return req.model_dump_json(exclude_none=True)


def max_hop_range_m(preset: Preset) -> float:
    return float(preset.simulation.radius_km) * 1000.0


def splatter_session(*, verbose: bool = False) -> Session:
    from splatter import get_session

    mirror = str(ensure_skadi_mirror_dir())
    return get_session(mirror_root=mirror, verbose=verbose)


def ensure_dem_for_points(
    session: Session,
    points: Sequence[tuple[float, float]],
    *,
    buffer_m: float,
) -> None:
    if not points:
        return
    session.ensure_tiles_for_points(list(points), float(buffer_m))


def mutual_hop_viable(
    session: Session,
    *,
    lat_a: float,
    lon_a: float,
    lat_b: float,
    lon_b: float,
    rf_json: str,
) -> bool:
    return bool(
        session.link_mutual_viable(
            float(lat_a),
            float(lon_a),
            float(lat_b),
            float(lon_b),
            rf_json,
        )
    )


def mutual_hop_batch(
    session: Session,
    pairs: Sequence[tuple[float, float, float, float]],
    *,
    rf_json: str,
) -> list[bool]:
    if not pairs:
        return []
    return list(session.link_mutual_batch(list(pairs), rf_json))


def mutual_hop_batches(
    pairs: Sequence[tuple[float, float, float, float]],
    *,
    rf_json: str,
    chunk_size: int = 64,
    jobs: int = 1,
    verbose: bool = False,
) -> list[bool]:
    """Evaluate mutual-hop viability in fixed-size batches; parallel across batches when ``jobs > 1``."""
    if not pairs:
        return []
    size = max(1, int(chunk_size))
    chunks = [list(pairs[start : start + size]) for start in range(0, len(pairs), size)]
    workers = max(1, int(jobs))
    ticker = SuggestProgressTicker(verbose, label="RF hops", interval_s=0.5)
    if workers <= 1 or len(chunks) <= 1:
        session = splatter_session(verbose=verbose)
        out: list[bool] = []
        ticker.maybe(f"{len(pairs)} pair(s) in {len(chunks)} batch(es), workers=1", force=True)
        for chunk in chunks:
            out.extend(mutual_hop_batch(session, chunk, rf_json=rf_json))
            ticker.maybe(f"{len(out)}/{len(pairs)} evaluated")
        if verbose:
            ticker.done(f"{len(out)}/{len(pairs)} evaluated")
        return out

    def _worker(chunk: list[tuple[float, float, float, float]]) -> list[bool]:
        session = splatter_session(verbose=False)
        return mutual_hop_batch(session, chunk, rf_json=rf_json)

    mx = min(workers, len(chunks))
    ticker.maybe(
        f"{len(pairs)} pair(s) in {len(chunks)} batch(es), workers={mx}",
        force=True,
    )
    results: list[list[bool] | None] = [None] * len(chunks)
    done = 0
    with ThreadPoolExecutor(max_workers=mx) as pool:
        futs = {pool.submit(_worker, chunk): idx for idx, chunk in enumerate(chunks)}
        for fut in as_completed(futs):
            results[futs[fut]] = fut.result()
            done += 1
            evaluated = sum(len(chunk) for chunk in results if chunk is not None)
            ticker.maybe(f"{evaluated}/{len(pairs)} evaluated ({done}/{len(chunks)} batch(es))")
    if verbose:
        ticker.done(f"{len(pairs)} pair(s) evaluated")
    return [item for chunk in results if chunk is not None for item in chunk]


def rf_json_for_preset(preset: Preset) -> str:
    """Cached-friendly RF JSON keyed only on preset propagation (not position)."""
    return propagation_request_json(preset)
