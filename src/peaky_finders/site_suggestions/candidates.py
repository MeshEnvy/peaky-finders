"""Candidate site locations on eligible land."""

from __future__ import annotations

from dataclasses import dataclass

import geopandas as gpd
import numpy as np
from rasterio import features
from rasterio.transform import from_bounds, xy
from shapely import make_valid
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

from peaky_finders.pairwise_dem_peak import global_max_skadi_elevation_in_polygon
from peaky_finders.site_suggestions.depth_grid import CoverageDepthGrid, largest_uncovered_patch_centroid_ll


@dataclass(frozen=True)
class SiteCandidate:
    lat: float
    lon: float
    elev_m: float | None
    strategy: str


def _cluster_points_by_buffer(
    points: list[SiteCandidate],
    *,
    cluster_radius_m: float,
) -> list[SiteCandidate]:
    if not points:
        return []
    if cluster_radius_m <= 0 or len(points) == 1:
        return points

    half = float(cluster_radius_m) / 2.0
    metric = [
        (
            c,
            gpd.GeoDataFrame(geometry=[Point(c.lon, c.lat)], crs="EPSG:4326")
            .to_crs("EPSG:3857")
            .geometry.iloc[0],
        )
        for c in points
    ]
    kept: list[SiteCandidate] = []
    used = [False] * len(metric)
    for i, (ci, gi) in enumerate(metric):
        if used[i]:
            continue
        cluster = [ci]
        used[i] = True
        buf_i = gi.buffer(half)
        for j in range(i + 1, len(metric)):
            if used[j]:
                continue
            cj, gj = metric[j]
            if buf_i.intersects(gj.buffer(half)):
                used[j] = True
                cluster.append(cj)
        best = max(
            cluster,
            key=lambda c: (c.elev_m is not None, c.elev_m or -1.0),
        )
        kept.append(best)
    return kept


def _eligible_peak_candidates(
    *,
    eligible_ll: BaseGeometry,
    grid: CoverageDepthGrid,
    goal_depth: int,
    dem_mirror_root,
    max_candidates: int,
    cluster_radius_m: float,
    return_stats: bool = False,
) -> tuple[list[SiteCandidate], dict[str, int] | None]:
    elig = make_valid(eligible_ll) if not eligible_ll.is_valid else eligible_ll
    if elig.is_empty:
        return [], None

    elig_m = gpd.GeoDataFrame(geometry=[elig], crs="EPSG:4326").to_crs("EPSG:3857").geometry.iloc[0]
    minx, miny, maxx, maxy = elig_m.bounds
    width_m = maxx - minx
    height_m = maxy - miny
    sample_dim = max(8, min(48, int(max_candidates * 2)))
    if width_m >= height_m:
        cols = sample_dim
        rows = max(8, int(round(sample_dim * height_m / max(width_m, 1.0))))
    else:
        rows = sample_dim
        cols = max(8, int(round(sample_dim * width_m / max(height_m, 1.0))))
    transform = from_bounds(minx, miny, maxx, maxy, cols, rows)

    elig_mask = features.rasterize(
        [(elig_m, 1)],
        out_shape=(rows, cols),
        transform=transform,
        fill=0,
        dtype=np.uint8,
    ).astype(bool)

    need = grid.uncovered_mask(goal_depth=goal_depth)
    if not np.any(need):
        return [], None

    raw: list[SiteCandidate] = []
    for row in range(rows):
        for col in range(cols):
            if not elig_mask[row, col]:
                continue
            lon, lat = xy(transform, row, col, offset="center")
            pt_ll = (
                gpd.GeoDataFrame(geometry=[Point(float(lon), float(lat))], crs="EPSG:3857")
                .to_crs("EPSG:4326")
                .geometry.iloc[0]
            )
            if pt_ll is None or pt_ll.is_empty or not elig.intersects(pt_ll):
                continue
            if grid.marginal_gain_cells(pt_ll, goal_depth=goal_depth) <= 0:
                continue
            peak = global_max_skadi_elevation_in_polygon(pt_ll.buffer(0.002), dem_mirror_root)
            if peak is not None:
                raw.append(
                    SiteCandidate(
                        lat=float(peak[1]),
                        lon=float(peak[0]),
                        elev_m=float(peak[2]),
                        strategy="peak",
                    )
                )
            else:
                raw.append(
                    SiteCandidate(
                        lat=float(pt_ll.y),
                        lon=float(pt_ll.x),
                        elev_m=None,
                        strategy="peak",
                    )
                )

    raw.sort(key=lambda c: (c.elev_m is not None, c.elev_m or -1.0), reverse=True)
    clustered = _cluster_points_by_buffer(raw, cluster_radius_m=cluster_radius_m)
    kept = clustered[: max(1, int(max_candidates))]
    stats = None
    if return_stats:
        stats = {
            "cols": cols,
            "rows": rows,
            "raw_count": len(raw),
            "clustered_count": len(clustered),
        }
    return kept, stats


