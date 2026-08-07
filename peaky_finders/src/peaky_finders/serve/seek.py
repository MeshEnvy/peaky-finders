"""Goal-seek candidate peak API for ``peaky serve``."""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from pathlib import Path

import pyproj
from shapely.geometry import Point, box, mapping
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform

from peaky_finders.serve.eligible_land import EligibleLandError, load_or_build_eligible_geometry
from peaky_finders.core.links.rf import mutual_hop_batch, rf_json_for_preset, splatter_session
from peaky_finders.core.preset import Preset, load_preset_for_coverage
from peaky_finders.core.rf.mapping import preset_to_request
from peaky_finders.serve.seek_progress import (
    SeekScanHeartbeat,
    seek_scan_active,
    seek_scan_begin,
    seek_scan_clear,
    seek_scan_finish,
    seek_scan_update,
)
from peaky_finders.serve.seek_jobs import _SeekJob, get_seek_queue

SEEK_EXCLUDE_PROXIMITY_M = 100.0
SEEK_GOAL_DEDUP_M = 1500.0
SEEK_SITE_PEAK_DEDUP_M = 500.0
SEEK_PEAK_BIN_MIN_M = 500.0


class ServeSeekError(Exception):
    """Goal-seek candidate evaluation failed."""


class ServeSeekNotReadyError(ServeSeekError):
    """Viewshed footprint for the current hop is not ready yet."""


class ServeSeekCancelled(ServeSeekError):
    """Scan superseded by a newer goal-seek request."""


@dataclass(frozen=True)
class SeekPoint:
    lat: float
    lon: float


