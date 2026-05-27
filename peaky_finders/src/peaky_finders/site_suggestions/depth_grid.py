"""EPSG:3857 depth raster for site-suggestion coverage planning."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import geopandas as gpd
import numpy as np
from pyproj import Transformer
from rasterio import features
from rasterio.transform import from_bounds, rowcol
from shapely import make_valid
from shapely.geometry import Point, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from peaky_finders.coverage_footprint import read_coverage_footprint
from peaky_finders.link_overlap import _read_coverage_footprint_epsg3857


@dataclass
class CoverageDepthGrid:
    """Per-cell overlap count on an EPSG:3857 grid; scoring uses ``target_mask``."""

    depth: np.ndarray
    target_mask: np.ndarray
    transform: object
    cols: int
    rows: int

    @property
    def target_cell_count(self) -> int:
        return int(np.count_nonzero(self.target_mask))

    def uncovered_mask(self, *, goal_depth: int) -> np.ndarray:
        need = int(goal_depth)
        return self.target_mask & (self.depth.astype(np.uint32) < need)

    def uncovered_fraction(self, *, goal_depth: int) -> float:
        total = self.target_cell_count
        if total <= 0:
            return 0.0
        return float(np.count_nonzero(self.uncovered_mask(goal_depth=goal_depth))) / float(total)

    def point_marginal_gain_cells(self, lon: float, lat: float, *, goal_depth: int) -> int:
        """Return 1 when ``(lon, lat)`` lies in a target cell still below ``goal_depth``."""
        kept = self.filter_peaks_llz_by_uncovered_grid([(float(lon), float(lat), 0.0)], goal_depth=goal_depth)
        return 1 if kept else 0

    def depth_at_point(self, lon: float, lat: float) -> int:
        """Footprint overlap count at ``(lon, lat)``; 0 when out of bounds or outside the target mask."""
        to_m = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
        x, y = to_m.transform(float(lon), float(lat))
        rows, cols = rowcol(self.transform, x, y)
        r = int(rows)
        c = int(cols)
        if r < 0 or c < 0 or r >= self.rows or c >= self.cols:
            return 0
        if not self.target_mask[r, c]:
            return 0
        return int(self.depth[r, c])

    def filter_peaks_llz_by_uncovered_grid(
        self,
        peaks_llz: Sequence[tuple[float, float, float]],
        *,
        goal_depth: int,
        verbose: bool = False,
        progress_every: int = 20_000,
    ) -> list[tuple[float, float, float]]:
        """Keep peaks whose grid cell is in the target mask and still below ``goal_depth``."""
        if not peaks_llz:
            return []

        from peaky_finders.site_suggestions.log import suggest_progress

        n = len(peaks_llz)
        need = int(goal_depth)
        to_m = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
        out: list[tuple[float, float, float]] = []
        chunk = max(1, int(progress_every))
        for start in range(0, n, chunk):
            end = min(n, start + chunk)
            block = peaks_llz[start:end]
            lons = np.fromiter((p[0] for p in block), dtype=np.float64, count=len(block))
            lats = np.fromiter((p[1] for p in block), dtype=np.float64, count=len(block))
            xs, ys = to_m.transform(lons, lats)
            rows, cols = rowcol(self.transform, xs, ys)
            rows = np.asarray(rows, dtype=np.int64)
            cols = np.asarray(cols, dtype=np.int64)
            in_bounds = (rows >= 0) & (cols >= 0) & (rows < self.rows) & (cols < self.cols)
            sel = np.zeros(len(block), dtype=bool)
            if np.any(in_bounds):
                rr = rows[in_bounds]
                cc = cols[in_bounds]
                sel[in_bounds] = self.target_mask[rr, cc] & (self.depth[rr, cc].astype(np.uint32) < need)
            out.extend(p for p, keep in zip(block, sel, strict=True) if keep)
            if verbose and end < n:
                suggest_progress(verbose, f"uncovered grid filter {end}/{n} peaks checked, {len(out)} kept so far")
        return out

    def uncovered_geometry_wgs84(self, *, goal_depth: int) -> BaseGeometry | None:
        """EPSG:4326 union of target cells still below ``goal_depth``."""
        mask = self.uncovered_mask(goal_depth=goal_depth).astype(np.uint8)
        if not np.any(mask):
            return None
        pieces: list[BaseGeometry] = []
        for geom, val in features.shapes(mask, mask=mask.astype(bool), transform=self.transform, connectivity=8):
            if int(val) == 1:
                pieces.append(shape(geom))
        if not pieces:
            return None
        union_m = unary_union(pieces)
        if union_m is None or union_m.is_empty:
            return None
        out = gpd.GeoDataFrame(geometry=[union_m], crs="EPSG:3857").to_crs("EPSG:4326").geometry.iloc[0]
        if out is None or out.is_empty:
            return None
        return out if out.is_valid else make_valid(out)

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

    def covered_mask(self, *, min_depth: int = 1) -> np.ndarray:
        need = max(1, int(min_depth))
        return self.target_mask & (self.depth.astype(np.uint32) >= need)

    def coverage_geometry_wgs84(self, *, min_depth: int = 1) -> BaseGeometry | None:
        """EPSG:4326 union of target cells with overlap depth ≥ ``min_depth``."""
        mask = self.covered_mask(min_depth=min_depth).astype(np.uint8)
        if not np.any(mask):
            return None
        pieces: list[BaseGeometry] = []
        for geom, val in features.shapes(mask, mask=mask.astype(bool), transform=self.transform, connectivity=8):
            if int(val) == 1:
                pieces.append(shape(geom))
        if not pieces:
            return None
        union_m = unary_union(pieces)
        if union_m is None or union_m.is_empty:
            return None
        out = gpd.GeoDataFrame(geometry=[union_m], crs="EPSG:3857").to_crs("EPSG:4326").geometry.iloc[0]
        if out is None or out.is_empty:
            return None
        return out if out.is_valid else make_valid(out)

    def min_distance_coverage_to_point_m(
        self,
        lon: float,
        lat: float,
        *,
        min_depth: int = 1,
    ) -> float:
        """Minimum EPSG:3857 distance from composite coverage (depth≥``min_depth``) to ``(lon, lat)``."""
        from pyproj import Transformer

        geom = self.coverage_geometry_wgs84(min_depth=min_depth)
        if geom is None or geom.is_empty:
            return float("inf")
        to_m = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
        x, y = to_m.transform(float(lon), float(lat))
        gm = gpd.GeoDataFrame(geometry=[geom], crs="EPSG:4326").to_crs("EPSG:3857").geometry.iloc[0]
        return float(Point(x, y).distance(gm))

    def frontier_sample_points_toward_goal(
        self,
        *,
        goal_lon: float,
        goal_lat: float,
        eligible_ll: BaseGeometry,
        min_depth: int = 1,
        spacing_m: float = 500.0,
        max_points: int = 48,
        coverage_geometry_wgs84: BaseGeometry | None = None,
    ) -> list[tuple[float, float]]:
        """Sample ``(lat, lon)`` on depth≥``min_depth`` frontier biased toward ``goal``."""
        from pyproj import Transformer

        if coverage_geometry_wgs84 is not None and not coverage_geometry_wgs84.is_empty:
            g = (
                coverage_geometry_wgs84
                if coverage_geometry_wgs84.is_valid
                else make_valid(coverage_geometry_wgs84)
            )
            gm = gpd.GeoDataFrame(geometry=[g], crs="EPSG:4326").to_crs("EPSG:3857").geometry.iloc[0]
            layer = features.rasterize(
                [(gm, 1)],
                out_shape=(self.rows, self.cols),
                transform=self.transform,
                fill=0,
                dtype=np.uint8,
            ).astype(bool)
            covered = self.target_mask & layer
        else:
            covered = self.covered_mask(min_depth=min_depth)
        if not np.any(covered):
            return []

        to_m = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
        from_m = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
        gx, gy = to_m.transform(float(goal_lon), float(goal_lat))

        rows, cols = np.where(covered)
        if rows.size == 0:
            return []

        scored: list[tuple[float, float, float]] = []
        for r, c in zip(rows, cols, strict=True):
            x, y = self.transform * (c + 0.5, r + 0.5)
            dist_goal = float(np.hypot(x - gx, y - gy))
            is_frontier = False
            for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)):
                nr, nc = int(r) + dr, int(c) + dc
                if nr < 0 or nc < 0 or nr >= self.rows or nc >= self.cols:
                    is_frontier = True
                    break
                if not covered[nr, nc]:
                    is_frontier = True
                    break
            if not is_frontier:
                continue
            lon, lat = from_m.transform(x, y)
            if not eligible_ll.intersects(Point(float(lon), float(lat))):
                continue
            scored.append((dist_goal, float(lat), float(lon)))

        if not scored:
            for r, c in zip(rows, cols, strict=True):
                x, y = self.transform * (c + 0.5, r + 0.5)
                dist_goal = float(np.hypot(x - gx, y - gy))
                lon, lat = from_m.transform(x, y)
                if not eligible_ll.intersects(Point(float(lon), float(lat))):
                    continue
                scored.append((dist_goal, float(lat), float(lon)))

        scored.sort(key=lambda item: (item[0], item[1], item[2]))
        step = max(50.0, float(spacing_m))
        out: list[tuple[float, float]] = []
        seen: set[tuple[int, int]] = set()
        for _dist, lat, lon in scored:
            x, y = to_m.transform(lon, lat)
            key = (int(round(x / step)), int(round(y / step)))
            if key in seen:
                continue
            seen.add(key)
            out.append((lat, lon))
            if len(out) >= max(1, int(max_points)):
                break
        return out


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
    target_ll: BaseGeometry,
    footprint_gpkg_paths: Sequence[Path],
    max_raster_dimension: int,
    verbose: bool = False,
    target_label: str = "target",
) -> CoverageDepthGrid:
    """Sum footprint overlap counts on an EPSG:3857 grid bounded by ``aoi_ll``.

    Marginal gain and uncovered fraction use ``target_ll`` (typically eligible land).
    """
    from peaky_finders.site_suggestions.log import suggest_log, suggest_progress

    aoi0 = make_valid(aoi_ll) if not aoi_ll.is_valid else aoi_ll
    if aoi0.is_empty:
        raise ValueError("AOI geometry is empty")

    target0 = make_valid(target_ll) if not target_ll.is_valid else target_ll
    if target0.is_empty:
        raise ValueError("coverage target geometry is empty")

    aoi_m = gpd.GeoDataFrame(geometry=[aoi0], crs="EPSG:4326").to_crs("EPSG:3857").geometry.iloc[0]
    target_m = gpd.GeoDataFrame(geometry=[target0], crs="EPSG:4326").to_crs("EPSG:3857").geometry.iloc[0]
    minx, miny, maxx, maxy = aoi_m.bounds
    cols, rows = _grid_shape_for_bounds(minx, miny, maxx, maxy, max_raster_dimension=max_raster_dimension)
    transform = from_bounds(minx, miny, maxx, maxy, cols, rows)
    if verbose:
        suggest_log(
            verbose,
            f"site suggest:     planner grid {cols}×{rows} px (max_dim={max_raster_dimension})",
        )

    target_mask = features.rasterize(
        [(target_m, 1)],
        out_shape=(rows, cols),
        transform=transform,
        fill=0,
        dtype=np.uint8,
    ).astype(bool)

    depth = np.zeros((rows, cols), dtype=np.uint32)
    paths = [Path(p) for p in footprint_gpkg_paths]
    n_paths = len(paths)
    if verbose and n_paths:
        suggest_log(verbose, f"site suggest:     rasterizing {n_paths} seed footprint(s)…")
    for i, p in enumerate(paths, start=1):
        if verbose:
            suggest_progress(verbose, f"seed footprint [{i}/{n_paths}] {p.name}…")
        fp_ll = read_coverage_footprint(p)
        if fp_ll is None or fp_ll.is_empty:
            if verbose:
                suggest_progress(verbose, f"seed footprint [{i}/{n_paths}] {p.name}: empty, skipped")
            continue
        fp_m = _read_coverage_footprint_epsg3857(p)
        if fp_m is None or fp_m.is_empty:
            if verbose:
                suggest_progress(verbose, f"seed footprint [{i}/{n_paths}] {p.name}: empty EPSG:3857, skipped")
            continue
        layer = features.rasterize(
            [(fp_m, 1)],
            out_shape=(rows, cols),
            transform=transform,
            fill=0,
            dtype=np.uint16,
        )
        depth += layer.astype(np.uint32, copy=False)
        if verbose:
            suggest_progress(verbose, f"seed footprint [{i}/{n_paths}] {p.name}: merged")

    if verbose:
        suggest_log(
            verbose,
            f"site suggest:     initial depth grid: {int(np.count_nonzero(target_mask))} {target_label} cell(s)",
        )

    return CoverageDepthGrid(
        depth=depth,
        target_mask=target_mask,
        transform=transform,
        cols=cols,
        rows=rows,
    )


def largest_uncovered_patch_centroid_ll(
    grid: CoverageDepthGrid,
    *,
    goal_depth: int,
) -> tuple[float, float] | None:
    """EPSG:4326 centroid of the largest connected uncovered target component (8-connect)."""
    mask = grid.uncovered_mask(goal_depth=goal_depth).astype(np.uint8)
    if not np.any(mask):
        return None
    pieces: list[BaseGeometry] = []
    for geom, val in features.shapes(mask, mask=mask.astype(bool), transform=grid.transform, connectivity=8):
        if int(val) == 1:
            pieces.append(shape(geom))
    if not pieces:
        return None
    best = max(pieces, key=lambda g: g.area if not g.is_empty else 0.0)
    if best is None or best.is_empty:
        return None
    out = gpd.GeoDataFrame(geometry=[best.centroid], crs="EPSG:3857").to_crs("EPSG:4326").geometry.iloc[0]
    if out is None or out.is_empty:
        return None
    return (float(out.y), float(out.x))
