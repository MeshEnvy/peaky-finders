"""Site suggestion planner and preset IO."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
from shapely.geometry import box

from peaky_finders.site_suggestions.depth_grid import build_coverage_depth_grid
from peaky_finders.site_suggestions.planner import plan_greedy_site_suggestions, planned_to_preset_entries
from peaky_finders.site_suggestions.preset_io import append_suggested_sites_to_preset, remove_suggested_sites_from_preset
from peaky_finders.sites_job import SiteType, load_preset, write_preset_document


def _write_gpkg(path: Path, geom, *, layer: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    gpd.GeoDataFrame(geometry=[geom], crs="EPSG:4326").to_file(path, driver="GPKG", layer=layer)


def test_site_type_defaults_to_installed(tmp_path: Path) -> None:
    p = tmp_path / "job.yaml"
    write_preset_document(
        p,
        {
            "simulation": {
                "transmitter": {"height_m": 2.0, "gain_dbi": 2.0, "loss_db": 0.0},
                "receiver": {"height_m": 2.0, "gain_dbi": 2.0, "loss_db": 0.0},
            },
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
        footprint_gpkg_paths=[a],
        max_raster_dimension=256,
    )
    new_fp = box(-115.04, 39.02, -114.99, 39.04)
    gain = grid.marginal_gain_cells(new_fp, goal_depth=1)
    assert gain > 0
    grid.add_footprint(new_fp)
    assert grid.marginal_gain_cells(new_fp, goal_depth=1) == 0


def test_append_and_remove_suggested_sites(tmp_path: Path) -> None:
    p = tmp_path / "job.yaml"
    write_preset_document(
        p,
        {
            "simulation": {
                "transmitter": {"height_m": 2.0, "gain_dbi": 2.0, "loss_db": 0.0},
                "receiver": {"height_m": 2.0, "gain_dbi": 2.0, "loss_db": 0.0},
            },
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
            "simulation": {
                "transmitter": {"height_m": 2.0, "gain_dbi": 2.0, "loss_db": 0.0},
                "receiver": {"height_m": 2.0, "gain_dbi": 2.0, "loss_db": 0.0},
            },
            "display": {"colormap": "rainbow", "min_dbm": -130.0, "max_dbm": -80.0},
            "bundle": {
                "site_suggestions": {
                    "coverage_goal_depth": 1,
                    "planner_raster_dimension": 256,
                    "max_candidates_per_round": 4,
                    "peak_cluster_radius_m": 500.0,
                }
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
                    coverage_gpkg=seed_gpkg,
                    request_json=tmp_path / "ws/request.json",
                    site_slugs=("seed",),
                    rep_slug="seed",
                ),
            ),
            "splat_tiles_root": tmp_path / "dem",
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
        n_suggestions=1,
        suggest_root=tmp_path / "suggest",
        footprint_runner=_fake_footprint,
    )
    assert len(winners) == 1
    entries = planned_to_preset_entries(winners)
    assert "Greedy suggest" in entries[0]["rationale"]


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
            "simulation": {
                "transmitter": {"height_m": 2.0, "gain_dbi": 2.0, "loss_db": 0.0},
                "receiver": {"height_m": 2.0, "gain_dbi": 2.0, "loss_db": 0.0},
            },
            "display": {"colormap": "rainbow", "min_dbm": -130.0, "max_dbm": -80.0},
            "bundle": {"site_suggestions": {"planner_raster_dimension": 256, "max_candidates_per_round": 2}},
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
                    coverage_gpkg=seed_gpkg,
                    request_json=tmp_path / "ws/request.json",
                    site_slugs=("seed",),
                    rep_slug="seed",
                ),
            ),
            "splat_tiles_root": tmp_path / "dem",
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
        n_suggestions=1,
        suggest_root=tmp_path / "suggest",
        footprint_runner=_fake_footprint,
        verbose=True,
    )
    out = capsys.readouterr().out
    assert "planner configuration" in out
    assert "candidate shortlist" in out
    assert "viewshed trials" in out
    assert "selection" in out
