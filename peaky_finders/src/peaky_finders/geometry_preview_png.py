"""Flat-map PNG previews for WGS84 geometries (EPSG:3857 axes; debug / cache sidecars)."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
from PIL import Image
from rasterio import features
from rasterio.transform import from_bounds
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

_DEFAULT_MAX_EDGE_PX = 1440
_PAD_FRAC = 0.02
_MIN_PAD_M = 500.0


def _aabbggrr_to_rgba_u8(hex8: str) -> tuple[int, int, int, int]:
    """KML ``aabbggrr`` → ``(r, g, b, a)`` in 0..255."""
    s = hex8.strip().lower()
    if len(s) != 8:
        raise ValueError(f"Expected 8 hex digits (aabbggrr); got {hex8!r}")
    aa = int(s[0:2], 16)
    bb = int(s[2:4], 16)
    gg = int(s[4:6], 16)
    rr = int(s[6:8], 16)
    return (rr, gg, bb, aa)


def _bounds_with_padding(
    minx: float,
    miny: float,
    maxx: float,
    maxy: float,
    *,
    pad_frac: float = _PAD_FRAC,
    min_pad_m: float = _MIN_PAD_M,
) -> tuple[float, float, float, float]:
    xpad = max((maxx - minx) * pad_frac, min_pad_m)
    ypad = max((maxy - miny) * pad_frac, min_pad_m)
    return minx - xpad, miny - ypad, maxx + xpad, maxy + ypad


def _output_shape(
    minx: float,
    miny: float,
    maxx: float,
    maxy: float,
    *,
    max_edge_px: int,
) -> tuple[int, int]:
    width_m = maxx - minx
    height_m = maxy - miny
    if width_m <= 0:
        width_m = _MIN_PAD_M * 2
    if height_m <= 0:
        height_m = _MIN_PAD_M * 2
    if width_m >= height_m:
        cols = max_edge_px
        rows = max(1, int(round(max_edge_px * height_m / width_m)))
    else:
        rows = max_edge_px
        cols = max(1, int(round(max_edge_px * width_m / height_m)))
    return rows, cols


def _iter_geometries(gdf: gpd.GeoDataFrame) -> list[BaseGeometry]:
    out: list[BaseGeometry] = []
    for geom in gdf.geometry:
        if geom is None or getattr(geom, "is_empty", True):
            continue
        out.append(geom)
    return out


def _rasterize_mask(
    geoms: list[BaseGeometry],
    *,
    rows: int,
    cols: int,
    transform,
) -> np.ndarray:
    if not geoms:
        return np.zeros((rows, cols), dtype=np.uint8)
    return features.rasterize(
        [(geom, 1) for geom in geoms],
        out_shape=(rows, cols),
        transform=transform,
        fill=0,
        dtype=np.uint8,
    )


def _line_geometries(
    geoms: list[BaseGeometry],
    *,
    pixel_size_m: float,
    line_width_px: float,
) -> list[BaseGeometry]:
    if line_width_px <= 0:
        return []
    half_w = max(pixel_size_m * line_width_px * 0.5, pixel_size_m * 0.5)
    out: list[BaseGeometry] = []
    for geom in geoms:
        gt = geom.geom_type
        if gt == "Point":
            continue
        if gt == "LineString":
            edge = geom
        elif gt == "MultiLineString":
            edge = geom
        else:
            edge = geom.boundary
        if edge is None or getattr(edge, "is_empty", True):
            continue
        buffered = edge.buffer(half_w, cap_style=2, join_style=2)
        if not buffered.is_empty:
            out.append(buffered)
    return out


def _point_geometries(
    geoms: list[BaseGeometry],
    *,
    pixel_size_m: float,
    marker_radius_px: float = 2.5,
) -> list[BaseGeometry]:
    radius_m = max(pixel_size_m * marker_radius_px, pixel_size_m)
    out: list[BaseGeometry] = []
    for geom in geoms:
        if geom.geom_type != "Point":
            continue
        out.append(Point(geom.x, geom.y).buffer(radius_m))
    return out


def _alpha_blend(base: np.ndarray, mask: np.ndarray, color: tuple[int, int, int, int]) -> None:
    if not np.any(mask):
        return
    r, g, b, a = color
    alpha = a / 255.0
    inv = 1.0 - alpha
    sel = mask.astype(bool)
    base[sel, 0] = np.clip(r * alpha + base[sel, 0] * inv, 0, 255).astype(np.uint8)
    base[sel, 1] = np.clip(g * alpha + base[sel, 1] * inv, 0, 255).astype(np.uint8)
    base[sel, 2] = np.clip(b * alpha + base[sel, 2] * inv, 0, 255).astype(np.uint8)
    base[sel, 3] = 255


def _render_geodataframe_preview_rgba(
    gdf_3857: gpd.GeoDataFrame,
    *,
    face_rgba: tuple[int, int, int, int],
    edge_rgba: tuple[int, int, int, int],
    fill_polygons: bool,
    line_width: float,
    max_edge_px: int,
) -> np.ndarray:
    geoms = _iter_geometries(gdf_3857)
    if not geoms:
        raise ValueError("GeoDataFrame has no drawable geometries")

    minx, miny, maxx, maxy = gdf_3857.total_bounds
    minx, miny, maxx, maxy = _bounds_with_padding(minx, miny, maxx, maxy)
    rows, cols = _output_shape(minx, miny, maxx, maxy, max_edge_px=max_edge_px)
    transform = from_bounds(minx, miny, maxx, maxy, cols, rows)
    pixel_size_m = max((maxx - minx) / cols, (maxy - miny) / rows)

    rgba = np.zeros((rows, cols, 4), dtype=np.uint8)
    rgba[:, :, :3] = 255
    rgba[:, :, 3] = 255

    types = set(gdf_3857.geom_type.astype(str))
    point_only = types <= {"Point"}

    if point_only:
        point_mask = _rasterize_mask(
            _point_geometries(geoms, pixel_size_m=pixel_size_m),
            rows=rows,
            cols=cols,
            transform=transform,
        )
        edge = edge_rgba
        if edge[3] < 90:
            edge = (edge[0], edge[1], edge[2], max(edge[3], 90))
        _alpha_blend(rgba, point_mask, edge)
        return rgba

    if fill_polygons and face_rgba[3] > 0:
        fill_mask = _rasterize_mask(geoms, rows=rows, cols=cols, transform=transform)
        _alpha_blend(rgba, fill_mask, face_rgba)

    if line_width > 0 and edge_rgba[3] > 0:
        line_mask = _rasterize_mask(
            _line_geometries(geoms, pixel_size_m=pixel_size_m, line_width_px=1.0),
            rows=rows,
            cols=cols,
            transform=transform,
        )
        _alpha_blend(rgba, line_mask, edge_rgba)

    return rgba


def write_wgs84_geodataframe_preview_png(
    gdf_wgs84: gpd.GeoDataFrame,
    png_path: Path,
    *,
    face_aabbggrr: str = "66888888",
    line_aabbggrr: str = "ff666666",
    fill_polygons: bool = True,
    line_width: float = 2.0,
    max_edge_px: int = _DEFAULT_MAX_EDGE_PX,
) -> None:
    """Write a flat-map PNG for one WGS84 GeoDataFrame; no-op if empty."""
    if gdf_wgs84.empty or gdf_wgs84.geometry.is_empty.all():
        return

    g = gdf_wgs84.to_crs("EPSG:3857")
    if _iter_geometries(g) == []:
        return

    rgba = _render_geodataframe_preview_rgba(
        g,
        face_rgba=_aabbggrr_to_rgba_u8(face_aabbggrr),
        edge_rgba=_aabbggrr_to_rgba_u8(line_aabbggrr),
        fill_polygons=fill_polygons,
        line_width=line_width,
        max_edge_px=max_edge_px,
    )

    png_path = Path(png_path)
    png_path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, mode="RGBA").save(png_path, format="PNG")


def write_wgs84_geometry_preview_png(
    geom: BaseGeometry,
    png_path: Path,
    *,
    face_aabbggrr: str = "66888888",
    line_aabbggrr: str = "ff666666",
    fill_polygons: bool = True,
    line_width: float = 2.0,
) -> None:
    """Write a single-geometry preview; no-op if geometry is null or empty."""
    if geom is None or getattr(geom, "is_empty", True):
        return
    write_wgs84_geodataframe_preview_png(
        gpd.GeoDataFrame(geometry=[geom], crs="EPSG:4326"),
        png_path,
        face_aabbggrr=face_aabbggrr,
        line_aabbggrr=line_aabbggrr,
        fill_polygons=fill_polygons,
        line_width=line_width,
    )