def _gap_fill_candidate(
    *,
    grid: CoverageDepthGrid,
    goal_depth: int,
    eligible_ll: BaseGeometry,
) -> SiteCandidate | None:
    centroid = largest_uncovered_patch_centroid_ll(grid, goal_depth=goal_depth)
    if centroid is None:
        return None
    lat, lon = centroid
    pt = Point(lon, lat)
    if not eligible_ll.intersects(pt):
        rep = eligible_ll.representative_point()
        lat, lon = float(rep.y), float(rep.x)
    return SiteCandidate(lat=lat, lon=lon, elev_m=None, strategy="gap")


def generate_site_candidates(
    *,
    eligible_ll: BaseGeometry,
    grid: CoverageDepthGrid,
    goal_depth: int,
    dem_mirror_root,
    max_candidates: int,
    cluster_radius_m: float,
    iteration: int,
    verbose: bool = False,
) -> list[SiteCandidate]:
    """Peak-biased shortlist, with gap centroid on later iterations."""
    from peaky_finders.site_suggestions.log import suggest_log

    out: list[SiteCandidate] = []
    peaks, peak_stats = _eligible_peak_candidates(
        eligible_ll=eligible_ll,
        grid=grid,
        goal_depth=goal_depth,
        dem_mirror_root=dem_mirror_root,
        max_candidates=max_candidates,
        cluster_radius_m=cluster_radius_m,
        return_stats=verbose,
    )
    if verbose and peak_stats is not None:
        suggest_log(verbose, "site suggest:   peak candidate generation:")
        suggest_log(verbose, f"     eligible sample grid: {peak_stats['cols']}×{peak_stats['rows']}")
        suggest_log(verbose, f"     raw eligible+uncovered samples: {peak_stats['raw_count']}")
        suggest_log(verbose, f"     after cluster (radius={cluster_radius_m} m): {peak_stats['clustered_count']}")
        suggest_log(verbose, f"     shortlist cap: {max_candidates}  kept: {len(peaks)}")
    out.extend(peaks)
    if iteration > 0 or not peaks:
        gap = _gap_fill_candidate(grid=grid, goal_depth=goal_depth, eligible_ll=eligible_ll)
        if gap is not None:
            if verbose:
                suggest_log(
                    verbose,
                    f"site suggest:   gap-fill candidate @ {gap.lat:.6f}, {gap.lon:.6f}",
                )
            out.append(gap)
        elif verbose:
            suggest_log(verbose, "site suggest:   gap-fill: no uncovered patch centroid")
    dedup: list[SiteCandidate] = []
    seen: set[tuple[int, int]] = set()
    for c in out:
        key = (int(round(c.lat * 1e4)), int(round(c.lon * 1e4)))
        if key in seen:
            continue
        seen.add(key)
        dedup.append(c)
    return dedup[: max(1, int(max_candidates))]
