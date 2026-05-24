"""Candidate site locations on eligible land."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from pyproj import Transformer
from shapely import make_valid
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

from peaky_finders.site_suggestions.depth_grid import CoverageDepthGrid, largest_uncovered_patch_centroid_ll
from peaky_finders.site_suggestions.eligible_peaks_cache import load_or_build_eligible_peaks
from peaky_finders.site_suggestions.log import suggest_log, suggest_progress, suggest_step


@dataclass(frozen=True)
class SiteCandidate:
    lat: float
    lon: float
    elev_m: float | None
    strategy: str


def _precluster_peak_cap(*, max_candidates: int) -> int:
    return max(512, int(max_candidates) * 128)


def _cluster_points_by_buffer(
    points: list[SiteCandidate],
    *,
    cluster_radius_m: float,
    verbose: bool = False,
) -> list[SiteCandidate]:
    if not points:
        return []
    if cluster_radius_m <= 0 or len(points) == 1:
        return points

    half = float(cluster_radius_m) / 2.0
    to_m = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    lons = np.fromiter((c.lon for c in points), dtype=np.float64, count=len(points))
    lats = np.fromiter((c.lat for c in points), dtype=np.float64, count=len(points))
    xs, ys = to_m.transform(lons, lats)
    metric = [(points[i], float(xs[i]), float(ys[i])) for i in range(len(points))]

    kept: list[SiteCandidate] = []
    used = [False] * len(metric)
    report_every = max(1, len(metric) // 10)
    for i, (ci, xi, yi) in enumerate(metric):
        if used[i]:
            continue
        if verbose and i > 0 and i % report_every == 0:
            suggest_progress(verbose, f"spatial cluster scanned {i}/{len(metric)} peaks, {len(kept)} cluster(s) so far")
        cluster = [ci]
        used[i] = True
        buf_i = Point(xi, yi).buffer(half)
        for j in range(i + 1, len(metric)):
            if used[j]:
                continue
            cj, xj, yj = metric[j]
            if buf_i.intersects(Point(xj, yj).buffer(half)):
                used[j] = True
                cluster.append(cj)
        best = max(
            cluster,
            key=lambda c: (c.elev_m is not None, c.elev_m or -1.0),
        )
        kept.append(best)
    return kept


def _prefilter_peaks_by_elevation(
    peaks_llz: list[tuple[float, float, float]],
    *,
    cap: int,
    verbose: bool,
) -> list[tuple[float, float, float]]:
    if len(peaks_llz) <= cap:
        return peaks_llz
    suggest_log(verbose, f"site suggest:   → elevation prefilter ({len(peaks_llz)} → top {cap} by elev)…")
    ranked = sorted(peaks_llz, key=lambda p: (-p[2], p[0], p[1]))
    kept = ranked[:cap]
    suggest_log(verbose, f"site suggest:   ✓ elevation prefilter kept {len(kept)} peak(s)")
    return kept


def _eligible_peak_candidates(
    *,
    eligible_ll: BaseGeometry,
    grid: CoverageDepthGrid,
    goal_depth: int,
    dem_mirror_root: Path,
    suggest_root: Path,
    eligible_sha: str,
    max_candidates: int,
    cluster_radius_m: float,
    jobs: int = 1,
    verbose: bool = False,
    return_stats: bool = False,
) -> tuple[list[SiteCandidate], dict[str, int] | None]:
    elig = make_valid(eligible_ll) if not eligible_ll.is_valid else eligible_ll
    if elig.is_empty:
        return [], None

    with suggest_step(verbose, "load eligible peaks index"):
        peaks_llz, from_cache = load_or_build_eligible_peaks(
            suggest_root=suggest_root,
            eligible_sha=eligible_sha,
            eligible_ll=elig,
            dem_mirror_root=dem_mirror_root,
            bin_size_m=float(cluster_radius_m),
            jobs=jobs,
            verbose=verbose,
        )
        suggest_log(
            verbose,
            f"site suggest:     {len(peaks_llz)} peak(s) ({'cache' if from_cache else 'fresh scan'})",
        )

    suggest_log(verbose, f"site suggest:   → uncovered grid filter ({len(peaks_llz)} peaks)…")
    uncovered_peaks = grid.filter_peaks_llz_by_uncovered_grid(
        peaks_llz,
        goal_depth=goal_depth,
        verbose=verbose,
    )
    suggest_log(
        verbose,
        f"site suggest:   ✓ uncovered grid filter kept {len(uncovered_peaks)}/{len(peaks_llz)} peak(s)",
    )
    if not uncovered_peaks:
        return [], None

    precluster_cap = _precluster_peak_cap(max_candidates=max_candidates)
    ranked_peaks = _prefilter_peaks_by_elevation(
        uncovered_peaks,
        cap=precluster_cap,
        verbose=verbose,
    )

    raw = [
        SiteCandidate(
            lat=float(lat),
            lon=float(lon),
            elev_m=float(elev_m),
            strategy="peak",
        )
        for lon, lat, elev_m in ranked_peaks
    ]

    with suggest_step(verbose, f"spatial cluster ({len(raw)} peaks, radius={cluster_radius_m} m)"):
        clustered = _cluster_points_by_buffer(
            raw,
            cluster_radius_m=cluster_radius_m,
            verbose=verbose,
        )
        suggest_log(verbose, f"site suggest:     {len(clustered)} cluster representative(s)")
    kept = clustered[: max(1, int(max_candidates))]

    stats = None
    if return_stats:
        stats = {
            "eligible_peaks_total": len(peaks_llz),
            "uncovered_peaks": len(uncovered_peaks),
            "prefilter_peaks": len(ranked_peaks),
            "dem_peaks_raw": len(raw),
            "clustered_count": len(clustered),
            "from_cache": int(from_cache),
        }
    return kept, stats


def _gap_fill_candidate(
    *,
    grid: CoverageDepthGrid,
    goal_depth: int,
    eligible_ll: BaseGeometry,
    verbose: bool = False,
) -> SiteCandidate | None:
    with suggest_step(verbose, "gap-fill largest uncovered patch"):
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
    dem_mirror_root: Path,
    suggest_root: Path,
    eligible_sha: str,
    max_candidates: int,
    cluster_radius_m: float,
    iteration: int,
    jobs: int = 1,
    verbose: bool = False,
) -> list[SiteCandidate]:
    """Peak-biased shortlist, with gap centroid on later iterations."""
    out: list[SiteCandidate] = []
    peaks, peak_stats = _eligible_peak_candidates(
        eligible_ll=eligible_ll,
        grid=grid,
        goal_depth=goal_depth,
        dem_mirror_root=dem_mirror_root,
        suggest_root=suggest_root,
        eligible_sha=eligible_sha,
        max_candidates=max_candidates,
        cluster_radius_m=cluster_radius_m,
        jobs=jobs,
        verbose=verbose,
        return_stats=verbose,
    )
    if verbose and peak_stats is not None:
        suggest_log(verbose, "site suggest: ── iteration peak shortlist summary ──")
        suggest_log(verbose, f"     eligible peaks (cached index): {peak_stats['eligible_peaks_total']}")
        suggest_log(verbose, f"     still uncovered on grid: {peak_stats['uncovered_peaks']}")
        suggest_log(verbose, f"     elevation prefilter kept: {peak_stats['prefilter_peaks']}")
        suggest_log(
            verbose,
            f"     after cluster (radius={cluster_radius_m} m): {peak_stats['clustered_count']}",
        )
        suggest_log(verbose, f"     shortlist cap: {max_candidates}  kept: {len(peaks)}")
    out.extend(peaks)
    if iteration > 0 or not peaks:
        gap = _gap_fill_candidate(
            grid=grid,
            goal_depth=goal_depth,
            eligible_ll=eligible_ll,
            verbose=verbose,
        )
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
