"""Regional Skadi peaks for mesh-backbone refine search."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from pyproj import Transformer
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

from peaky_finders.site_suggestions.candidates import SiteCandidate, _dedupe_candidates, generate_refine_candidates
from peaky_finders.site_suggestions.context import SiteSuggestionContext
from peaky_finders.site_suggestions.eligible_peaks_cache import load_or_build_eligible_peaks
from peaky_finders.site_suggestions.log import suggest_log, suggest_progress, suggest_step, SuggestProgressTicker
from peaky_finders.site_suggestions.strategies.base import StrategyRefineSettings

_TO_M = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)


def regional_peak_candidates_around_centers(
    *,
    peaks_llz: Sequence[tuple[float, float, float]],
    centers: Sequence[SiteCandidate],
    radius_m: float,
    per_seed_cap: int,
    eligible_ll: BaseGeometry,
    strategy: str = "refine_peak",
    verbose: bool = False,
) -> list[SiteCandidate]:
    """Return Skadi peaks within ``radius_m`` of each center, capped per seed by elevation."""
    if not peaks_llz or not centers or int(per_seed_cap) <= 0 or float(radius_m) <= 0:
        return []

    radius2 = float(radius_m) * float(radius_m)
    cap = max(1, int(per_seed_cap))
    lons = np.fromiter((float(p[0]) for p in peaks_llz), dtype=np.float64, count=len(peaks_llz))
    lats = np.fromiter((float(p[1]) for p in peaks_llz), dtype=np.float64, count=len(peaks_llz))
    elevs = np.fromiter((float(p[2]) for p in peaks_llz), dtype=np.float64, count=len(peaks_llz))
    px, py = _TO_M.transform(lons, lats)

    out: list[SiteCandidate] = []
    ticker = SuggestProgressTicker(verbose, label="coarse peaks", interval_s=0.5)
    for center_idx, center in enumerate(centers, start=1):
        cx, cy = _TO_M.transform(float(center.lon), float(center.lat))
        dist2 = (px - cx) ** 2 + (py - cy) ** 2
        sel = dist2 <= radius2
        if not np.any(sel):
            continue

        idx = np.flatnonzero(sel)
        order = sorted(
            idx.tolist(),
            key=lambda i: (-float(elevs[i]), float(dist2[i]), float(lons[i]), float(lats[i])),
        )
        kept = 0
        for i in order:
            lon = float(lons[i])
            lat = float(lats[i])
            pt = Point(lon, lat)
            if not eligible_ll.intersects(pt):
                continue
            out.append(
                SiteCandidate(
                    lat=lat,
                    lon=lon,
                    elev_m=float(elevs[i]),
                    strategy=strategy,
                )
            )
            kept += 1
            if kept >= cap:
                break
        ticker.maybe(f"seed [{center_idx}/{len(centers)}], {len(out)} peak(s) kept")
    if verbose and centers:
        ticker.done(f"{len(out)} peak(s) from {len(centers)} seed(s)")
    return _dedupe_candidates(out)


def generate_mesh_refine_candidates(
    *,
    centers: list[SiteCandidate],
    eligible_ll: BaseGeometry,
    refine: StrategyRefineSettings,
    ctx: SiteSuggestionContext | None = None,
    verbose: bool = False,
) -> list[SiteCandidate]:
    """Flat grid around refine seeds plus optional regional Skadi peaks."""
    grid = generate_refine_candidates(
        centers=centers,
        eligible_ll=eligible_ll,
        refine_radius_m=float(refine.refine_radius_m),
        refine_spacing_m=float(refine.refine_spacing_m),
    )
    if not refine.refine_peaks_enabled or int(refine.refine_peaks_per_seed) <= 0 or not centers:
        return grid

    if ctx is None:
        suggest_log(verbose, "site suggest:     refine peaks skipped (no suggest context)")
        return grid

    with suggest_step(verbose, "load eligible peaks for refine"):
        peaks_llz, from_cache = load_or_build_eligible_peaks(
            suggest_root=ctx.suggest_root,
            eligible_sha=ctx.eligible_sha,
            eligible_ll=eligible_ll,
            dem_mirror_root=ctx.dem_mirror_root,
            bin_size_m=float(refine.refine_peak_bin_size_m),
            jobs=ctx.jobs,
            verbose=verbose,
        )
        suggest_log(
            verbose,
            f"site suggest:     {len(peaks_llz)} peak(s) for refine ({'cache' if from_cache else 'fresh scan'})",
        )

    peak_cands = regional_peak_candidates_around_centers(
        peaks_llz=peaks_llz,
        centers=centers,
        radius_m=float(refine.refine_peak_radius_m),
        per_seed_cap=int(refine.refine_peaks_per_seed),
        eligible_ll=eligible_ll,
    )
    suggest_log(
        verbose,
        f"site suggest:     refine peaks: {len(peak_cands)} within "
        f"{refine.refine_peak_radius_m:.0f} m of {len(centers)} seed(s)",
    )
    return _dedupe_candidates([*grid, *peak_cands])


def attach_frontier_peak_candidates(
    *,
    ctx: SiteSuggestionContext,
    candidates: list[SiteCandidate],
    eligible_ll: BaseGeometry,
    verbose: bool = False,
) -> list[SiteCandidate]:
    """Union frontier samples with nearby Skadi peaks (coarse pass)."""
    mb = ctx.cfg.mesh_backbone
    if not mb.coarse_peaks_enabled or int(mb.coarse_peaks_per_sample) <= 0 or not candidates:
        return candidates

    with suggest_step(verbose, f"load eligible peaks for coarse ({len(candidates)} frontier sample(s))"):
        peaks_llz, from_cache = load_or_build_eligible_peaks(
            suggest_root=ctx.suggest_root,
            eligible_sha=ctx.eligible_sha,
            eligible_ll=eligible_ll,
            dem_mirror_root=ctx.dem_mirror_root,
            bin_size_m=float(mb.refine_peak_bin_size_m),
            jobs=ctx.jobs,
            verbose=verbose,
        )
        suggest_log(
            verbose,
            f"site suggest:     {len(peaks_llz)} peak(s) for coarse ({'cache' if from_cache else 'fresh scan'})",
        )

    peak_cands = regional_peak_candidates_around_centers(
        peaks_llz=peaks_llz,
        centers=candidates,
        radius_m=float(mb.coarse_peak_radius_m),
        per_seed_cap=int(mb.coarse_peaks_per_sample),
        eligible_ll=eligible_ll,
        strategy="frontier_peak",
        verbose=verbose,
    )
    suggest_log(
        verbose,
        f"site suggest:     coarse frontier peaks: {len(peak_cands)} within "
        f"{mb.coarse_peak_radius_m:.0f} m of frontier sample(s)",
    )
    return _dedupe_candidates([*candidates, *peak_cands])
