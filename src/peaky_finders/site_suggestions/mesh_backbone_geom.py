"""Mesh-backbone link geometry (anchor pairs, buffered legs, sampling)."""

from __future__ import annotations

from dataclasses import dataclass

import geopandas as gpd
import numpy as np
from pyproj import Transformer
from shapely import make_valid
from shapely.geometry import LineString, Point
from shapely.geometry.base import BaseGeometry

from peaky_finders.sites_job import MeshBackboneLinkEntry, MeshBackboneStrategyConfig

_TO_M = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
_FROM_M = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)


@dataclass(frozen=True)
class AnchorPoint:
    key: str
    lat: float
    lon: float


def anchor_from_loc(key: str, loc: tuple[float, float]) -> AnchorPoint:
    return AnchorPoint(key=str(key), lat=float(loc[0]), lon=float(loc[1]))


def anchors_from_config(cfg: MeshBackboneStrategyConfig) -> dict[str, AnchorPoint]:
    return {k: anchor_from_loc(k, loc) for k, loc in cfg.anchors.items()}


def build_link_leg(a: AnchorPoint, b: AnchorPoint) -> LineString:
    """EPSG:3857 line segment between two anchors."""
    x0, y0 = _TO_M.transform(float(a.lon), float(a.lat))
    x1, y1 = _TO_M.transform(float(b.lon), float(b.lat))
    return LineString([(x0, y0), (x1, y1)])


def resolve_link_leg(
    anchors: dict[str, AnchorPoint],
    link: MeshBackboneLinkEntry,
) -> LineString:
    a_key, b_key = link.endpoints
    return build_link_leg(anchors[a_key], anchors[b_key])


def link_search_zone(
    leg: LineString,
    *,
    buffer_m: float,
    eligible_ll: BaseGeometry,
) -> BaseGeometry | None:
    """Buffered link leg clipped to eligible land (EPSG:4326)."""
    if leg.is_empty:
        return None
    buf = max(1.0, float(buffer_m))
    zone_m = leg.buffer(buf)
    if zone_m is None or zone_m.is_empty:
        return None
    zone_m = make_valid(zone_m) if not zone_m.is_valid else zone_m

    elig = make_valid(eligible_ll) if not eligible_ll.is_valid else eligible_ll
    if elig.is_empty:
        return None
    elig_m = gpd.GeoDataFrame(geometry=[elig], crs="EPSG:4326").to_crs("EPSG:3857").geometry.iloc[0]
    clipped = zone_m.intersection(elig_m)
    if clipped is None or clipped.is_empty:
        return None

    out = gpd.GeoDataFrame(geometry=[clipped], crs="EPSG:3857").to_crs("EPSG:4326").geometry.iloc[0]
    if out is None or out.is_empty:
        return None
    return out if out.is_valid else make_valid(out)


def distance_to_link_m(lon: float, lat: float, leg: LineString) -> float:
    """Minimum distance in meters from ``(lon, lat)`` to the link leg."""
    if leg.is_empty:
        return float("inf")
    x, y = _TO_M.transform(float(lon), float(lat))
    return float(Point(x, y).distance(leg))


def sample_along_link(
    leg: LineString,
    *,
    spacing_m: float,
    zone_ll: BaseGeometry | None = None,
) -> list[tuple[float, float]]:
    """Sample ``(lat, lon)`` along a link leg every ``spacing_m`` (optionally clip to ``zone_ll``)."""
    if leg.is_empty:
        return []
    step = max(1.0, float(spacing_m))
    zone_m = None
    if zone_ll is not None and not zone_ll.is_empty:
        g = make_valid(zone_ll) if not zone_ll.is_valid else zone_ll
        zone_m = gpd.GeoDataFrame(geometry=[g], crs="EPSG:4326").to_crs("EPSG:3857").geometry.iloc[0]

    length = float(leg.length)
    if length <= 0:
        return []

    out: list[tuple[float, float]] = []
    seen: set[tuple[int, int]] = set()
    n = max(1, int(np.ceil(length / step)))
    for i in range(n + 1):
        dist = min(length, i * step)
        pt_m = leg.interpolate(dist)
        if zone_m is not None and not zone_m.intersects(pt_m):
            continue
        lon, lat = _FROM_M.transform(pt_m.x, pt_m.y)
        key = (int(round(lat * 1e5)), int(round(lon * 1e5)))
        if key in seen:
            continue
        seen.add(key)
        out.append((float(lat), float(lon)))
    return out


def resolved_link_legs(
    cfg: MeshBackboneStrategyConfig,
) -> list[tuple[MeshBackboneLinkEntry, LineString]]:
    """Each configured link with its EPSG:3857 leg."""
    anchors = anchors_from_config(cfg)
    return [(link, resolve_link_leg(anchors, link)) for link in cfg.links]