@dataclass(frozen=True)
class _RfCandidate:
    lon: float
    lat: float
    elev_m: float
    candidate_id: str
    is_goal: bool = False
    is_site: bool = False
    site_slug: str | None = None
    site_name: str | None = None


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dlambda = math.radians(lon2 - lon1)
    y = math.sin(dlambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def _parse_bbox(raw: str) -> tuple[float, float, float, float]:
    parts = [p.strip() for p in str(raw).split(",")]
    if len(parts) != 4:
        raise ServeSeekError("bbox must be west,south,east,north")
    west, south, east, north = (float(p) for p in parts)
    if west >= east or south >= north:
        raise ServeSeekError("bbox west<east and south<north required")
    return west, south, east, north


def _parse_exclude_points(raw: str | None) -> list[SeekPoint]:
    if not raw or not str(raw).strip():
        return []
    out: list[SeekPoint] = []
    for chunk in str(raw).split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = [p.strip() for p in chunk.split(",")]
        if len(parts) != 2:
            raise ServeSeekError("exclude must be lat,lon pairs separated by semicolons")
        out.append(SeekPoint(lat=float(parts[0]), lon=float(parts[1])))
    return out


def _parse_exclude_slugs(raw: str | None) -> set[str]:
    if not raw or not str(raw).strip():
        return set()
    return {part.strip() for part in str(raw).split(";") if part.strip()}


def _load_seek_preset(project_dir: Path) -> Preset:
    return load_preset_for_coverage(project_dir / "config.yaml")


def _pair_within_hop_range(preset: Preset, *, lat_a: float, lon_a: float, lat_b: float, lon_b: float) -> bool:
    return _haversine_m(lat_a, lon_a, lat_b, lon_b) <= float(preset.simulation.radius_km) * 1000.0


def _near_excluded(lat: float, lon: float, exclude: list[SeekPoint]) -> bool:
    for pt in exclude:
        if _haversine_m(lat, lon, pt.lat, pt.lon) <= SEEK_EXCLUDE_PROXIMITY_M:
            return True
    return False


def _hop_disc_wgs84(lat: float, lon: float, radius_m: float) -> BaseGeometry:
    aeqd = pyproj.CRS.from_proj4(
        f"+proj=aeqd +lat_0={lat} +lon_0={lon} +datum=WGS84 +units=m +no_defs"
    )
    wgs84 = pyproj.CRS.from_epsg(4326)
    to_aeqd = pyproj.Transformer.from_crs(wgs84, aeqd, always_xy=True)
    to_wgs84 = pyproj.Transformer.from_crs(aeqd, wgs84, always_xy=True)
    local = transform(to_aeqd.transform, Point(float(lon), float(lat)))
    return transform(to_wgs84.transform, local.buffer(float(radius_m)))


def _seek_search_geometry(
    *,
    eligible: BaseGeometry,
    from_lat: float,
    from_lon: float,
    radius_km: float,
    west: float,
    south: float,
    east: float,
    north: float,
) -> BaseGeometry:
    hop_m = float(radius_km) * 1000.0
    search = eligible.intersection(_hop_disc_wgs84(from_lat, from_lon, hop_m))
    if search.is_empty:
        return search
    return search.intersection(box(west, south, east, north))


def _candidate_line_feature(
    *,
    from_lat: float,
    from_lon: float,
    to_lat: float,
    to_lon: float,
    distance_km: float,
    bearing_deg: float,
    rf_viable: bool,
    elev_m: float,
    candidate_id: str,
    is_goal: bool = False,
    is_site: bool = False,
) -> dict[str, object]:
    props: dict[str, object] = {
        "candidate_id": candidate_id,
        "distance_km": round(distance_km, 1),
        "bearing_deg": round(bearing_deg, 0),
        "rf_viable": rf_viable,
        "elev_m": round(elev_m, 1),
    }
    if is_goal:
        props["is_goal"] = True
    if is_site:
        props["is_site"] = True
    return {
        "type": "Feature",
        "geometry": {
            "type": "LineString",
            "coordinates": [[from_lon, from_lat], [to_lon, to_lat]],
        },
        "properties": props,
    }


def _goal_point(goal_lon: float, goal_lat: float) -> Point:
    return Point(float(goal_lon), float(goal_lat))


def _goal_on_eligible(eligible: BaseGeometry, goal_lat: float, goal_lon: float) -> bool:
    if eligible.is_empty:
        return False
    return eligible.intersects(_goal_point(goal_lon, goal_lat))


def _goal_in_hop_range(
    preset: Preset,
    *,
    from_lat: float,
    from_lon: float,
    goal_lat: float,
    goal_lon: float,
) -> bool:
    return _pair_within_hop_range(
        preset,
        lat_a=from_lat,
        lon_a=from_lon,
        lat_b=goal_lat,
        lon_b=goal_lon,
    )


def _goal_hop_eligible(
    *,
    preset: Preset,
    eligible: BaseGeometry,
    from_lat: float,
    from_lon: float,
    goal_lat: float,
    goal_lon: float,
    exclude: list[SeekPoint],
) -> bool:
    """Whether an anonymous peak at the goal coords would pass seek filters."""
    if not _goal_in_hop_range(
        preset,
        from_lat=from_lat,
        from_lon=from_lon,
        goal_lat=goal_lat,
        goal_lon=goal_lon,
    ):
        return False
    if _near_excluded(goal_lat, goal_lon, exclude):
        return False
    return _goal_on_eligible(eligible, goal_lat, goal_lon)


def _goal_finish_eligible(
    preset: Preset,
    *,
    from_lat: float,
    from_lon: float,
    goal_lat: float,
    goal_lon: float,
) -> bool:
    """Preset end sites are always RF-tested when within one hop — not peak-scan rules."""
    return _goal_in_hop_range(
        preset,
        from_lat=from_lat,
        from_lon=from_lon,
        goal_lat=goal_lat,
        goal_lon=goal_lon,
    )


def _collect_reachable_site_rows(
    *,
    preset: Preset,
    eligible: BaseGeometry,
    from_lat: float,
    from_lon: float,
    goal_lat: float,
    goal_lon: float,
    exclude: list[SeekPoint],
    exclude_slugs: set[str],
) -> list[tuple[str, str, float, float, float]]:
    """Preset sites in hop range on eligible land — sorted toward goal."""
    rows: list[tuple[str, str, float, float, float, float]] = []
    for slug, site in preset.sites.items():
        if slug in exclude_slugs:
            continue
        lat, lon = site.lat, site.lon
        if not _pair_within_hop_range(preset, lat_a=from_lat, lon_a=from_lon, lat_b=lat, lon_b=lon):
            continue
        if _near_excluded(lat, lon, exclude):
            continue
        if not _goal_on_eligible(eligible, lat, lon):
            continue
        elev = float(site.height_m) if site.height_m is not None else 0.0
        dist_goal = _haversine_m(lat, lon, goal_lat, goal_lon)
        rows.append((slug, site.name, lon, lat, elev, dist_goal))
    rows.sort(key=lambda row: row[5])
    return [(slug, name, lon, lat, elev) for slug, name, lon, lat, elev, _ in rows]


def _dedupe_peaks_near_sites(
    peaks: list[tuple[float, float, float]],
    site_rows: list[tuple[str, str, float, float, float]],
) -> list[tuple[float, float, float]]:
    if not site_rows:
        return peaks
    kept: list[tuple[float, float, float]] = []
    for lon, lat, elev_m in peaks:
        near_site = False
        for _slug, _name, site_lon, site_lat, _elev in site_rows:
            if _haversine_m(site_lat, site_lon, lat, lon) <= SEEK_SITE_PEAK_DEDUP_M:
                near_site = True
                break
        if not near_site:
            kept.append((lon, lat, elev_m))
    return kept


def _goal_line_feature(
    *,
    from_lat: float,
    from_lon: float,
    goal_lat: float,
    goal_lon: float,
) -> dict[str, object]:
    distance_km = _haversine_m(from_lat, from_lon, goal_lat, goal_lon) / 1000.0
    bearing_deg = _bearing_deg(from_lat, from_lon, goal_lat, goal_lon)
    return {
        "type": "Feature",
        "geometry": {
            "type": "LineString",
            "coordinates": [[from_lon, from_lat], [goal_lon, goal_lat]],
        },
        "properties": {
            "distance_km": round(distance_km, 1),
            "bearing_deg": round(bearing_deg, 0),
            "kind": "goal",
        },
    }


def resolve_seek_peak_bin_size_m(seek_cfg: object, requested: float | None) -> float:
    """Clamp zoom-requested bin size to ``[500 m, preset seek.peak_bin_size_m]``."""
    ceiling = max(SEEK_PEAK_BIN_MIN_M, float(seek_cfg.peak_bin_size_m))
    if requested is None:
        return ceiling
    req = float(requested)
    if not math.isfinite(req) or req <= 0:
        raise ServeSeekError("peak_bin_size_m must be a positive number")
    return max(SEEK_PEAK_BIN_MIN_M, min(ceiling, req))


def load_seek_candidates(
    project_dir: Path,
    *,
    from_lat: float,
    from_lon: float,
    goal_lat: float,
    goal_lon: float,
    bbox: str,
    exclude_raw: str | None = None,
    exclude_slugs_raw: str | None = None,
    goal_elev_m: float | None = None,
    peak_bin_size_m: float | None = None,
    verbose: bool = False,
) -> dict[str, object]:
    prepared = _prepare_seek_request(
        project_dir,
        from_lat=from_lat,
        from_lon=from_lon,
        goal_lat=goal_lat,
        goal_lon=goal_lon,
        bbox=bbox,
        exclude_raw=exclude_raw,
        exclude_slugs_raw=exclude_slugs_raw,
        goal_elev_m=goal_elev_m,
        peak_bin_size_m=peak_bin_size_m,
    )
    scan_gen = seek_scan_begin(prepared.slug)
    try:
        return _load_seek_candidates_body(
            **prepared.body_kwargs,
            scan_gen=scan_gen,
            verbose=verbose,
        )
    finally:
        seek_scan_clear(prepared.slug, scan_gen)


def enqueue_seek_candidates(
    project_dir: Path,
    *,
    from_lat: float,
    from_lon: float,
    goal_lat: float,
    goal_lon: float,
    bbox: str,
    exclude_raw: str | None = None,
    exclude_slugs_raw: str | None = None,
    goal_elev_m: float | None = None,
    peak_bin_size_m: float | None = None,
    verbose: bool = False,
) -> int:
    """Queue goal-seek on a background worker; return the scan generation id."""
    prepared = _prepare_seek_request(
        project_dir,
        from_lat=from_lat,
        from_lon=from_lon,
        goal_lat=goal_lat,
        goal_lon=goal_lon,
        bbox=bbox,
        exclude_raw=exclude_raw,
        exclude_slugs_raw=exclude_slugs_raw,
        goal_elev_m=goal_elev_m,
        peak_bin_size_m=peak_bin_size_m,
    )
    scan_gen = seek_scan_begin(prepared.slug)

    def _run() -> None:
        slug = prepared.slug
        try:
            result = _load_seek_candidates_body(
                **prepared.body_kwargs,
                scan_gen=scan_gen,
                verbose=verbose,
            )
        except ServeSeekCancelled:
            seek_scan_finish(slug, scan_gen, status="cancelled")
            return
        except ServeSeekNotReadyError as exc:
            seek_scan_finish(
                slug,
                scan_gen,
                status="error",
                error=str(exc),
                error_status=503,
            )
            return
        except ServeSeekError as exc:
            seek_scan_finish(
                slug,
                scan_gen,
                status="error",
                error=str(exc),
                error_status=422,
            )
            return
        except OSError as exc:
            seek_scan_finish(
                slug,
                scan_gen,
                status="error",
                error=str(exc),
                error_status=503,
            )
            return
        except ServeViewshedError as exc:
            seek_scan_finish(
                slug,
                scan_gen,
                status="error",
                error=str(exc),
                error_status=503,
            )
            return
        except Exception as exc:
            seek_scan_finish(
                slug,
                scan_gen,
                status="error",
                error=str(exc) or exc.__class__.__name__,
                error_status=500,
            )
            return
        seek_scan_finish(slug, scan_gen, status="done", result=result)

    get_seek_queue().submit(_SeekJob(slug=prepared.slug, gen=scan_gen, run=_run))
    return scan_gen


@dataclass(frozen=True)
class _PreparedSeekRequest:
    slug: str
    body_kwargs: dict[str, object]


def _prepare_seek_request(
    project_dir: Path,
    *,
    from_lat: float,
    from_lon: float,
    goal_lat: float,
    goal_lon: float,
    bbox: str,
    exclude_raw: str | None,
    exclude_slugs_raw: str | None,
    goal_elev_m: float | None,
    peak_bin_size_m: float | None,
) -> _PreparedSeekRequest:
    if not (-90.0 <= from_lat <= 90.0 and -180.0 <= from_lon <= 180.0):
        raise ServeSeekError(f"from coordinates out of bounds: ({from_lat}, {from_lon})")
    if not (-90.0 <= goal_lat <= 90.0 and -180.0 <= goal_lon <= 180.0):
        raise ServeSeekError(f"goal coordinates out of bounds: ({goal_lat}, {goal_lon})")

    west, south, east, north = _parse_bbox(bbox)
    exclude = _parse_exclude_points(exclude_raw)
    exclude_slugs = _parse_exclude_slugs(exclude_slugs_raw)
    preset_path = project_dir / "config.yaml"
    slug = project_dir.name
    preset = _load_seek_preset(project_dir)
    seek_cfg = preset.seek
    peak_bin_m = resolve_seek_peak_bin_size_m(seek_cfg, peak_bin_size_m)
    return _PreparedSeekRequest(
        slug=slug,
        body_kwargs={
            "project_dir": project_dir,
            "slug": slug,
            "preset_path": preset_path,
            "preset": preset,
            "seek_cfg": seek_cfg,
            "peak_bin_m": peak_bin_m,
            "from_lat": from_lat,
            "from_lon": from_lon,
            "goal_lat": goal_lat,
            "goal_lon": goal_lon,
            "west": west,
            "south": south,
            "east": east,
            "north": north,
            "exclude": exclude,
            "exclude_slugs": exclude_slugs,
            "goal_elev_m": goal_elev_m,
            "scan_t0": time.monotonic(),
        },
    )


def _load_seek_candidates_body(
    *,
    project_dir: Path,
    slug: str,
    scan_gen: int,
    preset_path: Path,
    preset: Preset,
    seek_cfg: object,
    peak_bin_m: float,
    from_lat: float,
    from_lon: float,
    goal_lat: float,
    goal_lon: float,
    west: float,
    south: float,
    east: float,
    north: float,
    exclude: list[SeekPoint],
    exclude_slugs: set[str],
    goal_elev_m: float | None,
    verbose: bool,
    scan_t0: float,
) -> dict[str, object]:
    def _ensure_scan_active() -> None:
        if not seek_scan_active(slug, scan_gen):
            raise ServeSeekCancelled("superseded")

    try:
        seek_scan_update(slug, scan_gen, phase="eligible_land", detail="Building eligible land…")
        eligible, eligible_digest = load_or_build_eligible_geometry(preset_path)
    except EligibleLandError as e:
        raise ServeSeekError(str(e)) from e

    hop_km = float(preset.simulation.radius_km)
    search = _seek_search_geometry(
        eligible=eligible,
        from_lat=from_lat,
        from_lon=from_lon,
        radius_km=hop_km,
        west=west,
        south=south,
        east=east,
        north=north,
    )
    if search.is_empty:
        linkable: list[tuple[float, float, float]] = []
        n_peaks_scanned = 0
    else:
        seek_scan_update(slug, scan_gen, phase="peak_links", detail="Finding linkable peaks…")
        session = splatter_session(verbose=verbose)
        tx_height = float(preset_to_request(preset, from_lat, from_lon).tx_height)
        rf_json = rf_json_for_preset(preset)
        cap = int(seek_cfg.max_candidates)
        linkable = session.linkable_binned_peaks(
            from_lat,
            from_lon,
            tx_height,
            json.dumps(mapping(search)),
            rf_json,
            limit=cap,
            bin_size_m=peak_bin_m,
        )
        n_peaks_scanned = len(linkable)

    filtered = [
        (lon, lat, elev_m)
        for lon, lat, elev_m in linkable
        if not _near_excluded(lat, lon, exclude)
    ]
    filtered.sort(key=lambda p: _haversine_m(p[1], p[0], goal_lat, goal_lon))

    seek_scan_update(
        slug,
        scan_gen,
        phase="peak_links",
        done=len(filtered),
        total=max(n_peaks_scanned, 1),
        detail=f"Found {len(filtered)} linkable peak(s)",
    )

    site_rows = _collect_reachable_site_rows(
        preset=preset,
        eligible=eligible,
        from_lat=from_lat,
        from_lon=from_lon,
        goal_lat=goal_lat,
        goal_lon=goal_lon,
        exclude=exclude,
        exclude_slugs=exclude_slugs,
    )

    cap = int(seek_cfg.max_candidates)
    capped = _dedupe_peaks_near_sites(filtered[:cap], site_rows)

    goal_in_hop_range = _goal_in_hop_range(
        preset,
        from_lat=from_lat,
        from_lon=from_lon,
        goal_lat=goal_lat,
        goal_lon=goal_lon,
    )
    goal_on_eligible = _goal_on_eligible(eligible, goal_lat, goal_lon) if goal_in_hop_range else False
    goal_near_prior_hop = _near_excluded(goal_lat, goal_lon, exclude) if goal_in_hop_range else False
    goal_hop_eligible = (
        goal_in_hop_range and goal_on_eligible and not goal_near_prior_hop
    )
    goal_finish_eligible = _goal_finish_eligible(
        preset,
        from_lat=from_lat,
        from_lon=from_lon,
        goal_lat=goal_lat,
        goal_lon=goal_lon,
    )
    goal_reachable = goal_hop_eligible
    goal_elev = float(goal_elev_m) if goal_elev_m is not None else 0.0
    goal_row: tuple[float, float, float] | None = None
    if goal_finish_eligible:
        goal_row = (goal_lon, goal_lat, goal_elev)
        capped = _dedupe_peaks_near_sites(
            [
                (lon, lat, elev_m)
                for lon, lat, elev_m in capped
                if _haversine_m(goal_lat, goal_lon, lat, lon) > SEEK_GOAL_DEDUP_M
            ],
            site_rows,
        )[:cap]

    rf_candidates: list[_RfCandidate] = []
    if goal_row is not None:
        rf_candidates.append(
            _RfCandidate(
                lon=goal_row[0],
                lat=goal_row[1],
                elev_m=goal_row[2],
                candidate_id="goal",
                is_goal=True,
            )
        )
    for slug, name, lon, lat, elev_m in site_rows:
        rf_candidates.append(
            _RfCandidate(
                lon=lon,
                lat=lat,
                elev_m=elev_m,
                candidate_id=f"site:{slug}",
                is_site=True,
                site_slug=slug,
                site_name=name,
            )
        )
    for idx, (lon, lat, elev_m) in enumerate(capped):
        rf_candidates.append(
            _RfCandidate(
                lon=lon,
                lat=lat,
                elev_m=elev_m,
                candidate_id=f"c{idx}",
            )
        )

    rf_pairs = [(from_lat, from_lon, row.lat, row.lon) for row in rf_candidates]
    rf_viable: list[bool] = []
    peak_candidate_start = len(rf_candidates) - len(capped)
    if rf_pairs:
        seek_scan_update(slug, scan_gen, phase="rf", detail="Checking RF links…")
        session = splatter_session(verbose=verbose)
        site_goal_candidates = rf_candidates[:peak_candidate_start]
        if site_goal_candidates:
            points = [(from_lat, from_lon)] + [(row.lat, row.lon) for row in site_goal_candidates]
            session.ensure_tiles_for_points(points, float(preset.simulation.radius_km) * 1000.0)
            rf_json = rf_json_for_preset(preset)
            site_goal_viable = mutual_hop_batch(
                session,
                [(from_lat, from_lon, row.lat, row.lon) for row in site_goal_candidates],
                rf_json=rf_json,
            )
        else:
            site_goal_viable = []
        rf_viable = list(site_goal_viable) + [True] * len(capped)

    candidate_features: list[dict[str, object]] = []
    line_features: list[dict[str, object]] = []
    site_candidate_slugs: list[str] = []
    goal_rf_viable = False
    for idx, row in enumerate(rf_candidates):
        viable = bool(rf_viable[idx]) if idx < len(rf_viable) else False
        if row.is_goal:
            goal_rf_viable = viable
            if not viable:
                continue
        dist_km = _haversine_m(from_lat, from_lon, row.lat, row.lon) / 1000.0
        bearing = _bearing_deg(from_lat, from_lon, row.lat, row.lon)
        props: dict[str, object] = {
            "candidate_id": row.candidate_id,
            "lat": row.lat,
            "lon": row.lon,
            "elev_m": round(row.elev_m, 1),
            "distance_km": round(dist_km, 1),
            "bearing_deg": round(bearing, 0),
            "rf_viable": viable,
            "goal_distance_km": round(_haversine_m(row.lat, row.lon, goal_lat, goal_lon) / 1000.0, 1),
        }
        if row.is_goal:
            props["is_goal"] = True
        if row.is_site:
            props["is_site"] = True
            props["site_slug"] = row.site_slug
            props["site_name"] = row.site_name
            if row.site_slug:
                site_candidate_slugs.append(row.site_slug)
        candidate_features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [row.lon, row.lat]},
                "properties": props,
            }
        )
        if row.is_goal:
            continue
        line_features.append(
            _candidate_line_feature(
                from_lat=from_lat,
                from_lon=from_lon,
                to_lat=row.lat,
                to_lon=row.lon,
                distance_km=dist_km,
                bearing_deg=bearing,
                rf_viable=viable,
                elev_m=row.elev_m,
                candidate_id=row.candidate_id,
                is_goal=row.is_goal,
                is_site=row.is_site,
            )
        )
        if row.is_site and row.site_slug and row.site_name:
            line_features[-1]["properties"]["site_slug"] = row.site_slug
            line_features[-1]["properties"]["site_name"] = row.site_name

    return {
        "from": {"lat": from_lat, "lon": from_lon},
        "goal": {"lat": goal_lat, "lon": goal_lon},
        "candidates": {"type": "FeatureCollection", "features": candidate_features},
        "lines": {"type": "FeatureCollection", "features": line_features},
        "goal_line": _goal_line_feature(
            from_lat=from_lat,
            from_lon=from_lon,
            goal_lat=goal_lat,
            goal_lon=goal_lon,
        ),
        "meta": {
            "n_peaks_linkable": len(filtered),
            "n_peaks_tested": n_peaks_scanned,
            "n_candidates": sum(1 for row in rf_candidates if not row.is_goal and not row.is_site),
            "n_site_candidates": len(site_candidate_slugs),
            "site_candidate_slugs": site_candidate_slugs,
            "eligible_digest": eligible_digest,
            "goal_in_hop_range": goal_in_hop_range,
            "goal_on_eligible": goal_on_eligible,
            "goal_near_prior_hop": goal_near_prior_hop,
            "goal_hop_eligible": goal_hop_eligible,
            "goal_finish_eligible": goal_finish_eligible,
            "goal_reachable": goal_reachable,
            "goal_rf_viable": goal_rf_viable,
            "goal_distance_km": round(_haversine_m(from_lat, from_lon, goal_lat, goal_lon) / 1000.0, 1),
            "hop_range_km": hop_km,
            "peak_bin_size_m": peak_bin_m,
            "scan_ms": int((time.monotonic() - scan_t0) * 1000),
        },
    }
