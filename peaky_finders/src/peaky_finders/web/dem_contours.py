"""Contour lines from cached Skadi HGT tiles for the web map."""

from __future__ import annotations

import gzip
import io
import json
import math
from pathlib import Path

import matplotlib

matplotlib.use('Agg')

import numpy as np
from matplotlib import pyplot as plt
from rasterio.transform import xy
from shapely.geometry import box, mapping, shape

from peaky_finders.skadi_dem import VOID_SRTM, iter_skadi_tile_names_for_wgs84_bounds, skadi_mirror_tile_gz_path
from peaky_finders.sites_job import (
    Preset,
    load_preset,
    resolved_preset_build_dir,
    resolved_skadi_mirror_dir,
)

WEB_CONTOUR_CACHE_VER = "web2"
WEB_INTERVAL_M = 200.0
WEB_SIMPLIFY_TOL = 0.00012


def resolved_project_dem_dir(preset_path: Path) -> Path | None:
    """Project ``build/dem`` mirror, else global ``SPLAT_CACHE`` when populated."""
    preset_path_r = preset_path.expanduser().resolve()
    for root in (resolved_preset_build_dir(preset_path_r) / "dem", resolved_skadi_mirror_dir()):
        if root.is_dir() and any(root.glob("*.hgt.gz")):
            return root.resolve()
    return None


def _decode_hgt_gz(path: Path) -> tuple[np.ndarray, object]:
    from rasterio.io import MemoryFile

    raw_hgt = gzip.GzipFile(fileobj=io.BytesIO(path.read_bytes())).read()
    mem_fn = path.name.replace(".gz", "")
    with MemoryFile(raw_hgt, filename=mem_fn) as mem:
        with mem.open(driver="SRTMHGT") as src:
            elev = np.ascontiguousarray(src.read(1), dtype=np.float64)
            transform = src.transform
    elev[elev <= VOID_SRTM] = np.nan
    return elev, transform


def _contour_levels(elev: np.ndarray, *, interval_m: float) -> np.ndarray:
    finite = elev[np.isfinite(elev)]
    if finite.size == 0:
        return np.array([], dtype=np.float64)
    lo = float(math.floor(finite.min() / interval_m) * interval_m)
    hi = float(math.ceil(finite.max() / interval_m) * interval_m)
    if hi <= lo:
        return np.array([lo], dtype=np.float64)
    return np.arange(lo + interval_m, hi, interval_m, dtype=np.float64)


def _contours_for_tile(
    *,
    tile_path: Path,
    interval_m: float,
) -> list[dict]:
    elev, transform = _decode_hgt_gz(tile_path)
    levels = _contour_levels(elev, interval_m=interval_m)
    if levels.size == 0:
        return []

    h, w = elev.shape
    cols = np.arange(w, dtype=np.float64)
    rows = np.arange(h, dtype=np.float64)
    lon_row = np.array([xy(transform, 0, c)[0] for c in cols], dtype=np.float64)
    lat_col = np.array([xy(transform, r, 0)[1] for r in rows], dtype=np.float64)
    lon_2d, lat_2d = np.meshgrid(lon_row, lat_col)

    fig, ax = plt.subplots(figsize=(1, 1), dpi=100)
    try:
        cs = ax.contour(lon_2d, lat_2d, elev, levels=levels)
    finally:
        plt.close(fig)

    out: list[dict] = []
    for level_idx, level in enumerate(cs.levels):
        for seg in cs.allsegs[level_idx]:
            if len(seg) < 2:
                continue
            coords = [[float(x), float(y)] for x, y in seg]
            out.append(
                {
                    "type": "Feature",
                    "properties": {"ele_m": float(level)},
                    "geometry": {"type": "LineString", "coordinates": coords},
                }
            )
    return out


def _bbox_for_preset(preset: Preset, *, pad: float = 0.08) -> tuple[float, float, float, float]:
    if not preset.sites:
        raise ValueError("preset has no sites")
    lats = [float(s.lat) for s in preset.sites.values()]
    lons = [float(s.lon) for s in preset.sites.values()]
    return min(lons) - pad, min(lats) - pad, max(lons) + pad, max(lats) + pad


def _optimize_for_web(
    features: list[dict],
    bbox: tuple[float, float, float, float],
    *,
    simplify_tol: float,
) -> list[dict]:
    clip = box(*bbox)
    out: list[dict] = []
    for feature in features:
        geom = shape(feature["geometry"])
        if geom.is_empty:
            continue
        clipped = geom.intersection(clip)
        if clipped.is_empty:
            continue
        parts = list(clipped.geoms) if clipped.geom_type == "MultiLineString" else [clipped]
        for part in parts:
            if part.geom_type != "LineString" or part.is_empty:
                continue
            simplified = part.simplify(simplify_tol, preserve_topology=True)
            if simplified.is_empty or len(simplified.coords) < 2:
                continue
            out.append(
                {
                    "type": "Feature",
                    "properties": feature["properties"],
                    "geometry": mapping(simplified),
                }
            )
    return out


def _cache_path(
    *,
    dem_dir: Path,
    interval_m: float,
    bbox: tuple[float, float, float, float],
    simplify_tol: float,
) -> Path:
    key = (
        f"{bbox[0]:.4f}_{bbox[1]:.4f}_{bbox[2]:.4f}_{bbox[3]:.4f}_"
        f"{int(interval_m)}_{simplify_tol:.5f}_{WEB_CONTOUR_CACHE_VER}"
    )
    return dem_dir / ".web-contours" / f"{key}.geojson"


def load_dem_contours(
    *,
    preset_path: Path,
    interval_m: float = WEB_INTERVAL_M,
    simplify_tol: float = WEB_SIMPLIFY_TOL,
) -> dict:
    preset_path_r = preset_path.expanduser().resolve()
    preset = load_preset(preset_path_r)
    dem_dir = resolved_project_dem_dir(preset_path_r)
    if dem_dir is None:
        raise FileNotFoundError("no Skadi DEM tiles available for project")

    bbox = _bbox_for_preset(preset)
    cache = _cache_path(dem_dir=dem_dir, interval_m=interval_m, bbox=bbox, simplify_tol=simplify_tol)
    if cache.is_file():
        return json.loads(cache.read_text())

    minx, miny, maxx, maxy = bbox
    tile_names = iter_skadi_tile_names_for_wgs84_bounds(minx, miny, maxx, maxy)
    raw_features: list[dict] = []
    for name in tile_names:
        tile_path = skadi_mirror_tile_gz_path(dem_dir, name)
        if not tile_path.is_file():
            continue
        raw_features.extend(_contours_for_tile(tile_path=tile_path, interval_m=interval_m))

    features = _optimize_for_web(raw_features, bbox, simplify_tol=simplify_tol)
    doc = {"type": "FeatureCollection", "features": features}
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(doc, separators=(",", ":")))
    return doc
