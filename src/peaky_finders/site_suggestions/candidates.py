"""Candidate site locations on eligible land."""

from __future__ import annotations

from dataclasses import dataclass

import geopandas as gpd
from shapely import make_valid
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

from peaky_finders.pairwise_dem_peak import skadi_binned_peaks_in_polygon
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

    need_ll = grid.uncovered_geometry_wgs84(goal_depth=goal_depth)
    if need_ll is None or need_ll.is_empty:
        return [], None

    search = elig.intersection(need_ll)
    if search.is_empty:
        return [], None
    if not search.is_valid:
        search = make_valid(search)
        if search.is_empty:
            return [], None

    peaks_llz = skadi_binned_peaks_in_polygon(
        search,
        dem_mirror_root,
        bin_size_m=float(cluster_radius_m),
    )
    raw = [
        SiteCandidate(
            lat=float(lat),
            lon=float(lon),
            elev_m=float(elev_m),
            strategy="peak",
        )
        for lon, lat, elev_m in peaks_llz
    ]
    clustered = _cluster_points_by_buffer(raw, cluster_radius_m=cluster_radius_m)
    kept = clustered[: max(1, int(max_candidates))]
    stats = None
    if return_stats:
        stats = {
            "dem_peaks_raw": len(raw),
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
        suggest_log(
            verbose,
            f"     masked DEM peaks (eligible ∩ uncovered): {peak_stats['dem_peaks_raw']}",
        )
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
