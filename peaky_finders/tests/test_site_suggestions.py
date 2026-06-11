"""Site suggestion planner and preset IO."""

from __future__ import annotations

import time
from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
from pyproj import Transformer
from shapely.geometry import Point, box

from peaky_finders.site_suggestions.depth_grid import build_coverage_depth_grid
from peaky_finders.site_suggestions.planner import plan_greedy_site_suggestions, planned_to_preset_entries
from peaky_finders.site_suggestions.preset_io import append_suggested_sites_to_preset, remove_suggested_sites_from_preset
from peaky_finders.sites_job import SiteType, load_preset, write_preset_document
from rf_fixtures import MINIMAL_SIMULATION


_MINIMAL_LAND = {
    "layers": {
        "aoi_layer": {"role": "aoi", "path": "aoi/test.gdb", "layers": [{"name": "boundary"}]},
        "inc_layer": {"role": "positive", "path": "include/test.gdb", "layers": [{"name": "inc_layer"}]},
    }
}

_SUGGEST_SIMULATION = MINIMAL_SIMULATION


def _write_gpkg(path: Path, geom, *, layer: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    gpd.GeoDataFrame(geometry=[geom], crs="EPSG:4326").to_file(path, driver="GPKG", layer=layer)


def test_site_type_defaults_to_installed(tmp_path: Path) -> None:
    p = tmp_path / "job.yaml"
    write_preset_document(
        p,
        {
            "simulation": dict(_SUGGEST_SIMULATION),
            "display": {"colormap": "rainbow", "min_dbm": -130.0, "max_dbm": -80.0},
            "sites": {"a": {"name": "A", "loc": [39.0, -119.0]}},
        },
    )
    job = load_preset(p)
    assert job.sites["a"].type == SiteType.INSTALLED


def test_depth_grid_marginal_gain(tmp_path: Path) -> None:
    a = tmp_path / "a.gpkg"
    _write_gpkg(a, box(-115.02, 39.01, -115.00, 39.03), layer="coverage")
    aoi = box(-115.05, 39.00, -114.98, 39.05)
    grid = build_coverage_depth_grid(
        aoi_ll=aoi,
        target_ll=aoi,
        footprint_gpkg_paths=[a],
        max_raster_dimension=256,
    )
    new_fp = box(-115.04, 39.02, -114.99, 39.04)
    gain = grid.marginal_gain_cells(new_fp, goal_depth=1)
    assert gain > 0
    grid.add_footprint(new_fp)
    assert grid.marginal_gain_cells(new_fp, goal_depth=1) == 0


def test_uncovered_geometry_wgs84_excludes_covered_aoi(tmp_path: Path) -> None:
    covered = box(-115.02, 39.01, -115.00, 39.03)
    a = tmp_path / "a.gpkg"
    _write_gpkg(a, covered, layer="coverage")
    aoi = box(-115.05, 39.00, -114.98, 39.05)
    grid = build_coverage_depth_grid(
        aoi_ll=aoi,
        target_ll=aoi,
        footprint_gpkg_paths=[a],
        max_raster_dimension=256,
    )
    need = grid.uncovered_geometry_wgs84(goal_depth=1)
    assert need is not None
    assert not need.intersects(covered.centroid)


def test_point_marginal_gain_cells(tmp_path: Path) -> None:
    a = tmp_path / "a.gpkg"
    covered = box(-115.02, 39.01, -115.00, 39.03)
    _write_gpkg(a, covered, layer="coverage")
    aoi = box(-115.05, 39.00, -114.98, 39.05)
    grid = build_coverage_depth_grid(
        aoi_ll=aoi,
        target_ll=aoi,
        footprint_gpkg_paths=[a],
        max_raster_dimension=256,
    )
    assert grid.point_marginal_gain_cells(-115.01, 39.02, goal_depth=1) == 0
    assert grid.point_marginal_gain_cells(-115.04, 39.04, goal_depth=1) == 1


def test_filter_peaks_llz_matches_point_lookup(tmp_path: Path) -> None:
    a = tmp_path / "a.gpkg"
    covered = box(-115.02, 39.01, -115.00, 39.03)
    _write_gpkg(a, covered, layer="coverage")
    aoi = box(-115.05, 39.00, -114.98, 39.05)
    grid = build_coverage_depth_grid(
        aoi_ll=aoi,
        target_ll=aoi,
        footprint_gpkg_paths=[a],
        max_raster_dimension=256,
    )
    peaks = [
        (-115.01, 39.02, 2500.0),
        (-115.04, 39.04, 3000.0),
        (-114.99, 39.01, 2800.0),
    ]
    batch = grid.filter_peaks_llz_by_uncovered_grid(peaks, goal_depth=1)
    point = [p for p in peaks if grid.point_marginal_gain_cells(p[0], p[1], goal_depth=1) > 0]
    assert batch == point


def test_marginal_gain_ignores_ineligible_target_cells(tmp_path: Path) -> None:
    aoi = box(-115.05, 39.00, -114.98, 39.05)
    eligible = box(-115.035, 39.01, -114.99, 39.04)
    grid = build_coverage_depth_grid(
        aoi_ll=aoi,
        target_ll=eligible,
        footprint_gpkg_paths=[],
        max_raster_dimension=256,
    )
    ineligible_only = box(-115.06, 39.00, -115.045, 39.05)
    eligible_patch = box(-115.03, 39.02, -115.005, 39.035)

    assert grid.marginal_gain_cells(ineligible_only, goal_depth=1) == 0
    assert grid.marginal_gain_cells(eligible_patch, goal_depth=1) > 0


def test_eligible_peak_candidates_use_masked_dem(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from peaky_finders import pairwise_dem_peak as dem_peak
    from peaky_finders.site_suggestions.providers.land_grab.candidates import _eligible_peak_candidates
    from peaky_finders.site_suggestions.candidates import _grid_samples_around_point
    from peaky_finders.sites_job import SuggestConfig, LandGrabStrategyConfig
    from rasterio.transform import from_bounds

    aoi = box(-115.0, 39.0, -114.0, 40.0)
    eligible = box(-115.0, 39.0, -114.0, 40.0)
    grid = build_coverage_depth_grid(
        aoi_ll=aoi,
        target_ll=eligible,
        footprint_gpkg_paths=[],
        max_raster_dimension=256,
    )

    transform = from_bounds(-115.0, 39.0, -114.0, 40.0, 21, 21)
    elev = np.full((21, 21), 2000, dtype=np.int32)
    elev[15, 5] = 3200
    mirror = tmp_path / "dem"
    mirror.mkdir()
    (mirror / "N39W115.hgt.gz").write_bytes(b"x")
    aff = tuple(getattr(transform, attr) for attr in ("a", "b", "c", "d", "e", "f"))
    monkeypatch.setattr(
        dem_peak,
        "_cached_skadi_elev_affine",
        lambda _p: (elev, aff),
    )

    peaks, stats = _eligible_peak_candidates(
        eligible_ll=eligible,
        grid=grid,
        goal_depth=1,
        dem_mirror_root=mirror,
        suggest_root=tmp_path / "suggest",
        eligible_sha="test-eligible",
        cfg=SuggestConfig(
            land_grab=LandGrabStrategyConfig(
                max_clusters_per_round=4,
                max_candidates_per_round=4,
                peak_cluster_radius_m=1500.0,
                cluster_sample_spacing_m=250.0,
                cluster_sample_radius_m=750.0,
            ),
        ).land_grab,
        return_stats=True,
    )

    assert stats is not None
    assert stats["eligible_peaks_total"] >= 1
    assert stats["uncovered_peaks"] >= 1
    assert peaks
    assert max(c.elev_m or 0.0 for c in peaks) == 3200.0


def test_grid_samples_around_point_on_eligible() -> None:
    from peaky_finders.site_suggestions.candidates import SiteCandidate, _grid_samples_around_point

    eligible = box(-115.0, 39.0, -114.0, 40.0)
    center = SiteCandidate(lat=39.5, lon=-114.5, elev_m=3000.0, strategy="peak")
    samples = _grid_samples_around_point(
        center,
        spacing_m=100.0,
        radius_m=150.0,
        eligible_ll=eligible,
        strategy="cluster",
    )
    assert len(samples) > 1
    assert all(c.strategy == "cluster" for c in samples)


def test_spatial_index_cluster_matches_buffer_greedy() -> None:
    from peaky_finders.site_suggestions.candidates import SiteCandidate
    from peaky_finders.site_suggestions.providers.land_grab.candidates import _cluster_points_by_buffer

    def _brute_cluster(
        points: list[SiteCandidate],
        *,
        cluster_radius_m: float,
    ) -> list[tuple[tuple[float, float], int]]:
        if not points:
            return []
        half = float(cluster_radius_m) / 2.0
        to_m = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
        metric = []
        for c in points:
            x, y = to_m.transform(c.lon, c.lat)
            metric.append((c, float(x), float(y)))
        used = [False] * len(metric)
        out: list[tuple[tuple[float, float], int]] = []
        for i, (ci, xi, yi) in enumerate(metric):
            if used[i]:
                continue
            group = [ci]
            used[i] = True
            buf_i = Point(xi, yi).buffer(half)
            for j in range(i + 1, len(metric)):
                if used[j]:
                    continue
                cj, xj, yj = metric[j]
                if buf_i.intersects(Point(xj, yj).buffer(half)):
                    used[j] = True
                    group.append(cj)
            center = max(group, key=lambda c: (c.elev_m is not None, c.elev_m or -1.0))
            out.append(((center.lat, center.lon), len(group)))
        return out

    rng = np.random.default_rng(0)
    to_m = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    from_m = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
    cx, cy = to_m.transform(-115.0, 39.5)
    pts: list[SiteCandidate] = []
    for k in range(120):
        x = cx + float(rng.uniform(-5000.0, 5000.0))
        y = cy + float(rng.uniform(-5000.0, 5000.0))
        lon, lat = from_m.transform(x, y)
        pts.append(SiteCandidate(lat=float(lat), lon=float(lon), elev_m=float(k), strategy="peak"))

    radius_m = 1500.0
    got = _cluster_points_by_buffer(pts, cluster_radius_m=radius_m, verbose=False)
    want = _brute_cluster(pts, cluster_radius_m=radius_m)
    got_sig = sorted(((c.center.lat, c.center.lon), len(c.members)) for c in got)
    assert got_sig == sorted(want)


def test_spatial_index_cluster_many_peaks_fast() -> None:
    from peaky_finders.site_suggestions.candidates import SiteCandidate
    from peaky_finders.site_suggestions.providers.land_grab.candidates import _cluster_points_by_buffer

    pts = [
        SiteCandidate(lat=39.0 + i * 0.001, lon=-115.0 + (i % 17) * 0.001, elev_m=float(i), strategy="peak")
        for i in range(8000)
    ]
    t0 = time.perf_counter()
    clusters = _cluster_points_by_buffer(pts, cluster_radius_m=1500.0, verbose=False)
    elapsed = time.perf_counter() - t0
    assert clusters
    assert elapsed < 3.0


def test_run_candidate_batch_viewshed_mock(tmp_path: Path) -> None:
    from peaky_finders.site_suggestions.batch_viewshed import run_candidate_batch_viewsheds
    from peaky_finders.site_suggestions.candidates import SiteCandidate
    from peaky_finders.sites_job import load_preset, write_preset_document

    preset_path = tmp_path / "job.yaml"
    write_preset_document(
        preset_path,
        {
            "simulation": dict(_SUGGEST_SIMULATION),
            "display": {"colormap": "rainbow", "min_dbm": -130.0, "max_dbm": -80.0},
            "sites": {"seed": {"name": "Seed", "loc": [39.0, -119.0]}},
        },
    )
    preset = load_preset(preset_path)
    candidates = [
        SiteCandidate(lat=39.02, lon=-115.03, elev_m=2500.0, strategy="cluster"),
        SiteCandidate(lat=39.04, lon=-115.01, elev_m=2600.0, strategy="cluster"),
    ]

    def _fake(**kw):
        lat = float(kw["lat"])
        lon = float(kw["lon"])
        return box(lon - 0.01, lat - 0.01, lon + 0.01, lat + 0.01)

    results = run_candidate_batch_viewsheds(
        preset=preset,
        preset_path=preset_path,
        viewshed_root=tmp_path / "viewsheds",
        candidates=candidates,
        footprint_runner=_fake,
    )
    assert len(results) == 2
    assert all(r.footprint is not None for r in results.values())


def test_append_and_remove_suggested_sites(tmp_path: Path) -> None:
    p = tmp_path / "job.yaml"
    write_preset_document(
        p,
        {
            "simulation": dict(_SUGGEST_SIMULATION),
            "display": {"colormap": "rainbow", "min_dbm": -130.0, "max_dbm": -80.0},
            "sites": {"placed-a": {"name": "A", "loc": [39.0, -119.0]}},
        },
    )
    slugs = append_suggested_sites_to_preset(
        p,
        [{"_suggest_iteration": 1, "name": "Suggested 1", "loc": [39.1, -119.1], "rationale": "test"}],
    )
    assert len(slugs) == 1
    job = load_preset(p)
    assert job.sites[slugs[0]].type == SiteType.SUGGESTED
    assert remove_suggested_sites_from_preset(p) == 1
    job2 = load_preset(p)
    assert slugs[0] not in job2.sites


def test_greedy_planner_picks_best_mock_footprint(tmp_path: Path) -> None:
    from peaky_finders.build_configure import PlannedComposite, PlannedViewshedWorkspace

    aoi = box(-115.05, 39.00, -114.98, 39.05)
    eligible = box(-115.04, 39.01, -114.99, 39.04)
    aoi_gpkg = tmp_path / "aoi.gpkg"
    elig_gpkg = tmp_path / "eligible.gpkg"
    _write_gpkg(aoi_gpkg, aoi, layer="aoi")
    _write_gpkg(elig_gpkg, eligible, layer="eligible_land_use")

    seed_gpkg = tmp_path / "seed.gpkg"
    _write_gpkg(seed_gpkg, box(-115.04, 39.01, -115.02, 39.03), layer="coverage")

    preset_path = tmp_path / "job.yaml"
    write_preset_document(
        preset_path,
        {
            "simulation": dict(_SUGGEST_SIMULATION),
            "display": {"colormap": "rainbow", "min_dbm": -130.0, "max_dbm": -80.0},
            "land": _MINIMAL_LAND,
            "suggest": {
                    "strategy": "land-grab",
                    "planner_raster_dimension": 256,
                    "land_grab": {
                        "coverage_goal_depth": 1,
                        "max_candidates_per_round": 4,
                        "max_clusters_per_round": 2,
                        "peak_cluster_radius_m": 500.0,
                        "cluster_sample_spacing_m": 200.0,
                        "cluster_sample_radius_m": 300.0,
                        "refine_enabled": False,
                    },
            },
            "sites": {"seed": {"name": "Seed", "loc": [39.02, -115.03]}},
        },
    )
    preset = load_preset(preset_path)

    plan = type(
        "Plan",
        (),
        {
            "composites": (
                PlannedComposite(role="aoi", sha="a", union_gpkg=aoi_gpkg, manifest=tmp_path / "aoi.json"),
                PlannedComposite(role="eligible", sha="e", union_gpkg=elig_gpkg, manifest=tmp_path / "e.json"),
            ),
            "viewshed_workspaces": (
                PlannedViewshedWorkspace(
                    digest="d",
                    workdir=tmp_path / "ws",
                    output_ppm=tmp_path / "ws/out.ppm",
                    splat_png=tmp_path / "ws/splat.png",
                    splat_gpkg=seed_gpkg,
                    request_json=tmp_path / "ws/request.json",
                    site_slugs=("seed",),
                ),
            ),
            "splat_tiles_root": tmp_path / "dem",
            "viewsheds_root": tmp_path / "viewsheds",
        },
    )()

    def _fake_footprint(**kw) -> box:
        lat = float(kw["lat"])
        lon = float(kw["lon"])
        return box(lon - 0.02, lat - 0.01, lon + 0.02, lat + 0.01)

    from peaky_finders.site_suggestions.solver import SOLVE_UNTIL_COMPLETE

    winners = plan_greedy_site_suggestions(
        preset=preset,
        preset_path=preset_path,
        plan=plan,
        suggest_cli_n=SOLVE_UNTIL_COMPLETE,
        suggest_root=tmp_path / "suggest",
        footprint_runner=_fake_footprint,
    )
    assert len(winners) >= 1
    entries = planned_to_preset_entries(winners)
    assert "Greedy suggest" in entries[0]["rationale"]
    assert winners[0].gain_cells == max(w.gain_cells for w in winners)


def test_greedy_planner_verbose_logs_trials(capsys, tmp_path: Path) -> None:
    from peaky_finders.build_configure import PlannedComposite, PlannedViewshedWorkspace

    aoi = box(-115.05, 39.00, -114.98, 39.05)
    eligible = box(-115.04, 39.01, -114.99, 39.04)
    aoi_gpkg = tmp_path / "aoi.gpkg"
    elig_gpkg = tmp_path / "eligible.gpkg"
    _write_gpkg(aoi_gpkg, aoi, layer="aoi")
    _write_gpkg(elig_gpkg, eligible, layer="eligible_land_use")

    seed_gpkg = tmp_path / "seed.gpkg"
    _write_gpkg(seed_gpkg, box(-115.04, 39.01, -115.02, 39.03), layer="coverage")

    preset_path = tmp_path / "job.yaml"
    write_preset_document(
        preset_path,
        {
            "simulation": dict(_SUGGEST_SIMULATION),
            "display": {"colormap": "rainbow", "min_dbm": -130.0, "max_dbm": -80.0},
            "land": _MINIMAL_LAND,
            "suggest": {
                    "strategy": "land-grab",
                    "planner_raster_dimension": 256,
                    "land_grab": {
                        "max_candidates_per_round": 2,
                        "refine_enabled": False,
                    },
            },
            "sites": {"seed": {"name": "Seed", "loc": [39.02, -115.03]}},
        },
    )
    preset = load_preset(preset_path)
    plan = type(
        "Plan",
        (),
        {
            "composites": (
                PlannedComposite(role="aoi", sha="a", union_gpkg=aoi_gpkg, manifest=tmp_path / "aoi.json"),
                PlannedComposite(role="eligible", sha="e", union_gpkg=elig_gpkg, manifest=tmp_path / "e.json"),
            ),
            "viewshed_workspaces": (
                PlannedViewshedWorkspace(
                    digest="d",
                    workdir=tmp_path / "ws",
                    output_ppm=tmp_path / "ws/out.ppm",
                    splat_png=tmp_path / "ws/splat.png",
                    splat_gpkg=seed_gpkg,
                    request_json=tmp_path / "ws/request.json",
                    site_slugs=("seed",),
                ),
            ),
            "splat_tiles_root": tmp_path / "dem",
            "viewsheds_root": tmp_path / "viewsheds",
        },
    )()

    def _fake_footprint(**kw) -> box:
        lat = float(kw["lat"])
        lon = float(kw["lon"])
        return box(lon - 0.02, lat - 0.01, lon + 0.02, lat + 0.01)

    plan_greedy_site_suggestions(
        preset=preset,
        preset_path=preset_path,
        plan=plan,
        suggest_cli_n=1,
        suggest_root=tmp_path / "suggest",
        footprint_runner=_fake_footprint,
        verbose=True,
    )
    out = capsys.readouterr().out
    assert "planner configuration" in out
    assert "strategy: land-grab" in out
    assert "coverage_target: eligible" in out
    assert "candidate shortlist" in out
    assert "viewshed trials" in out
    assert "selection" in out


def test_site_suggestions_default_strategy() -> None:
    from peaky_finders.sites_job import SuggestConfig, SiteSuggestionStrategy

    cfg = SuggestConfig()
    assert cfg.strategy == SiteSuggestionStrategy.LAND_GRAB
    assert cfg.land_grab.coverage_goal_depth == 1


def test_resolve_land_grab_strategy() -> None:
    from peaky_finders.site_suggestions.solver import SOLVE_UNTIL_COMPLETE
    from peaky_finders.site_suggestions.providers.land_grab import LandGrabStrategy
    from peaky_finders.site_suggestions.providers.registry import resolve_site_suggestion_strategy
    from peaky_finders.sites_job import SuggestConfig

    provider = resolve_site_suggestion_strategy(SuggestConfig())
    assert isinstance(provider, LandGrabStrategy)
    assert provider.name == "land-grab"
    assert provider.goal_depth(SuggestConfig()) == 1
    assert provider.resolve_step_budget(SuggestConfig(), 3) == 3  # goal budget
    assert provider.resolve_step_budget(SuggestConfig(), SOLVE_UNTIL_COMPLETE) is None


def test_mesh_backbone_planner_picks_along_incomplete_link(tmp_path: Path) -> None:
    from peaky_finders.build_configure import PlannedComposite, PlannedViewshedWorkspace

    aoi = box(-115.05, 39.00, -114.98, 39.05)
    eligible = box(-115.04, 39.01, -114.99, 39.04)
    aoi_gpkg = tmp_path / "aoi.gpkg"
    elig_gpkg = tmp_path / "eligible.gpkg"
    _write_gpkg(aoi_gpkg, aoi, layer="aoi")
    _write_gpkg(elig_gpkg, eligible, layer="eligible_land_use")

    seed_gpkg = tmp_path / "seed.gpkg"
    _write_gpkg(seed_gpkg, box(-115.04, 39.01, -115.02, 39.03), layer="coverage")

    preset_path = tmp_path / "job.yaml"
    write_preset_document(
        preset_path,
        {
            "simulation": dict(_SUGGEST_SIMULATION),
            "display": {"colormap": "rainbow", "min_dbm": -130.0, "max_dbm": -80.0},
            "land": _MINIMAL_LAND,
            "goals": {
                "goal-b": {"name": "Goal B", "loc": [39.02, -114.99]},
            },
            "suggest": {
                    "strategy": "mesh-backbone",
                    "planner_raster_dimension": 256,
                    "mesh_backbone": {
                        "max_candidates_per_round": 8,
                        "frontier_sample_spacing_m": 1500.0,
                        "refine_enabled": False,
                        "goal_order": ["goal-b"],
                    },
            },
            "sites": {
                "seed": {"name": "Seed", "loc": [39.02, -115.04]},
                "site-b": {"name": "B", "loc": [39.02, -114.99]},
            },
        },
    )
    preset = load_preset(preset_path)
    plan = type(
        "Plan",
        (),
        {
            "composites": (
                PlannedComposite(role="aoi", sha="a", union_gpkg=aoi_gpkg, manifest=tmp_path / "aoi.json"),
                PlannedComposite(role="eligible", sha="e", union_gpkg=elig_gpkg, manifest=tmp_path / "e.json"),
            ),
            "viewshed_workspaces": (
                PlannedViewshedWorkspace(
                    digest="d",
                    workdir=tmp_path / "ws",
                    output_ppm=tmp_path / "ws/out.ppm",
                    splat_png=tmp_path / "ws/splat.png",
                    splat_gpkg=seed_gpkg,
                    request_json=tmp_path / "ws/request.json",
                    site_slugs=("seed",),
                ),
            ),
            "splat_tiles_root": tmp_path / "dem",
            "viewsheds_root": tmp_path / "viewsheds",
        },
    )()

    def _fake_footprint(**kw) -> box:
        lat = float(kw["lat"])
        lon = float(kw["lon"])
        return box(lon - 0.02, lat - 0.01, lon + 0.02, lat + 0.01)

    winners = plan_greedy_site_suggestions(
        preset=preset,
        preset_path=preset_path,
        plan=plan,
        suggest_cli_n=1,
        suggest_root=tmp_path / "suggest",
        footprint_runner=_fake_footprint,
    )
    assert len(winners) >= 1
    assert winners[0].lon > -115.04
    assert winners[0].strategy.startswith("goal:")
    assert winners[-1].strategy.startswith("goal:")


def test_suggest_cli_n_stops_after_goal_budget(monkeypatch, tmp_path: Path) -> None:
    from peaky_finders.build_configure import PlannedComposite, PlannedViewshedWorkspace
    from peaky_finders.site_suggestions import planner as planner_mod
    from peaky_finders.site_suggestions.candidates import SiteCandidate
    from peaky_finders.site_suggestions.planner import PlannedSuggestion
    from peaky_finders.site_suggestions.providers.mesh_backbone import MeshBackboneStrategy

    aoi = box(-115.05, 39.00, -114.98, 39.05)
    eligible = box(-115.04, 39.01, -114.99, 39.04)
    aoi_gpkg = tmp_path / "aoi.gpkg"
    elig_gpkg = tmp_path / "eligible.gpkg"
    _write_gpkg(aoi_gpkg, aoi, layer="aoi")
    _write_gpkg(elig_gpkg, eligible, layer="eligible_land_use")

    seed_gpkg = tmp_path / "seed.gpkg"
    _write_gpkg(seed_gpkg, box(-115.04, 39.01, -115.02, 39.03), layer="coverage")

    preset_path = tmp_path / "job.yaml"
    write_preset_document(
        preset_path,
        {
            "simulation": dict(_SUGGEST_SIMULATION),
            "display": {"colormap": "rainbow", "min_dbm": -130.0, "max_dbm": -80.0},
            "land": _MINIMAL_LAND,
            "goals": {
                "goal-b": {"name": "Goal B", "loc": [39.02, -114.99]},
            },
            "suggest": {
                    "strategy": "mesh-backbone",
                    "planner_raster_dimension": 256,
                    "mesh_backbone": {
                        "max_candidates_per_round": 4,
                        "refine_enabled": False,
                        "goal_order": ["goal-b"],
                    },
            },
            "sites": {"seed": {"name": "Seed", "loc": [39.02, -115.04]}},
        },
    )
    preset = load_preset(preset_path)
    plan = type(
        "Plan",
        (),
        {
            "composites": (
                PlannedComposite(role="aoi", sha="a", union_gpkg=aoi_gpkg, manifest=tmp_path / "aoi.json"),
                PlannedComposite(role="eligible", sha="e", union_gpkg=elig_gpkg, manifest=tmp_path / "e.json"),
            ),
            "viewshed_workspaces": (
                PlannedViewshedWorkspace(
                    digest="d",
                    workdir=tmp_path / "ws",
                    output_ppm=tmp_path / "ws/out.ppm",
                    splat_png=tmp_path / "ws/splat.png",
                    splat_gpkg=seed_gpkg,
                    request_json=tmp_path / "ws/request.json",
                    site_slugs=("seed",),
                ),
            ),
            "splat_tiles_root": tmp_path / "dem",
            "viewsheds_root": tmp_path / "viewsheds",
        },
    )()

    def _always_candidates(self, ctx, *, iteration: int) -> list[SiteCandidate]:
        del self, ctx, iteration
        return [SiteCandidate(lat=39.02, lon=-115.02, elev_m=None, strategy="goal:goal-b")]

    def _fake_trials(*, iteration: int, **kwargs) -> tuple[PlannedSuggestion | None, box | None, list]:
        del kwargs
        lat = 39.02 + 0.001 * iteration
        lon = -115.02 + 0.001 * iteration
        fp = box(lon - 0.02, lat - 0.01, lon + 0.02, lat + 0.01)
        pick = PlannedSuggestion(
            lat=lat,
            lon=lon,
            elev_m=None,
            gain_cells=100,
            strategy="goal:goal-b",
            iteration=iteration,
            rationale=f"Mesh-grow #{iteration}: fake",
        )
        return pick, fp, []

    monkeypatch.setattr(MeshBackboneStrategy, "generate_candidates", _always_candidates)
    monkeypatch.setattr(
        MeshBackboneStrategy,
        "planning_complete",
        lambda self, ctx: False,
    )
    monkeypatch.setattr(planner_mod, "_run_candidate_trials", _fake_trials)

    def _mock_goals_satisfied(ctx, *, planning_complete, baseline, tracked_keys=None):
        del planning_complete, tracked_keys
        done = {f"goal-{n}" for n in range(1, len(ctx.session_sites) + 1)}
        return done - baseline

    monkeypatch.setattr(planner_mod, "goals_satisfied_since", _mock_goals_satisfied)

    winners = plan_greedy_site_suggestions(
        preset=preset,
        preset_path=preset_path,
        plan=plan,
        suggest_cli_n=3,
        suggest_root=tmp_path / "suggest",
        footprint_runner=lambda **kw: box(-115.04, 39.01, -115.02, 39.03),
    )
    assert len(winners) == 3
    assert [w.iteration for w in winners] == [1, 2, 3]


def test_suggest_iteration_helpers(tmp_path: Path) -> None:
    from peaky_finders.site_suggestions.preset_io import count_suggested_sites, next_suggest_iteration

    p = tmp_path / "job.yaml"
    write_preset_document(
        p,
        {
            "simulation": dict(_SUGGEST_SIMULATION),
            "display": {"colormap": "rainbow", "min_dbm": -130.0, "max_dbm": -80.0},
            "sites": {
                "seed": {"name": "Seed", "loc": [39.0, -119.0]},
                "suggest-01-alpha": {
                    "type": "suggested",
                    "name": "Suggested 1",
                    "loc": [39.1, -119.1],
                },
                "suggest-03-beta": {
                    "type": "suggested",
                    "name": "Suggested 3",
                    "loc": [39.2, -119.2],
                },
            },
        },
    )
    preset = load_preset(p)
    assert count_suggested_sites(preset) == 2
    assert next_suggest_iteration(preset) == 4


def test_suggest_cli_n_adds_new_sites_when_prior_suggested_exist(monkeypatch, tmp_path: Path) -> None:
    from peaky_finders.build_configure import PlannedComposite, PlannedViewshedWorkspace
    from peaky_finders.site_suggestions import planner as planner_mod
    from peaky_finders.site_suggestions.candidates import SiteCandidate
    from peaky_finders.site_suggestions.planner import PlannedSuggestion
    from peaky_finders.site_suggestions.providers.mesh_backbone import MeshBackboneStrategy

    aoi = box(-115.05, 39.00, -114.98, 39.05)
    eligible = box(-115.04, 39.01, -114.99, 39.04)
    aoi_gpkg = tmp_path / "aoi.gpkg"
    elig_gpkg = tmp_path / "eligible.gpkg"
    _write_gpkg(aoi_gpkg, aoi, layer="aoi")
    _write_gpkg(elig_gpkg, eligible, layer="eligible_land_use")

    seed_gpkg = tmp_path / "seed.gpkg"
    _write_gpkg(seed_gpkg, box(-115.04, 39.01, -115.02, 39.03), layer="coverage")

    preset_path = tmp_path / "job.yaml"
    write_preset_document(
        preset_path,
        {
            "simulation": dict(_SUGGEST_SIMULATION),
            "display": {"colormap": "rainbow", "min_dbm": -130.0, "max_dbm": -80.0},
            "land": _MINIMAL_LAND,
            "goals": {
                "goal-b": {"name": "Goal B", "loc": [39.02, -114.99]},
            },
            "suggest": {
                    "strategy": "mesh-backbone",
                    "planner_raster_dimension": 256,
                    "mesh_backbone": {
                        "max_candidates_per_round": 4,
                        "refine_enabled": False,
                        "goal_order": ["goal-b"],
                    },
            },
            "sites": {
                "seed": {"name": "Seed", "loc": [39.02, -115.04]},
                "suggest-01-prior": {
                    "type": "suggested",
                    "name": "Suggested 1",
                    "loc": [39.02, -115.03],
                },
                "suggest-02-prior": {
                    "type": "suggested",
                    "name": "Suggested 2",
                    "loc": [39.02, -115.02],
                },
            },
        },
    )
    preset = load_preset(preset_path)
    plan = type(
        "Plan",
        (),
        {
            "composites": (
                PlannedComposite(role="aoi", sha="a", union_gpkg=aoi_gpkg, manifest=tmp_path / "aoi.json"),
                PlannedComposite(role="eligible", sha="e", union_gpkg=elig_gpkg, manifest=tmp_path / "e.json"),
            ),
            "viewshed_workspaces": (
                PlannedViewshedWorkspace(
                    digest="d",
                    workdir=tmp_path / "ws",
                    output_ppm=tmp_path / "ws/out.ppm",
                    splat_png=tmp_path / "ws/splat.png",
                    splat_gpkg=seed_gpkg,
                    request_json=tmp_path / "ws/request.json",
                    site_slugs=("seed",),
                ),
            ),
            "splat_tiles_root": tmp_path / "dem",
            "viewsheds_root": tmp_path / "viewsheds",
        },
    )()

    def _always_candidates(self, ctx, *, iteration: int) -> list[SiteCandidate]:
        del self, ctx, iteration
        return [SiteCandidate(lat=39.02, lon=-115.02, elev_m=None, strategy="goal:goal-b")]

    def _fake_trials(*, iteration: int, **kwargs) -> tuple[PlannedSuggestion | None, box | None, list]:
        del kwargs
        lat = 39.02 + 0.001 * iteration
        lon = -115.02 + 0.001 * iteration
        fp = box(lon - 0.02, lat - 0.01, lon + 0.02, lat + 0.01)
        pick = PlannedSuggestion(
            lat=lat,
            lon=lon,
            elev_m=None,
            gain_cells=100,
            strategy="goal:goal-b",
            iteration=iteration,
            rationale=f"Mesh-grow #{iteration}: fake",
        )
        return pick, fp, []

    monkeypatch.setattr(MeshBackboneStrategy, "generate_candidates", _always_candidates)
    monkeypatch.setattr(
        MeshBackboneStrategy,
        "planning_complete",
        lambda self, ctx: False,
    )
    monkeypatch.setattr(planner_mod, "_run_candidate_trials", _fake_trials)

    def _mock_goals_satisfied(ctx, *, planning_complete, baseline, tracked_keys=None):
        del planning_complete, tracked_keys
        done = {f"goal-{n}" for n in range(1, len(ctx.session_sites) + 1)}
        return done - baseline

    monkeypatch.setattr(planner_mod, "goals_satisfied_since", _mock_goals_satisfied)

    winners = plan_greedy_site_suggestions(
        preset=preset,
        preset_path=preset_path,
        plan=plan,
        suggest_cli_n=4,
        suggest_root=tmp_path / "suggest",
        footprint_runner=lambda **kw: box(-115.04, 39.01, -115.02, 39.03),
    )
    assert len(winners) == 4
    assert [w.iteration for w in winners] == [3, 4, 5, 6]


def test_on_pick_writes_each_site_to_preset(monkeypatch, tmp_path: Path) -> None:
    from peaky_finders.build_configure import PlannedComposite, PlannedViewshedWorkspace
    from peaky_finders.site_suggestions import planner as planner_mod
    from peaky_finders.site_suggestions.candidates import SiteCandidate
    from peaky_finders.site_suggestions.planner import PlannedSuggestion
    from peaky_finders.site_suggestions.preset_io import count_suggested_sites
    from peaky_finders.site_suggestions.providers.mesh_backbone import MeshBackboneStrategy

    aoi = box(-115.05, 39.00, -114.98, 39.05)
    eligible = box(-115.04, 39.01, -114.99, 39.04)
    aoi_gpkg = tmp_path / "aoi.gpkg"
    elig_gpkg = tmp_path / "eligible.gpkg"
    _write_gpkg(aoi_gpkg, aoi, layer="aoi")
    _write_gpkg(elig_gpkg, eligible, layer="eligible_land_use")

    seed_gpkg = tmp_path / "seed.gpkg"
    _write_gpkg(seed_gpkg, box(-115.04, 39.01, -115.02, 39.03), layer="coverage")

    preset_path = tmp_path / "job.yaml"
    write_preset_document(
        preset_path,
        {
            "simulation": dict(_SUGGEST_SIMULATION),
            "display": {"colormap": "rainbow", "min_dbm": -130.0, "max_dbm": -80.0},
            "land": _MINIMAL_LAND,
            "goals": {
                "goal-b": {"name": "Goal B", "loc": [39.02, -114.99]},
            },
            "suggest": {
                    "strategy": "mesh-backbone",
                    "planner_raster_dimension": 256,
                    "mesh_backbone": {
                        "max_candidates_per_round": 4,
                        "refine_enabled": False,
                        "goal_order": ["goal-b"],
                    },
            },
            "sites": {"seed": {"name": "Seed", "loc": [39.02, -115.04]}},
        },
    )
    preset = load_preset(preset_path)
    plan = type(
        "Plan",
        (),
        {
            "composites": (
                PlannedComposite(role="aoi", sha="a", union_gpkg=aoi_gpkg, manifest=tmp_path / "aoi.json"),
                PlannedComposite(role="eligible", sha="e", union_gpkg=elig_gpkg, manifest=tmp_path / "e.json"),
            ),
            "viewshed_workspaces": (
                PlannedViewshedWorkspace(
                    digest="d",
                    workdir=tmp_path / "ws",
                    output_ppm=tmp_path / "ws/out.ppm",
                    splat_png=tmp_path / "ws/splat.png",
                    splat_gpkg=seed_gpkg,
                    request_json=tmp_path / "ws/request.json",
                    site_slugs=("seed",),
                ),
            ),
            "splat_tiles_root": tmp_path / "dem",
            "viewsheds_root": tmp_path / "viewsheds",
        },
    )()

    def _always_candidates(self, ctx, *, iteration: int) -> list[SiteCandidate]:
        del self, ctx, iteration
        return [SiteCandidate(lat=39.02, lon=-115.02, elev_m=None, strategy="goal:goal-b")]

    def _fake_trials(*, iteration: int, **kwargs) -> tuple[PlannedSuggestion | None, box | None, list]:
        del kwargs
        lat = 39.02 + 0.001 * iteration
        lon = -115.02 + 0.001 * iteration
        fp = box(lon - 0.02, lat - 0.01, lon + 0.02, lat + 0.01)
        pick = PlannedSuggestion(
            lat=lat,
            lon=lon,
            elev_m=None,
            gain_cells=100,
            strategy="goal:goal-b",
            iteration=iteration,
            rationale=f"Mesh-grow #{iteration}: fake",
        )
        return pick, fp, []

    written: list[int] = []

    def _on_pick(pick: PlannedSuggestion) -> str:
        slugs = append_suggested_sites_to_preset(
            preset_path,
            planned_to_preset_entries([pick]),
        )
        written.append(count_suggested_sites(load_preset(preset_path)))
        return slugs[0]

    monkeypatch.setattr(MeshBackboneStrategy, "generate_candidates", _always_candidates)
    monkeypatch.setattr(
        MeshBackboneStrategy,
        "planning_complete",
        lambda self, ctx: len(ctx.session_footprints) >= 2,
    )
    monkeypatch.setattr(planner_mod, "_run_candidate_trials", _fake_trials)

    winners = plan_greedy_site_suggestions(
        preset=preset,
        preset_path=preset_path,
        plan=plan,
        suggest_cli_n=5,
        suggest_root=tmp_path / "suggest",
        footprint_runner=lambda **kw: box(-115.04, 39.01, -115.02, 39.03),
        on_pick=_on_pick,
    )
    assert len(winners) == 2
    assert written == [1, 2]
    assert count_suggested_sites(load_preset(preset_path)) == 2
