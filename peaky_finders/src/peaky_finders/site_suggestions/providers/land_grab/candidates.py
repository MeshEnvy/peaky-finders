"""Land-grab candidate generation (peak clusters + gap-fill)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import numpy as np
from pyproj import Transformer
from shapely import make_valid
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

from peaky_finders.site_suggestions.candidates import (
    SiteCandidate,
    _dedupe_candidates,
    _grid_samples_around_point,
)
from peaky_finders.site_suggestions.depth_grid import CoverageDepthGrid, largest_uncovered_patch_centroid_ll
from peaky_finders.site_suggestions.eligible_peaks_cache import load_or_build_eligible_peaks
from peaky_finders.site_suggestions.log import suggest_log, suggest_progress, suggest_step, SuggestProgressTicker
from peaky_finders.sites_job import LandGrabStrategyConfig


@dataclass(frozen=True)
class _PeakCluster:
    center: SiteCandidate
    members: tuple[SiteCandidate, ...]


def _precluster_peak_cap(*, max_clusters: int) -> int:
    """Cap peaks before spatial clustering (only need enough to form ``max_clusters`` groups)."""
    return max(512, int(max_clusters) * 512)


def _cluster_points_by_buffer(
    points: list[SiteCandidate],
    *,
    cluster_radius_m: float,
    verbose: bool = False,
) -> list[_PeakCluster]:
    """Greedy radius clustering on EPSG:3857 using a grid spatial index."""
    if not points:
        return []
    if cluster_radius_m <= 0 or len(points) == 1:
        return [_PeakCluster(center=points[0], members=(points[0],))]

    radius_m = float(cluster_radius_m)
    radius2 = radius_m * radius_m
    cell_m = max(radius_m, 1.0)
    n = len(points)

    to_m = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    lons = np.fromiter((c.lon for c in points), dtype=np.float64, count=n)
    lats = np.fromiter((c.lat for c in points), dtype=np.float64, count=n)
    xs, ys = to_m.transform(lons, lats)
    xs = np.asarray(xs, dtype=np.float64)
    ys = np.asarray(ys, dtype=np.float64)
    bx = np.floor(xs / cell_m).astype(np.int64)
    by = np.floor(ys / cell_m).astype(np.int64)

    buckets: dict[tuple[int, int], list[int]] = defaultdict(list)
    for i in range(n):
        buckets[(int(bx[i]), int(by[i]))].append(i)

    neighbor_offsets = tuple((dx, dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1))

    def _neighbor_indices(i: int) -> list[int]:
        out: list[int] = []
        cbx = int(bx[i])
        cby = int(by[i])
        for dx, dy in neighbor_offsets:
            out.extend(buckets.get((cbx + dx, cby + dy), ()))
        return out

    used = np.zeros(n, dtype=bool)
    clusters: list[_PeakCluster] = []
    ticker = SuggestProgressTicker(verbose, label="spatial cluster", interval_s=0.5)
    seeds_done = 0

    if verbose:
        suggest_log(
            verbose,
            f"site suggest:     spatial index cluster start: {n} peak(s), "
            f"radius={radius_m:.0f} m, cell={cell_m:.0f} m",
        )

    for i in range(n):
        if used[i]:
            continue
        seeds_done += 1
        ticker.maybe(
            f"seed {seeds_done} at index {i}/{n}, {len(clusters) + 1} cluster(s) forming",
        )

        group = [points[i]]
        used[i] = True
        xi = xs[i]
        yi = ys[i]
        for j in _neighbor_indices(i):
            if j <= i or used[j]:
                continue
            dx = xs[j] - xi
            dy = ys[j] - yi
            if dx * dx + dy * dy <= radius2:
                used[j] = True
                group.append(points[j])

        center = max(
            group,
            key=lambda c: (c.elev_m is not None, c.elev_m or -1.0),
        )
        clusters.append(_PeakCluster(center=center, members=tuple(group)))

    if verbose:
        ticker.done(f"{len(clusters)} cluster(s) from {n} peak(s)")
    return clusters


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
    dem_mirror_root,
    suggest_root,
    eligible_sha: str,
    cfg: LandGrabStrategyConfig,
    jobs: int = 1,
    verbose: bool = False,
    return_stats: bool = False,
) -> tuple[list[SiteCandidate], dict[str, int] | None]:
    elig = make_valid(eligible_ll) if not eligible_ll.is_valid else eligible_ll
    if elig.is_empty:
        return [], None

    max_clusters = int(cfg.max_clusters_per_round)
    spacing_m = float(cfg.cluster_sample_spacing_m)
    radius_m = float(cfg.cluster_sample_radius_m)

    with suggest_step(verbose, "load eligible peaks index"):
        peaks_llz, from_cache = load_or_build_eligible_peaks(
            suggest_root=suggest_root,
            eligible_sha=eligible_sha,
            eligible_ll=elig,
            dem_mirror_root=dem_mirror_root,
            bin_size_m=float(cfg.peak_cluster_radius_m),
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

    precluster_cap = _precluster_peak_cap(max_clusters=max_clusters)
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

    with suggest_step(verbose, f"spatial cluster ({len(raw)} peaks, radius={cfg.peak_cluster_radius_m} m)"):
        clusters = _cluster_points_by_buffer(
            raw,
            cluster_radius_m=float(cfg.peak_cluster_radius_m),
            verbose=verbose,
        )
        suggest_log(verbose, f"site suggest:     {len(clusters)} cluster(s)")

    clusters = clusters[: max(1, max_clusters)]

    with suggest_step(
        verbose,
        f"cluster grid samples ({len(clusters)} cluster(s), spacing={spacing_m} m, radius={radius_m} m)",
    ):
        sampled: list[SiteCandidate] = []
        for cluster in clusters:
            sampled.extend(
                _grid_samples_around_point(
                    cluster.center,
                    spacing_m=spacing_m,
                    radius_m=radius_m,
                    eligible_ll=elig,
                    strategy="cluster",
                )
            )
        sampled = _dedupe_candidates(sampled)
        suggest_log(verbose, f"site suggest:     {len(sampled)} grid sample(s) on eligible land")

    suggest_log(verbose, f"site suggest:   → uncovered grid filter ({len(sampled)} grid samples)…")
    sample_llz = [(c.lon, c.lat, c.elev_m or 0.0) for c in sampled]
    uncovered_samples = grid.filter_peaks_llz_by_uncovered_grid(
        sample_llz,
        goal_depth=goal_depth,
        verbose=verbose,
    )
    uncovered_keys = {(round(lon, 5), round(lat, 5)) for lon, lat, _ in uncovered_samples}
    kept = [c for c in sampled if (round(c.lon, 5), round(c.lat, 5)) in uncovered_keys]
    suggest_log(
        verbose,
        f"site suggest:   ✓ grid sample filter kept {len(kept)}/{len(sampled)} placement(s)",
    )

    cap = max(1, int(cfg.max_candidates_per_round))
    kept = kept[:cap]

    stats = None
    if return_stats:
        stats = {
            "eligible_peaks_total": len(peaks_llz),
            "uncovered_peaks": len(uncovered_peaks),
            "prefilter_peaks": len(ranked_peaks),
            "dem_peaks_raw": len(raw),
            "clustered_count": len(clusters),
            "grid_samples": len(sampled),
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
