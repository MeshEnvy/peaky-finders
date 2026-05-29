"""Place-name geocoding for the web chat agent (Nominatim)."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import httpx

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


def rank_geocode_hits(hits: list[dict[str, Any]], *, query: str) -> list[dict[str, Any]]:
    wants_peak = _query_wants_peak(query)
    ranked: list[dict[str, Any]] = []
    for hit in hits:
        score = _geocode_score(hit, wants_peak=wants_peak)
        ranked.append(
            {
                **hit,
                "score": round(score, 3),
                "quality": _quality_label(hit, wants_peak=wants_peak),
            }
        )
    ranked.sort(key=lambda row: float(row["score"]), reverse=True)
    return ranked


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
    limit: int = 5,
) -> dict[str, Any]:
    """Geocode with soft project bias, US filter, ranking, and a single best pick."""
    q = str(query).strip()
    hits = geocode_place(q, viewbox=viewbox, bounded=False, limit=limit)
    if not hits:
        hits = geocode_place(q, viewbox=None, bounded=False, limit=limit)
    ranked = rank_geocode_hits(hits, query=q)
    best = ranked[0] if ranked else None
    hint = None
    if best and best.get("quality") == "likely_street_not_peak":
        hint = (
            "Top hit looks like a street name, not a mountain peak. "
            "Prefer a natural/peak result or ask the user to clarify."
        )
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
