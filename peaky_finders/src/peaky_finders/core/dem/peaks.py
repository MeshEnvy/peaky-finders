"""Skadi DEM binned peak scan inside WGS-84 polygons."""

from __future__ import annotations

import gzip
import io
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from pathlib import Path

import numpy as np
from pyproj import Transformer
from rasterio import features
from rasterio.io import MemoryFile
from rasterio.transform import Affine, xy
from shapely import make_valid
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry

from peaky_finders.core.dem.skadi_tiles import (
    VOID_SRTM,
    iter_skadi_tile_names_for_wgs84_bounds,
    skadi_tile_wgs84_bounds,
)
from peaky_finders.core.viewshed.skadi_mirror import skadi_mirror_tile_gz_path

_SKADI_TILE_GRID_CACHE_MAX = 24


def _affine_tuple_to_affine(tup: tuple[float, ...]) -> Affine:
    return Affine(*tup)


@lru_cache(maxsize=_SKADI_TILE_GRID_CACHE_MAX)
def _cached_skadi_elev_affine(gz_resolved_posix: str) -> tuple[np.ndarray, tuple[float, ...]]:
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
    _cached_skadi_elev_affine.cache_clear()


def _clip_geom_to_tile(geom_ll: BaseGeometry, tile_name: str) -> BaseGeometry | None:
    try:
        tminx, tminy, tmaxx, tmaxy = skadi_tile_wgs84_bounds(tile_name)
        geom_clip = geom_ll.intersection(box(tminx, tminy, tmaxx, tmaxy))
    except ValueError:
        geom_clip = geom_ll
    if geom_clip.is_empty:
        return None
    if not geom_clip.is_valid:
        geom_clip = make_valid(geom_clip)
        if geom_clip.is_empty:
            return None
    return geom_clip


def _tile_usable_mask_on_geom(
    *,
    elev: np.ndarray,
    transform_like: tuple[float, ...],
    geom_ll: BaseGeometry,
    void_val: int,
    all_touched: bool = False,
) -> np.ndarray:
    transform = _affine_tuple_to_affine(transform_like)
    h, w = elev.shape
    mask = features.rasterize(
        [(geom_ll, 1)],
        out_shape=(h, w),
        transform=transform,
        fill=0,
        dtype=np.uint8,
        all_touched=all_touched,
    ).astype(bool)
    void = elev == int(void_val)
    return mask & ~void


def _strict_local_maxima_mask(usable: np.ndarray, elev: np.ndarray) -> np.ndarray:
    z = np.where(usable, elev, np.iinfo(np.int32).min)
    h, w = z.shape
    z_pad = np.pad(z, 1, mode="constant", constant_values=np.iinfo(np.int32).min)
    u_pad = np.pad(usable, 1, mode="constant", constant_values=False)
    peak = usable.copy()
    for dr in range(3):
        for dc in range(3):
            if dr == 1 and dc == 1:
                continue
            neighbor = z_pad[dr : dr + h, dc : dc + w]
            n_use = u_pad[dr : dr + h, dc : dc + w]
            peak &= (~n_use) | (z > neighbor)
    return peak


def _tile_binned_peaks_on_geom(
    *,
    elev: np.ndarray,
    transform_like: tuple[float, ...],
    geom_ll: BaseGeometry,
    void_val: int,
    bin_size_m: float,
) -> list[tuple[float, float, float]]:
    usable = _tile_usable_mask_on_geom(
        elev=elev,
        transform_like=transform_like,
        geom_ll=geom_ll,
        void_val=void_val,
        all_touched=True,
    )
    peaks_only = _strict_local_maxima_mask(usable, elev)
    if not np.any(peaks_only):
        return []

    rows_i, cols_i = np.nonzero(peaks_only)
    zs = elev[rows_i, cols_i].astype(np.float64)
    transform = _affine_tuple_to_affine(transform_like)
    lons, lats = xy(transform, rows_i, cols_i, offset="center")
    lons = np.asarray(lons, dtype=np.float64)
    lats = np.asarray(lats, dtype=np.float64)

    bin_m = max(500.0, float(bin_size_m))
    to_m = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    xs, ys = to_m.transform(lons, lats)
    bx = np.floor(xs / bin_m).astype(np.int64)
    by = np.floor(ys / bin_m).astype(np.int64)
    bin_key = bx * np.int64(1_000_003) + by

    uniq, inv = np.unique(bin_key, return_inverse=True)
    out: list[tuple[float, float, float]] = []
    for bi in range(len(uniq)):
        sel = inv == bi
        zi = zs[sel]
        li = int(np.argmax(zi))
        idx = int(np.flatnonzero(sel)[li])
        out.append((float(lons[idx]), float(lats[idx]), float(zs[idx])))
    return out


