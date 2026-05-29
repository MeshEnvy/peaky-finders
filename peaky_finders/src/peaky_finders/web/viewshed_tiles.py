"""XYZ map tiles from georeferenced viewshed rasters (zoom-aware LoD)."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PIL import Image

from peaky_finders.coverage_png import _lat_lon_to_fraction, bbox_rotation_normalized
from peaky_finders.web.viewshed_rasters import (
    _bounds_for_workdir,
    latlonbox_image_coordinates,
    resolve_site_workdir,
    resolve_splat_png_path,
)

TILE_SIZE = 256
_WEB_TILES_DIR = ".web-tiles"


def _ppm_to_rgba(ppm_path: Path) -> np.ndarray:
    with Image.open(ppm_path) as img:
        rgb = np.asarray(img.convert("RGB"), dtype=np.uint8)
    no_coverage = np.all(rgb == 255, axis=2)
    rgba = np.zeros((*rgb.shape[:2], 4), dtype=np.uint8)
    rgba[~no_coverage, 0:3] = rgb[~no_coverage]
    rgba[~no_coverage, 3] = 255
    return rgba


def load_viewshed_rgba(workdir: Path) -> tuple[np.ndarray, Path]:
    """Full-resolution SPLAT grid for web tiling (prefers ``output.ppm`` over capped ``splat.png``)."""
    ppm = workdir / "output.ppm"
    png = workdir / "splat.png"
    if ppm.is_file():
        return _ppm_to_rgba(ppm), ppm
    if png.is_file():
        with Image.open(png) as img:
            return np.asarray(img.convert("RGBA"), dtype=np.uint8), png
    raise FileNotFoundError(f"no viewshed raster under {workdir}")


def axis_aligned_bounds(box: dict[str, float]) -> list[float]:
    """MapLibre ``bounds``: ``[west, south, east, north]`` covering rotated overlay corners."""
    corners = latlonbox_image_coordinates(box)
    lons = [c[0] for c in corners]
    lats = [c[1] for c in corners]
    return [min(lons), min(lats), max(lons), max(lats)]


def zoom_range_for_raster(*, width: int, height: int, bounds: dict[str, float]) -> tuple[int, int]:
    """Heuristic min/max zoom for a georeferenced raster source."""
    ab = axis_aligned_bounds(bounds)
    geo_w = max(ab[2] - ab[0], 1e-9)
    geo_h = max(ab[3] - ab[1], 1e-9)
    span = max(geo_w, geo_h)
    px = max(width, height)
    if px < 1:
        return 0, 18
    ratio = (360.0 * px) / (TILE_SIZE * span)
    maxzoom = 0 if ratio <= 1.0 else int(math.floor(math.log2(ratio)))
    maxzoom = max(0, min(22, maxzoom + 1))
    minzoom = max(0, maxzoom - 10)
    return minzoom, maxzoom


def _tile_lat_lon(z: int, x: int, y: int, px: np.ndarray, py: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    n = 2.0**z
    lon = (x + px / TILE_SIZE) / n * 360.0 - 180.0
    lat_rad = np.arctan(np.sinh(math.pi * (1.0 - 2.0 * (y + py / TILE_SIZE) / n)))
    lat = np.degrees(lat_rad)
    return lat, lon


def _sample_rgba_bilinear(rgba: np.ndarray, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    h, w = rgba.shape[:2]
    x = np.clip(u * (w - 1), 0.0, w - 1)
    y = np.clip(v * (h - 1), 0.0, h - 1)
    x0 = np.floor(x).astype(np.int32)
    y0 = np.floor(y).astype(np.int32)
    x1 = np.minimum(x0 + 1, w - 1)
    y1 = np.minimum(y0 + 1, h - 1)
    wx = (x - x0)[..., np.newaxis]
    wy = (y - y0)[..., np.newaxis]
    c00 = rgba[y0, x0].astype(np.float32)
    c10 = rgba[y0, x1].astype(np.float32)
    c01 = rgba[y1, x0].astype(np.float32)
    c11 = rgba[y1, x1].astype(np.float32)
    top = c00 * (1.0 - wx) + c10 * wx
    bot = c01 * (1.0 - wx) + c11 * wx
    return np.clip(top * (1.0 - wy) + bot * wy, 0.0, 255.0).astype(np.uint8)


def render_viewshed_tile(
    *,
    rgba: np.ndarray,
    bounds: dict[str, float],
    z: int,
    x: int,
    y: int,
) -> Image.Image:
    north, south, east, west, rot = bbox_rotation_normalized(bounds)
    cols = np.arange(TILE_SIZE, dtype=np.float64) + 0.5
    rows = np.arange(TILE_SIZE, dtype=np.float64) + 0.5
    px, py = np.meshgrid(cols, rows)
    lat, lon = _tile_lat_lon(z, x, y, px, py)

    u = np.empty_like(lat)
    v = np.empty_like(lat)
    valid = np.zeros(lat.shape, dtype=bool)
    for idx in np.ndindex(lat.shape):
        u_frac, v_frac = _lat_lon_to_fraction(
            float(lat[idx]),
            float(lon[idx]),
            north=north,
            south=south,
            east=east,
            west=west,
            rotation_deg=rot,
        )
        if u_frac is not None:
            valid[idx] = True
            u[idx] = u_frac
            v[idx] = v_frac

    out = np.zeros((TILE_SIZE, TILE_SIZE, 4), dtype=np.uint8)
    if np.any(valid):
        out[valid] = _sample_rgba_bilinear(rgba, u[valid], v[valid])
    return Image.fromarray(out, "RGBA")


def _source_cache_key(source_path: Path) -> str:
    st = source_path.stat()
    return f"{st.st_mtime_ns}-{st.st_size}"


def _tile_cache_path(*, workdir: Path, source_key: str, z: int, x: int, y: int) -> Path:
    return workdir / _WEB_TILES_DIR / source_key / str(z) / str(x) / f"{y}.png"


def ensure_viewshed_tile(
    *,
    project_slug: str,
    site_slug: str,
    z: int,
    x: int,
    y: int,
) -> Path:
    """Return cached XYZ tile PNG (render on miss)."""
    if z < 0 or z > 22:
        raise FileNotFoundError("tile zoom out of range")
    resolve_splat_png_path(project_slug=project_slug, site_slug=site_slug)
    workdir = resolve_site_workdir(project_slug=project_slug, site_slug=site_slug)
    bounds = _bounds_for_workdir(workdir)
    if bounds is None:
        raise FileNotFoundError(f"missing viewshed bounds for site {site_slug!r}")

    rgba, source_path = load_viewshed_rgba(workdir)
    source_key = _source_cache_key(source_path)
    cache_path = _tile_cache_path(workdir=workdir, source_key=source_key, z=z, x=x, y=y)
    if cache_path.is_file():
        return cache_path

    tile = render_viewshed_tile(rgba=rgba, bounds=bounds, z=z, x=x, y=y)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tile.save(cache_path, format="PNG", optimize=True)
    return cache_path


def tile_layer_metadata(*, project_slug: str, site_slug: str, workdir: Path, bounds: dict[str, float]) -> dict[str, object]:
    rgba, source_path = load_viewshed_rgba(workdir)
    h, w = rgba.shape[:2]
    minzoom, maxzoom = zoom_range_for_raster(width=w, height=h, bounds=bounds)
    return {
        "tile_url": f"/api/projects/{project_slug}/viewsheds/{site_slug}/tiles/{{z}}/{{x}}/{{y}}.png",
        "bounds": axis_aligned_bounds(bounds),
        "minzoom": minzoom,
        "maxzoom": maxzoom,
        "source_pixels": [w, h],
        "source_path": source_path.name,
    }
