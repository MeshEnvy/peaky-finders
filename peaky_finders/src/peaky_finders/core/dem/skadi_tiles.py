"""Skadi 1° tile naming and bounds for DEM peak scans."""

from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path

from peaky_finders.core.viewshed.skadi_mirror import skadi_mirror_tile_gz_path

VOID_SRTM = -32768

_SKADI_TILE_STEM_RE = re.compile(
    r"^([NS])(?P<lat>\d{2})([EW])(?P<lon>\d{3})(?:\.hgt)?$",
    re.IGNORECASE,
)


def skadi_tile_wgs84_bounds(tile_name: str) -> tuple[float, float, float, float]:
    """Axis-aligned EPSG:4326 bounds ``(minx, miny, maxx, maxy)`` for one Skadi HGT cell."""
    stem = Path(tile_name).name.replace(".gz", "").removesuffix(".hgt")
    m = _SKADI_TILE_STEM_RE.match(stem)
    if not m:
        raise ValueError(f"not a Skadi tile name: {tile_name!r}")
    lat_deg = int(m.group("lat"))
    lon_deg = int(m.group("lon"))
    if m.group(1).upper() == "N":
        miny, maxy = float(lat_deg), float(lat_deg) + 1.0
    else:
        miny, maxy = float(-lat_deg), float(-lat_deg) + 1.0
    if m.group(3).upper() == "E":
        minx, maxx = float(lon_deg), float(lon_deg) + 1.0
    else:
        minx, maxx = float(-lon_deg), float(-lon_deg) + 1.0
    return (minx, miny, maxx, maxy)


def iter_skadi_tile_names_for_wgs84_bounds(
    minx: float,
    miny: float,
    maxx: float,
    maxy: float,
) -> list[str]:
    """1° Skadi cells intersecting an EPSG:4326 bbox."""
    lon_min_tile = int(math.floor(minx))
    lon_max_tile = int(math.floor(maxx))
    lat_min_tile = int(math.floor(miny))
    lat_max_tile = int(math.floor(maxy))
    names: list[str] = []
    for lat_tile in range(lat_min_tile, lat_max_tile + 1):
        for lon_tile in range(lon_min_tile, lon_max_tile + 1):
            ns = "N" if lat_tile >= 0 else "S"
            ew = "E" if lon_tile >= 0 else "W"
            names.append(f"{ns}{abs(lat_tile):02d}{ew}{abs(lon_tile):03d}.hgt.gz")
    return names


def skadi_tile_set_fingerprint(minx: float, miny: float, maxx: float, maxy: float) -> str:
    tiles = iter_skadi_tile_names_for_wgs84_bounds(minx, miny, maxx, maxy)
    body = "dem_prefetch_v1\n" + "\n".join(sorted(tiles)) + "\n"
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def skadi_mirror_tile_cached(mirror_root: Path, tile_name: str) -> bool:
    try:
        path = skadi_mirror_tile_gz_path(mirror_root, tile_name)
    except ValueError:
        return False
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False
