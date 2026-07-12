"""Ephemeral viewshed simulation overrides from serve HTTP query params."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode

MAX_SERVE_RADIUS_KM = 100.0
MIN_SERVE_RADIUS_KM = 1.0
MIN_SERVE_RASTER_DIMENSION = 128
MAX_SERVE_RASTER_DIMENSION = 4096
SERVE_VIEWSHED_PREVIEW_RASTER_DIMENSION = 256


@dataclass(frozen=True)
class ViewshedSimOverrides:
    """Optional ``radius_km`` / ``raster_dimension`` overrides for on-demand viewsheds."""

    radius_km: float | None = None
    raster_dimension: int | None = None

    def active(self) -> bool:
        return self.radius_km is not None or self.raster_dimension is not None


def parse_viewshed_sim_overrides(query: str) -> ViewshedSimOverrides:
    """Parse optional ``radius_km`` and ``raster_dimension`` from a query string."""
    qs = parse_qs(query, keep_blank_values=False)
    radius_raw = qs.get("radius_km", [None])[0]
    raster_raw = qs.get("raster_dimension", [None])[0]
    radius_km: float | None = None
    raster_dimension: int | None = None
    if radius_raw is not None and str(radius_raw).strip():
        radius_km = float(radius_raw)
        if not (MIN_SERVE_RADIUS_KM <= radius_km <= MAX_SERVE_RADIUS_KM):
            raise ValueError(
                f"radius_km must be between {MIN_SERVE_RADIUS_KM:g} and {MAX_SERVE_RADIUS_KM:g}"
            )
    if raster_raw is not None and str(raster_raw).strip():
        raster_dimension = int(float(raster_raw))
        if not (MIN_SERVE_RASTER_DIMENSION <= raster_dimension <= MAX_SERVE_RASTER_DIMENSION):
            raise ValueError(
                "raster_dimension must be between "
                f"{MIN_SERVE_RASTER_DIMENSION} and {MAX_SERVE_RASTER_DIMENSION}"
            )
    return ViewshedSimOverrides(radius_km=radius_km, raster_dimension=raster_dimension)


def viewshed_sim_query_string(overrides: ViewshedSimOverrides | None) -> str:
    """Canonical query suffix for viewshed PNG/meta URLs (no leading ``?``)."""
    if overrides is None or not overrides.active():
        return ""
    parts: dict[str, str] = {}
    if overrides.radius_km is not None:
        parts["radius_km"] = format(float(overrides.radius_km), "g")
    if overrides.raster_dimension is not None:
        parts["raster_dimension"] = str(int(overrides.raster_dimension))
    return urlencode(parts)
