"""Shared candidate types and grid sampling helpers."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from pyproj import Transformer
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry


@dataclass(frozen=True)
class SiteCandidate:
    lat: float
    lon: float
    elev_m: float | None
    strategy: str


def _grid_samples_around_point(
    center: SiteCandidate,
    *,
    spacing_m: float,
    radius_m: float,
    eligible_ll: BaseGeometry,
    strategy: str,
) -> list[SiteCandidate]:
    spacing = max(10.0, float(spacing_m))
    radius = max(spacing / 2.0, float(radius_m))
    to_m = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    from_m = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
    cx, cy = to_m.transform(float(center.lon), float(center.lat))

    xs = np.arange(cx - radius, cx + radius + spacing * 0.5, spacing, dtype=np.float64)
    ys = np.arange(cy - radius, cy + radius + spacing * 0.5, spacing, dtype=np.float64)
    if xs.size == 0 or ys.size == 0:
        return [center]

    xx, yy = np.meshgrid(xs, ys)
    flat_x = xx.ravel()
    flat_y = yy.ravel()
    dist2 = (flat_x - cx) ** 2 + (flat_y - cy) ** 2
    sel = dist2 <= radius * radius
    if not np.any(sel):
        return [center]

    lons, lats = from_m.transform(flat_x[sel], flat_y[sel])
    out: list[SiteCandidate] = []
    for lon, lat in zip(lons, lats, strict=True):
        pt = Point(float(lon), float(lat))
        if not eligible_ll.intersects(pt):
            continue
        out.append(
            SiteCandidate(
                lat=float(lat),
                lon=float(lon),
                elev_m=center.elev_m,
                strategy=strategy,
            )
        )
    return out or [center]


def generate_refine_candidates(
    *,
    centers: list[SiteCandidate],
    eligible_ll: BaseGeometry,
    refine_radius_m: float,
    refine_spacing_m: float,
) -> list[SiteCandidate]:
    out: list[SiteCandidate] = []
    for center in centers:
        out.extend(
            _grid_samples_around_point(
                center,
                spacing_m=refine_spacing_m,
                radius_m=refine_radius_m,
                eligible_ll=eligible_ll,
                strategy="refine",
            )
        )
    return _dedupe_candidates(out)


def _dedupe_candidates(candidates: list[SiteCandidate]) -> list[SiteCandidate]:
    dedup: list[SiteCandidate] = []
    seen: set[tuple[int, int]] = set()
    for c in candidates:
        key = (int(round(c.lat * 1e5)), int(round(c.lon * 1e5)))
        if key in seen:
            continue
        seen.add(key)
        dedup.append(c)
    return dedup
