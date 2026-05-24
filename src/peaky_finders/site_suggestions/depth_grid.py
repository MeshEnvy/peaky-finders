"""EPSG:3857 depth raster for AOI coverage planning."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import geopandas as gpd
import numpy as np
from rasterio import features
from rasterio.transform import from_bounds
from shapely import make_valid
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from peaky_finders.coverage_footprint import read_coverage_footprint
from peaky_finders.link_overlap import _read_coverage_footprint_epsg3857


@dataclass
class CoverageDepthGrid:
    """Per-cell overlap count clipped to AOI on an EPSG:3857 grid."""

    depth: np.ndarray
    aoi_mask: np.ndarray
    transform: object
    cols: int
    rows: int

    @property
    def aoi_cell_count(self) -> int:
        return int(np.count_nonzero(self.aoi_mask))

    def uncovered_mask(self, *, goal_depth: int) -> np.ndarray:
        need = int(goal_depth)
        return self.aoi_mask & (self.depth.astype(np.uint32) < need)

    def uncovered_fraction(self, *, goal_depth: int) -> float:
        total = self.aoi_cell_count
        if total <= 0:
            return 0.0
        return float(np.count_nonzero(self.uncovered_mask(goal_depth=goal_depth))) / float(total)

    def marginal_gain_cells(self, footprint_ll: BaseGeometry, *, goal_depth: int) -> int:
        if footprint_ll is None or footprint_ll.is_empty:
            return 0
        g = make_valid(footprint_ll) if not footprint_ll.is_valid else footprint_ll
        if g.is_empty:
            return 0
        gm = gpd.GeoDataFrame(geometry=[g], crs="EPSG:4326").to_crs("EPSG:3857").geometry.iloc[0]
        if gm is None or gm.is_empty:
            return 0
        need = self.uncovered_mask(goal_depth=goal_depth)
        layer = features.rasterize(
            [(gm, 1)],
            out_shape=(self.rows, self.cols),
            transform=self.transform,
            fill=0,
            dtype=np.uint8,
        ).astype(bool)
        return int(np.count_nonzero(need & layer))

    def add_footprint(self, footprint_ll: BaseGeometry) -> None:
        if footprint_ll is None or footprint_ll.is_empty:
            return
        g = make_valid(footprint_ll) if not footprint_ll.is_valid else footprint_ll
        if g.is_empty:
            return
        gm = gpd.GeoDataFrame(geometry=[g], crs="EPSG:4326").to_crs("EPSG:3857").geometry.iloc[0]
        if gm is None or gm.is_empty:
            return
        layer = features.rasterize(
            [(gm, 1)],
            out_shape=(self.rows, self.cols),
            transform=self.transform,
            fill=0,
            dtype=np.uint16,
        )
        self.depth += layer.astype(np.uint32, copy=False)


def _grid_shape_for_bounds(
    minx: float,
    miny: float,
    maxx: float,
    maxy: float,
    *,
    max_raster_dimension: int,
) -> tuple[int, int]:
    width_m = maxx - minx
    height_m = maxy - miny
    max_dim = max(1, int(max_raster_dimension))
    if width_m >= height_m:
        cols = max_dim
        rows = max(1, int(round(max_dim * height_m / width_m)))
    else:
        rows = max_dim
        cols = max(1, int(round(max_dim * width_m / height_m)))
    return cols, rows


def build_coverage_depth_grid(
    *,
    aoi_ll: BaseGeometry,
    footprint_gpkg_paths: Sequence[Path],
    max_raster_dimension: int,
) -> CoverageDepthGrid:
    """Sum footprint overlap counts over ``aoi_ll`` bounds on EPSG:3857."""
    aoi0 = make_valid(aoi_ll) if not aoi_ll.is_valid else aoi_ll
    if aoi0.is_empty:
        raise ValueError("AOI geometry is empty")

    aoi_m = gpd.GeoDataFrame(geometry=[aoi0], crs="EPSG:4326").to_crs("EPSG:3857").geometry.iloc[0]
    minx, miny, maxx, maxy = aoi_m.bounds
    cols, rows = _grid_shape_for_bounds(minx, miny, maxx, maxy, max_raster_dimension=max_raster_dimension)
    transform = from_bounds(minx, miny, maxx, maxy, cols, rows)

    aoi_mask = features.rasterize(
        [(aoi_m, 1)],
        out_shape=(rows, cols),
        transform=transform,
        fill=0,
        dtype=np.uint8,
    ).astype(bool)

    depth = np.zeros((rows, cols), dtype=np.uint32)
    for p in footprint_gpkg_paths:
        fp_ll = read_coverage_footprint(Path(p))
        if fp_ll is None or fp_ll.is_empty:
            continue
        fp_m = _read_coverage_footprint_epsg3857(Path(p))
        if fp_m is None or fp_m.is_empty:
            continue
        layer = features.rasterize(
            [(fp_m, 1)],
            out_shape=(rows, cols),
            transform=transform,
            fill=0,
            dtype=np.uint16,
        )
        depth += layer.astype(np.uint32, copy=False)

    return CoverageDepthGrid(
        depth=depth,
        aoi_mask=aoi_mask,
        transform=transform,
        cols=cols,
        rows=rows,
    )


def largest_uncovered_patch_centroid_ll(
    grid: CoverageDepthGrid,
    *,
    goal_depth: int,
) -> tuple[float, float] | None:
    """EPSG:4326 centroid of the largest connected uncovered AOI component (8-connect)."""
    mask = grid.uncovered_mask(goal_depth=goal_depth).astype(np.uint8)
    if not np.any(mask):
        return None
    pieces: list[BaseGeometry] = []
    from shapely.geometry import shape

    for geom, val in features.shapes(mask, mask=mask, transform=grid.transform, connectivity=8):
        if int(val) == 1:
            pieces.append(shape(geom))
    if not pieces:
        return None
    metric_pieces = [gpd.GeoDataFrame(geometry=[p], crs="EPSG:3857").to_crs("EPSG:4326").geometry.iloc[0] for p in pieces]
    best = max(metric_pieces, key=lambda g: g.area if g is not None and not g.is_empty else 0.0)
    if best is None or best.is_empty:
        return None
    c = best.centroid
    return (float(c.y), float(c.x))