def _binned_peaks_for_tile(
    *,
    tile_name: str,
    geom_ll: BaseGeometry,
    mirror_root: Path,
    bin_size_m: float,
    void_val: int,
) -> list[tuple[float, float, float]]:
    geom_clip = _clip_geom_to_tile(geom_ll, tile_name)
    if geom_clip is None:
        return []
    try:
        tp = skadi_mirror_tile_gz_path(mirror_root, tile_name)
    except ValueError:
        return []
    if not tp.is_file():
        return []
    elev, aff_tup = _cached_skadi_elev_affine(str(tp.resolve()))
    return _tile_binned_peaks_on_geom(
        elev=elev,
        transform_like=aff_tup,
        geom_ll=geom_clip,
        void_val=void_val,
        bin_size_m=bin_size_m,
    )


def skadi_binned_peaks_in_polygon(
    geom_ll: BaseGeometry | None,
    mirror_root: Path | str,
    *,
    bin_size_m: float = 1500.0,
    void_val: int = VOID_SRTM,
    max_workers: int = 1,
    verbose: bool = False,
    log_prefix: str = "",
    tile_progress: Callable[[int, int], None] | None = None,
) -> list[tuple[float, float, float]]:
    """Return ``[(lon, lat, elev_m), …]`` — highest SRTM cell per bin inside ``geom_ll``."""
    if geom_ll is None or geom_ll.is_empty:
        return []
    g0 = make_valid(geom_ll) if not geom_ll.is_valid else geom_ll
    if g0.is_empty:
        return []

    minx, miny, maxx, maxy = g0.bounds
    tiles = iter_skadi_tile_names_for_wgs84_bounds(minx, miny, maxx, maxy)
    root = Path(mirror_root)
    workers = max(1, int(max_workers))
    if verbose:
        print(
            f"{log_prefix}Skadi peak scan: {len(tiles)} tile(s), workers={workers}",
            flush=True,
        )

    raw: list[tuple[float, float, float]] = []
    done = 0

    def _run_tile(tile_name: str) -> tuple[str, list[tuple[float, float, float]], float]:
        t0 = time.perf_counter()
        peaks = _binned_peaks_for_tile(
            tile_name=tile_name,
            geom_ll=g0,
            mirror_root=root,
            bin_size_m=bin_size_m,
            void_val=void_val,
        )
        return tile_name, peaks, time.perf_counter() - t0

    if workers <= 1 or len(tiles) <= 1:
        for tile_name in tiles:
            tile_name, peaks, elapsed = _run_tile(tile_name)
            raw.extend(peaks)
            done += 1
            if tile_progress is not None:
                tile_progress(done, len(tiles))
            if verbose:
                print(
                    f"{log_prefix}  tile [{done}/{len(tiles)}] {tile_name}: "
                    f"{len(peaks)} peak(s) ({elapsed:.1f}s)",
                    flush=True,
                )
    else:
        mx = min(workers, len(tiles))
        with ThreadPoolExecutor(max_workers=mx) as pool:
            futs = {pool.submit(_run_tile, tile_name): tile_name for tile_name in tiles}
            for fut in as_completed(futs):
                tile_name, peaks, elapsed = fut.result()
                raw.extend(peaks)
                done += 1
                if tile_progress is not None:
                    tile_progress(done, len(tiles))
                if verbose:
                    print(
                        f"{log_prefix}  tile [{done}/{len(tiles)}] {tile_name}: "
                        f"{len(peaks)} peak(s) ({elapsed:.1f}s)",
                        flush=True,
                    )

    raw.sort(key=lambda p: (-p[2], p[0], p[1]))
    if verbose:
        print(f"{log_prefix}Skadi peak scan done: {len(raw)} raw peak(s)", flush=True)
    return raw
