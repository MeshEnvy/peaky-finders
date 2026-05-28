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

from peaky_finders.link_overlap import _read_coverage_footprint_epsg3857


def _strict_frontier_mask(covered: np.ndarray) -> np.ndarray:
    """True where ``covered`` cells touch a non-covered neighbor (8-connect) or grid edge."""
    padded = np.pad(covered, 1, constant_values=False)
    inner = padded[1:-1, 1:-1]
    frontier = np.zeros_like(covered, dtype=bool)
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if dr == 0 and dc == 0:
                continue
            neighbor = padded[1 + dr : 1 + dr + covered.shape[0], 1 + dc : 1 + dc + covered.shape[1]]
            frontier |= inner & ~neighbor
    return frontier


def _rasterize_seed_footprint_layer(
    path: Path,
    *,
    transform: object,
    rows: int,
    cols: int,
) -> np.ndarray | None:
    fp_m = _read_coverage_footprint_epsg3857(path)
    if fp_m is None or fp_m.is_empty:
        return None
    return features.rasterize(
        [(fp_m, 1)],
        out_shape=(rows, cols),
        transform=transform,
        fill=0,
        dtype=np.uint16,
    )


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

        from peaky_finders.site_suggestions.log import SuggestProgressTicker, suggest_progress

        n = len(peaks_llz)
        need = int(goal_depth)
        to_m = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
        out: list[tuple[float, float, float]] = []
        chunk = max(1, int(progress_every))
        ticker = SuggestProgressTicker(verbose, label="uncovered grid filter", interval_s=0.5)
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
            ticker.maybe(f"{end}/{n} peaks checked, {len(out)} kept")
        if verbose and n:
            ticker.done(f"{len(out)}/{n} peak(s) still uncovered")
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
        verbose: bool = False,
    ) -> list[tuple[float, float]]:
        """Sample ``(lat, lon)`` on depth≥``min_depth`` frontier biased toward ``goal``."""
        from pyproj import Transformer

        from peaky_finders.site_suggestions.log import SuggestProgressTicker

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
        ticker = SuggestProgressTicker(verbose, label="frontier scan", interval_s=0.5)

        frontier = _strict_frontier_mask(covered)
        fr_rows, fr_cols = np.where(frontier)
        strict_frontier = fr_rows.size > 0
        if not strict_frontier:
            ticker.maybe("no strict frontier cells; falling back to all covered cells", force=True)
            fr_rows, fr_cols = rows, cols

        ticker.maybe(
            f"{fr_rows.size} frontier cell(s) from {rows.size} covered cell(s)",
            force=True,
        )

        a, b, c, d, e, f = self.transform[:6]
        cols_f = fr_cols.astype(np.float64) + 0.5
        rows_f = fr_rows.astype(np.float64) + 0.5
        xs = c + cols_f * a + rows_f * b
        ys = f + cols_f * d + rows_f * e
        dist_goal = np.hypot(xs - gx, ys - gy)
        lons, lats = from_m.transform(xs, ys)

        from shapely import contains_xy

        eligible_mask = contains_xy(eligible_ll, np.asarray(lons), np.asarray(lats))
        keep = np.flatnonzero(eligible_mask)
        if keep.size == 0:
            if verbose:
                ticker.done("0 frontier sample(s) on eligible land")
            return []

        order = np.lexsort((lons[keep], lats[keep], dist_goal[keep]))
        scored = [
            (float(dist_goal[keep[i]]), float(lats[keep[i]]), float(lons[keep[i]]))
            for i in order
        ]
        if verbose and not strict_frontier:
            ticker.maybe(f"fallback eligible cells: {len(scored)}")
        elif verbose:
            ticker.maybe(f"eligible frontier cells: {len(scored)}")

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
        if verbose:
            ticker.done(f"{len(out)} frontier sample(s) kept (cap={max_points})")
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
    jobs: int = 1,
) -> CoverageDepthGrid:
    """Sum footprint overlap counts on an EPSG:3857 grid bounded by ``aoi_ll``.

    Marginal gain and uncovered fraction use ``target_ll`` (typically eligible land).
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

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

    workers = max(1, int(jobs))
    mx = min(workers, n_paths) if n_paths else 1
    if mx <= 1 or n_paths <= 1:
        for i, p in enumerate(paths, start=1):
            if verbose:
                suggest_progress(verbose, f"seed footprint [{i}/{n_paths}] {p.name}…")
            layer = _rasterize_seed_footprint_layer(p, transform=transform, rows=rows, cols=cols)
            if layer is None:
                if verbose:
                    suggest_progress(verbose, f"seed footprint [{i}/{n_paths}] {p.name}: empty, skipped")
                continue
            depth += layer.astype(np.uint32, copy=False)
            if verbose:
                suggest_progress(verbose, f"seed footprint [{i}/{n_paths}] {p.name}: merged")
    else:
        if verbose:
            suggest_progress(verbose, f"seed footprint rasterize: {n_paths} path(s), workers={mx}")
        done = 0
        with ThreadPoolExecutor(max_workers=mx) as pool:
            futs = {
                pool.submit(
                    _rasterize_seed_footprint_layer,
                    p,
                    transform=transform,
                    rows=rows,
                    cols=cols,
                ): p
                for p in paths
            }
            for fut in as_completed(futs):
                p = futs[fut]
                layer = fut.result()
                done += 1
                if layer is None:
                    if verbose:
                        suggest_progress(
                            verbose,
                            f"seed footprint [{done}/{n_paths}] {p.name}: empty, skipped",
                        )
                    continue
                depth += layer.astype(np.uint32, copy=False)
                if verbose:
                    suggest_progress(verbose, f"seed footprint [{done}/{n_paths}] {p.name}: merged")

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
