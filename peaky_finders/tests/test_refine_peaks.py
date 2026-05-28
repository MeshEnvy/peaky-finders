"""Regional peak refine candidates for mesh-backbone."""

from __future__ import annotations

from shapely.geometry import box

from peaky_finders.site_suggestions.candidates import SiteCandidate
from peaky_finders.site_suggestions.refine_peaks import regional_peak_candidates_around_centers
from peaky_finders.site_suggestions.strategies.base import StrategyRefineSettings
from peaky_finders.site_suggestions.strategies.mesh_backbone import MeshBackboneStrategy
from peaky_finders.sites_job import BundleSiteSuggestionsConfig, SiteSuggestionStrategy


def test_regional_peaks_ranks_by_elevation_within_radius() -> None:
    eligible = box(-116.0, 36.0, -115.0, 37.0)
    center = SiteCandidate(lat=36.5, lon=-115.5, elev_m=None, strategy="refine_seed")
    peaks = [
        (-115.501, 36.501, 1800.0),
        (-115.499, 36.499, 2100.0),
        (-115.48, 36.48, 3000.0),
    ]
    out = regional_peak_candidates_around_centers(
        peaks_llz=peaks,
        centers=[center],
        radius_m=3000.0,
        per_seed_cap=2,
        eligible_ll=eligible,
    )
    assert len(out) == 2
    assert out[0].elev_m == 2100.0
    assert out[0].strategy == "refine_peak"
    assert out[1].elev_m == 1800.0


def test_regional_peaks_excludes_outside_radius() -> None:
    eligible = box(-116.0, 36.0, -115.0, 37.0)
    center = SiteCandidate(lat=36.5, lon=-115.5, elev_m=None, strategy="refine_seed")
    peaks = [
        (-115.501, 36.501, 1800.0),
        (-115.0, 36.0, 4000.0),
    ]
    out = regional_peak_candidates_around_centers(
        peaks_llz=peaks,
        centers=[center],
        radius_m=500.0,
        per_seed_cap=8,
        eligible_ll=eligible,
    )
    assert len(out) == 1
    assert out[0].elev_m == 1800.0


def test_regional_peaks_per_seed_cap() -> None:
    eligible = box(-116.0, 36.0, -115.0, 37.0)
    center = SiteCandidate(lat=36.5, lon=-115.5, elev_m=None, strategy="refine_seed")
    peaks = [
        (-115.501 + i * 0.0001, 36.501 + i * 0.0001, 1000.0 + i)
        for i in range(10)
    ]
    out = regional_peak_candidates_around_centers(
        peaks_llz=peaks,
        centers=[center],
        radius_m=500.0,
        per_seed_cap=3,
        eligible_ll=eligible,
    )
    assert len(out) == 3


def test_mesh_backbone_refine_settings_include_peaks() -> None:
    cfg = BundleSiteSuggestionsConfig(strategy=SiteSuggestionStrategy.MESH_BACKBONE)
    refine = MeshBackboneStrategy().refine_settings(cfg)
    assert refine.refine_peaks_enabled is True
    assert refine.refine_peak_radius_m == 400.0
    assert refine.refine_peak_bin_size_m == 150.0
    assert refine.refine_peaks_per_seed == 8
    mb = cfg.mesh_backbone
    assert mb.coarse_peaks_enabled is True
    assert mb.coarse_peak_radius_m == 750.0
    assert mb.coarse_peaks_per_sample == 2


def test_attach_frontier_peak_candidates() -> None:
    from pathlib import Path
    from unittest.mock import patch

    from peaky_finders.site_suggestions.context import SiteSuggestionContext
    from peaky_finders.site_suggestions.refine_peaks import attach_frontier_peak_candidates
    from peaky_finders.sites_job import MeshBackboneStrategyConfig

    eligible = box(-116.0, 36.0, -115.0, 37.0)
    frontier = [
        SiteCandidate(lat=36.5, lon=-115.5, elev_m=None, strategy="goal:g1"),
    ]
    cfg = MeshBackboneStrategyConfig(
        coarse_peaks_enabled=True,
        coarse_peak_radius_m=1000.0,
        coarse_peaks_per_sample=2,
    )
    ctx = SiteSuggestionContext(
        preset=type("P", (), {"sites": {}})(),
        plan=type("Plan", (), {"viewshed_workspaces": ()})(),
        grid=type("G", (), {"depth_at_point": lambda *a, **k: 0})(),
        eligible_ll=eligible,
        aoi_ll=eligible,
        target_ll=eligible,
        suggest_root=Path("/tmp/suggest"),
        cfg=BundleSiteSuggestionsConfig(strategy=SiteSuggestionStrategy.MESH_BACKBONE, mesh_backbone=cfg),
        dem_mirror_root=Path("/tmp/dem"),
        eligible_sha="x",
        jobs=1,
        verbose=False,
    )
    peaks = [(-115.501, 36.501, 2500.0)]

    with patch(
        "peaky_finders.site_suggestions.refine_peaks.load_or_build_eligible_peaks",
        return_value=(peaks, True),
    ):
        out = attach_frontier_peak_candidates(
            ctx=ctx,
            candidates=frontier,
            eligible_ll=eligible,
            verbose=False,
        )

    assert len(out) == 2
    assert out[0].strategy == "goal:g1"
    assert any(c.strategy == "frontier_peak" and c.elev_m == 2500.0 for c in out)


def test_strategy_refine_settings_peak_defaults_off_for_land_grab() -> None:
    from peaky_finders.site_suggestions.strategies.land_grab import LandGrabStrategy

    cfg = BundleSiteSuggestionsConfig(strategy=SiteSuggestionStrategy.LAND_GRAB)
    refine = LandGrabStrategy().refine_settings(cfg)
    assert refine.refine_peaks_enabled is False
