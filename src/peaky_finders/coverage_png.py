"""Sample SPLAT-derived PNG overlays for whether a geographic point lies in modeled RF coverage."""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image


def bbox_rotation_normalized(bbox: dict[str, float]) -> tuple[float, float, float, float, float]:
    """Return ``north, south, east, west, rotation_deg`` with NaN rotation treated as 0."""
    rot_raw = bbox.get("rotation", 0.0)
    rotation = float(rot_raw) if rot_raw is not None else 0.0
    if rotation != rotation:  # NaN
        rotation = 0.0
    return (
        float(bbox["north"]),
        float(bbox["south"]),
        float(bbox["east"]),
        float(bbox["west"]),
        rotation,
    )


def fraction_to_lat_lon(
    u_frac: float,
    v_frac: float,
    *,
    north: float,
    south: float,
    east: float,
    west: float,
    rotation_deg: float,
) -> tuple[float, float]:
    """Inverse of the normalized fraction mapping used by ``_lat_lon_to_fraction``.

    ``u_frac`` increases eastward across image width; ``v_frac`` increases downward
    (aligned with raster row index: 0 at the **north** KML GroundOverlay edge, 1 south).
    Returns ``(lat, lon)`` using the same SPLAT/KML bbox convention as sampling.
    """
    lon_half = (east - west) / 2.0
    lat_half = (north - south) / 2.0
    if lon_half <= 0 or lat_half <= 0:
        raise ValueError("Invalid bbox half-extents")

    lon_c = (east + west) / 2.0
    lat_c = (north + south) / 2.0

    θ = math.radians(rotation_deg)
    ix = 2.0 * u_frac - 1.0
    # KML LatLonBox: image row 0 is the northern edge; SPLAT ppm matches that order.
    iy = 1.0 - 2.0 * v_frac
    gx = ix * math.cos(θ) - iy * math.sin(θ)
    gy = ix * math.sin(θ) + iy * math.cos(θ)

    lon = lon_c + gx * lon_half
    lat = lat_c + gy * lat_half
    return lat, lon


def pixel_to_lat_lon(
    col: float,
    row: float,
    *,
    width: int,
    height: int,
    north: float,
    south: float,
    east: float,
    west: float,
    rotation_deg: float,
) -> tuple[float, float]:
    """Map raster grid coordinate ``(col, row)`` (origin top-left, north-up) to ``(lat, lon)``.

    Coordinates follow GDAL/rasterio cell corners: ``col`` spans west→east over ``[0, width]``,
    ``row`` spans north→south over ``[0, height]``. This matches :func:`rasterio.features.shapes`
    polygons built with ``Affine.identity()``.

    Pixel ``(px, py)`` centers lie at ``(px + 0.5, py + 0.5)``. Sampling for ``point_in_png_coverage``
    uses the same convention (via ``u_frac * width`` → column index).
    """
    if width < 1 or height < 1:
        raise ValueError("width and height must be positive")
    u_frac = col / float(width)
    v_frac = row / float(height)
    return fraction_to_lat_lon(
        u_frac,
        v_frac,
        north=north,
        south=south,
        east=east,
        west=west,
        rotation_deg=rotation_deg,
    )


def _lat_lon_to_fraction(
    lat: float,
    lon: float,
    *,
    north: float,
    south: float,
    east: float,
    west: float,
    rotation_deg: float,
) -> tuple[float | None, float | None]:
    """Map (lat, lon) into image fractions matching KML GroundOverlay sampling.

    ``u`` spans west→east across width; ``v`` grows southward like row index with the
    **north** edge at ``v`` = 0. Returns ``(None, None)`` if the point falls outside the
    (possibly rotated) bounding box footprint.
    """
    lon_half = (east - west) / 2.0
    lat_half = (north - south) / 2.0
    if lon_half <= 0 or lat_half <= 0:
        return None, None

    lon_c = (east + west) / 2.0
    lat_c = (north + south) / 2.0
    dx = lon - lon_c
    dy = lat - lat_c

    gx = dx / lon_half
    gy = dy / lat_half

    θ = math.radians(rotation_deg)
    ix = gx * math.cos(θ) + gy * math.sin(θ)
    iy = -gx * math.sin(θ) + gy * math.cos(θ)

    u_frac = ix * 0.5 + 0.5
    v_frac = (1.0 - iy) * 0.5
    tol = 1e-9
    if u_frac < -tol or u_frac > 1.0 + tol or v_frac < -tol or v_frac > 1.0 + tol:
        return None, None

    u_frac = min(max(u_frac, 0.0), 1.0)
    v_frac = min(max(v_frac, 0.0), 1.0)
    return u_frac, v_frac


def point_in_png_coverage(
    png_path: Path,
    bbox: dict[str, float],
    lat: float,
    lon: float,
) -> bool:
    """True if ``(lat, lon)`` lies on a non-transparent / non-white-no-coverage pixel."""
    north, south, east, west, rotation = bbox_rotation_normalized(bbox)

    u_frac, v_frac = _lat_lon_to_fraction(
        lat, lon, north=north, south=south, east=east, west=west, rotation_deg=rotation
    )
    if u_frac is None:
        return False

    with Image.open(png_path) as im:
        w, h = im.size
        px = int(u_frac * w)
        py = int(v_frac * h)
        if px >= w:
            px = w - 1
        elif px < 0:
            px = 0
        if py >= h:
            py = h - 1
        elif py < 0:
            py = 0
        pixel = im.getpixel((px, py))

    if isinstance(pixel, int):
        return pixel < 255

    if len(pixel) >= 4:
        return pixel[3] > 127

    r, g, b = pixel[0], pixel[1], pixel[2]
    return not (r == 255 and g == 255 and b == 255)
