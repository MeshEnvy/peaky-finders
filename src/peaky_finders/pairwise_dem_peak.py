"""Global maximum Skadi DEM sample inside a WGS-84 polygon (pairwise overlap pins)."""

from __future__ import annotations

import gzip
import io
from functools import lru_cache
from pathlib import Path

import numpy as np
from rasterio import features
from rasterio.io import MemoryFile
from rasterio.transform import Affine, xy
from shapely import make_valid
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry

from peaky_finders.skadi_dem import (
    VOID_SRTM,
    iter_skadi_tile_names_for_wgs84_bounds,
    skadi_mirror_tile_gz_path,
    skadi_tile_wgs84_bounds,
)


def _affine_tuple_to_affine(tup: tuple[float, ...]) -> Affine:
    return Affine(*tup)


# LRU cap: worst case one SRTM1 1″ tile decoded as int32 is ~3601²×4 bytes (~52 MiB).


_SKADI_TILE_GRID_CACHE_MAX = 24


@lru_cache(maxsize=_SKADI_TILE_GRID_CACHE_MAX)
def _cached_skadi_elev_affine(
    gz_resolved_posix: str,
) -> tuple[np.ndarray, tuple[float, ...]]:
    """Decode one mirror ``*.hgt.gz`` to a detached int32 band + Affine (as immutable tuple rows)."""

    tp = Path(gz_resolved_posix)
    raw_hgt = gzip.GzipFile(fileobj=io.BytesIO(tp.read_bytes())).read()
    mem_fn = tp.name.replace(".gz", "")
    with MemoryFile(raw_hgt, filename=mem_fn) as mem:
        with mem.open(driver="SRTMHGT") as src:
            elev = np.ascontiguousarray(src.read(1), dtype=np.int32)
            tr = src.transform

    affine_tuple = tuple(getattr(tr, attr) for attr in ("a", "b", "c", "d", "e", "f"))
    return elev, affine_tuple


def skadi_dem_tile_grid_cache_clear() -> None:
    """Drop process-local LRU of decoded tiles (tests / long-lived REPL shells)."""

    _cached_skadi_elev_affine.cache_clear()


def _tile_max_on_geom(
    *,
    tile_name: str,
    elev: np.ndarray,
    transform_like: tuple[float, ...],
    geom_ll: BaseGeometry,
    void_val: int,
) -> tuple[float, float, float] | None:
    """Max valid cell under ``geom_ll`` using a predecoded SRTMHGT band."""

    try:
        tminx, tminy, tmaxx, tmaxy = skadi_tile_wgs84_bounds(tile_name)
        tb = box(tminx, tminy, tmaxx, tmaxy)
        geom_clip = geom_ll.intersection(tb)
    except ValueError:
        geom_clip = geom_ll

    if geom_clip.is_empty:
        return None
    if not geom_clip.is_valid:
        geom_clip = make_valid(geom_clip)
        if geom_clip.is_empty:
            return None

    transform = _affine_tuple_to_affine(transform_like)
    h, w = elev.shape

    shapes = [(geom_clip, 1)]
    mask = features.rasterize(
        shapes,
        out_shape=(h, w),
        transform=transform,
        fill=0,
        dtype=np.uint8,
        all_touched=False,
    ).astype(bool)

    void = elev == int(void_val)
    usable = mask & ~void
    if not np.any(usable):
        return None

    work = np.where(usable, elev.astype(np.float64), -np.inf)
    flat_max = float(np.max(work))
    if not np.isfinite(flat_max):
        return None
    idx = int(np.argmax(work))
    row, col = divmod(idx, w)
    lon, lat = xy(transform, row, col, offset="center")
    return (float(lon), float(lat), flat_max)


def global_max_skadi_elevation_in_polygon(
    geom_ll: BaseGeometry | None,
    mirror_root: Path | str,
    *,
    void_val: int = VOID_SRTM,
) -> tuple[float, float, float] | None:
    """Return ``(lon, lat, elev_m)`` of the highest valid SRTM cell center inside ``geom_ll``.

    Scans Skadi ``*.hgt.gz`` files on disk under ``mirror_root`` for every 1° cell intersecting
    ``geom_ll`` bounds. Decoded tiles are kept in a process-local LRU (see
    ``_SKADI_TILE_GRID_CACHE_MAX``) so repeated footprints sharing tiles avoid redundant I/O /
    decompress. Tie-break: greater ``elev_m`` wins; if tied, lexicographically smaller
    ``(lon, lat)``.
    """
    if geom_ll is None or geom_ll.is_empty:
        return None
    g0 = make_valid(geom_ll) if not geom_ll.is_valid else geom_ll
    if g0.is_empty:
        return None

    minx, miny, maxx, maxy = g0.bounds
    tiles = iter_skadi_tile_names_for_wgs84_bounds(minx, miny, maxx, maxy)
    root = Path(mirror_root)

    best_z: float | None = None
    best_lon = 0.0
    best_lat = 0.0

    for tile_name in tiles:
        try:
            tp = skadi_mirror_tile_gz_path(root, tile_name)
        except ValueError:
            continue
        if not tp.is_file():
            continue
        elev, aff_tup = _cached_skadi_elev_affine(str(tp.resolve()))
        got = _tile_max_on_geom(
            tile_name=tile_name,
            elev=elev,
            transform_like=aff_tup,
            geom_ll=g0,
            void_val=void_val,
        )
        if got is None:
            continue
        lon, lat, z = got
        if best_z is None or z > best_z or (z == best_z and (lon, lat) < (best_lon, best_lat)):
            best_z, best_lon, best_lat = z, lon, lat

    if best_z is None:
        return None
    return (best_lon, best_lat, best_z)
