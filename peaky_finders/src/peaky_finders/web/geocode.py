"""Place-name geocoding for the web chat agent (Nominatim)."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import httpx
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

NOMINATIM_SEARCH_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
USER_AGENT = "PeakyWeb/1.0 (mesh site planning)"

PEAK_TYPES = frozenset({"peak", "volcano", "ridge", "hill", "mountain"})
PEAK_QUERY_WORDS = frozenset({"peak", "mountain", "mount", "summit", "hill", "charleston"})


class GeocodeError(RuntimeError):
    pass


def _query_wants_peak(query: str) -> bool:
    tokens = {t.strip(".,;:").lower() for t in query.split()}
    return bool(tokens & PEAK_QUERY_WORDS)


def _geocode_score(hit: dict[str, Any], *, wants_peak: bool) -> float:
    score = float(hit.get("importance") or 0.0)
    cat = str(hit.get("category") or "")
    typ = str(hit.get("type") or "")
    name = str(hit.get("display_name") or "").lower()

    if cat == "natural" and typ in PEAK_TYPES:
        score += 20.0
    elif cat == "place" and typ in {"locality", "hamlet", "village", "town"}:
        score += 2.0
    elif cat == "highway":
        score -= 12.0

    if wants_peak:
        if "street" in name or " road" in name or " avenue" in name:
            score -= 15.0
        if cat == "natural":
            score += 5.0

    return score


def _quality_label(hit: dict[str, Any], *, wants_peak: bool) -> str:
    cat = str(hit.get("category") or "")
    typ = str(hit.get("type") or "")
    name = str(hit.get("display_name") or "").lower()
    if cat == "natural" and typ in PEAK_TYPES:
        return "peak"
    if wants_peak and cat == "highway":
        return "likely_street_not_peak"
    if wants_peak and "street" in name:
        return "likely_street_not_peak"
    return "place"


def _in_viewbox(lat: float, lon: float, viewbox: list[float]) -> bool:
    if len(viewbox) != 4:
        return False
    west, south, east, north = viewbox
    return west <= float(lon) <= east and south <= float(lat) <= north


def _in_aoi(lat: float, lon: float, aoi: BaseGeometry) -> bool:
    return bool(aoi.covers(Point(float(lon), float(lat))))


def _project_region_boost(hit: dict[str, Any], *, project_slug: str | None) -> float:
    slug = str(project_slug or "").strip().lower()
    if not slug:
        return 0.0
    name = str(hit.get("display_name") or "").lower()
    if slug in name:
        return 5.0
    return 0.0


def rank_geocode_hits(
    hits: list[dict[str, Any]],
    *,
    query: str,
    viewbox: list[float] | None = None,
    aoi: BaseGeometry | None = None,
    project_slug: str | None = None,
) -> list[dict[str, Any]]:
    wants_peak = _query_wants_peak(query)
    ranked: list[dict[str, Any]] = []
    for hit in hits:
        lat = float(hit["lat"])
        lon = float(hit["lon"])
        score = _geocode_score(hit, wants_peak=wants_peak)
        score += _project_region_boost(hit, project_slug=project_slug)
        in_aoi = aoi is not None and _in_aoi(lat, lon, aoi)
        if in_aoi:
            score += 5.0
        elif viewbox and _in_viewbox(lat, lon, viewbox):
            score += 2.0
        ranked.append(
            {
                **hit,
                "score": round(score, 3),
                "quality": _quality_label(hit, wants_peak=wants_peak),
                "in_project_aoi": in_aoi,
            }
        )
    ranked.sort(key=lambda row: float(row["score"]), reverse=True)
    return ranked


def _best_geocode_hit(
    ranked: list[dict[str, Any]],
    *,
    viewbox: list[float] | None = None,
    aoi: BaseGeometry | None = None,
    wants_peak: bool = False,
) -> tuple[dict[str, Any] | None, bool]:
    if not ranked:
        return None, False
    if aoi is not None:
        in_aoi = [row for row in ranked if row.get("in_project_aoi")]
        if in_aoi:
            if wants_peak:
                in_aoi = [row for row in in_aoi if row.get("quality") == "peak"] or in_aoi
            best = in_aoi[0]
            return best, best is not ranked[0]
    if viewbox and len(viewbox) == 4:
        in_viewbox = [
            row
            for row in ranked
            if _in_viewbox(float(row["lat"]), float(row["lon"]), viewbox)
        ]
        if in_viewbox:
            if wants_peak:
                in_viewbox = [row for row in in_viewbox if row.get("quality") == "peak"] or in_viewbox
            best = in_viewbox[0]
            return best, best is not ranked[0]
    return ranked[0], False


def geocode_place(
    query: str,
    *,
    viewbox: list[float] | None = None,
    bounded: bool = False,
    countrycodes: str | None = "us",
    limit: int = 5,
    timeout_s: float = 15.0,
) -> list[dict[str, Any]]:
    """Resolve a place name to candidate WGS-84 coordinates via OpenStreetMap Nominatim."""
    q = str(query).strip()
    if not q:
        raise GeocodeError("empty query")

    params: dict[str, str | int] = {
        "q": q,
        "format": "jsonv2",
        "addressdetails": 0,
        "limit": max(1, min(int(limit), 10)),
    }
    if countrycodes:
        params["countrycodes"] = countrycodes
    if viewbox and len(viewbox) == 4:
        west, south, east, north = viewbox
        params["viewbox"] = f"{west},{north},{east},{south}"
        if bounded:
            params["bounded"] = 1

    headers = {"User-Agent": USER_AGENT}
    url = f"{NOMINATIM_SEARCH_URL}?{urlencode(params)}"
    try:
        with httpx.Client(timeout=timeout_s, headers=headers) as client:
            resp = client.get(url)
    except httpx.HTTPError as exc:
        raise GeocodeError(f"geocode request failed: {exc}") from exc

    if resp.status_code >= 400:
        raise GeocodeError(f"geocode HTTP {resp.status_code}")

    data = resp.json()
    if not isinstance(data, list):
        raise GeocodeError("unexpected geocode response")

    out: list[dict[str, Any]] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        try:
            lat = float(row["lat"])
            lon = float(row["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        bbox = None
        raw_bbox = row.get("boundingbox")
        if isinstance(raw_bbox, list) and len(raw_bbox) == 4:
            try:
                south_b, north_b, west_b, east_b = (float(raw_bbox[i]) for i in range(4))
                bbox = [west_b, south_b, east_b, north_b]
            except (TypeError, ValueError):
                bbox = None
        out.append(
            {
                "display_name": str(row.get("display_name") or q),
                "lat": lat,
                "lon": lon,
                "category": row.get("category"),
                "type": row.get("type"),
                "importance": row.get("importance"),
                "bbox": bbox,
            }
        )
    return out


def geocode_place_ranked(
    query: str,
    *,
    viewbox: list[float] | None = None,
    aoi: BaseGeometry | None = None,
    project_slug: str | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    """Geocode with project AOI bias, US filter, ranking, and a single best pick."""
    q = str(query).strip()
    hits = geocode_place(q, viewbox=viewbox, bounded=False, limit=limit)
    if not hits:
        hits = geocode_place(q, viewbox=None, bounded=False, limit=limit)
    ranked = rank_geocode_hits(hits, query=q, viewbox=viewbox, aoi=aoi, project_slug=project_slug)
    wants_peak = _query_wants_peak(q)
    best, aoi_picked = _best_geocode_hit(ranked, viewbox=viewbox, aoi=aoi, wants_peak=wants_peak)
    hint = None
    if best and best.get("quality") == "likely_street_not_peak":
        hint = (
            "Top hit looks like a street name, not a mountain peak. "
            "Prefer a natural/peak result or ask the user to clarify."
        )
    elif aoi_picked:
        hint = "Best pick is inside the active project AOI (not the highest global OSM rank)."
    elif not ranked:
        hint = "No matches. Try a more specific query such as 'Charleston Peak, Nevada'."
    return {
        "query": q,
        "results": ranked,
        "best": best,
        "hint": hint,
    }


def reverse_geocode_label(
    lat: float,
    lon: float,
    *,
    timeout_s: float = 12.0,
) -> str:
    """Short OSM label for a coordinate (peak / place name when available)."""
    params = {
        "lat": f"{float(lat):.6f}",
        "lon": f"{float(lon):.6f}",
        "format": "json",
        "zoom": 18,
        "addressdetails": 1,
    }
    headers = {"User-Agent": USER_AGENT}
    url = f"{NOMINATIM_REVERSE_URL}?{urlencode(params)}"
    try:
        with httpx.Client(timeout=timeout_s, headers=headers) as client:
            resp = client.get(url)
    except httpx.HTTPError as exc:
        raise GeocodeError(f"reverse geocode request failed: {exc}") from exc

    if resp.status_code >= 400:
        raise GeocodeError(f"reverse geocode HTTP {resp.status_code}")

    data = resp.json()
    if not isinstance(data, dict):
        raise GeocodeError("unexpected reverse geocode response")

    osm_class = str(data.get("class") or "")
    osm_type = str(data.get("type") or "")
    if osm_class == "natural" and osm_type in PEAK_TYPES:
        name = str(data.get("name") or "").strip()
        if name:
            return name

    address = data.get("address")
    if isinstance(address, dict):
        for key in ("peak", "mountain", "ridge", "volcano", "hill"):
            val = address.get(key)
            if val:
                return str(val).strip()

    name = str(data.get("name") or "").strip()
    if name and osm_type not in {"county", "state", "administrative"}:
        return name
    display = str(data.get("display_name") or "").strip()
    if display:
        return display.split(",")[0].strip()
    raise GeocodeError("reverse geocode returned no label")
