"""Tests for RF min-hop corridor planning and scoring."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import geopandas as gpd
from shapely.geometry import LineString, box

from peaky_finders.site_suggestions.context import SiteSuggestionContext
from peaky_finders.site_suggestions.corridor import (
    CorridorGrowState,
    _geodesic_relay_nodes,
    _node_key,
    _scan_attachment_boundary_coords,
    plan_corridors_for_goal,
)
from peaky_finders.site_suggestions.corridor_scoring import (
    build_corridor_score_context,
    corridor_sort_key,
    corridor_trial_has_progress,
    max_covered_arc_m,
    score_corridor_trial,
    score_corridor_trials,
)
from peaky_finders.site_suggestions.depth_grid import build_coverage_depth_grid
from peaky_finders.site_suggestions.mesh_backbone_candidates import generate_corridor_grow_candidates
from peaky_finders.sites_job import (
    BundleSiteSuggestionsConfig,
    MeshBackboneGoalEntry,
    MeshBackboneRouting,
    MeshBackboneStrategyConfig,
    SiteSuggestionStrategy,
)

_RF_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "splatter"
    / "tests"
    / "fixtures"
    / "splat_request_hash_fixture.json"
).read_text(encoding="utf-8")


def _mock_mutual_hop_batches(pairs, *, rf_json: str, **kwargs) -> list[bool]:
    del rf_json, kwargs
    return [True] * len(pairs)


def _mock_ensure_dem(*args, **kwargs) -> None:
    del args, kwargs


def _ctx(*, eligible, goals: dict[str, tuple[float, float]], footprint=None) -> SiteSuggestionContext:
    grid = build_coverage_depth_grid(
        aoi_ll=eligible,
        target_ll=eligible,
        footprint_gpkg_paths=[],
        max_raster_dimension=128,
    )
    if footprint is not None:
        grid.add_footprint(footprint)
    mb = MeshBackboneStrategyConfig(
        routing=MeshBackboneRouting.CORRIDOR,
        goals={k: MeshBackboneGoalEntry(loc=v) for k, v in goals.items()},
        corridor_grid_cell_m=200.0,
        corridor_k=2,
        max_candidates_per_round=16,
        coarse_peaks_enabled=False,
    )
    ctx = SiteSuggestionContext(
        preset=type("P", (), {"sites": {"seed": type("E", (), {"lat": 39.0, "lon": -116.0})()}})(),
        plan=type("Plan", (), {"viewshed_workspaces": ()})(),
        grid=grid,
        eligible_ll=eligible,
        aoi_ll=eligible,
        target_ll=eligible,
        suggest_root=Path("/tmp/suggest"),
        cfg=BundleSiteSuggestionsConfig(strategy=SiteSuggestionStrategy.MESH_BACKBONE, mesh_backbone=mb),
        dem_mirror_root=Path("/tmp/dem"),
        eligible_sha="x",
        jobs=1,
        verbose=False,
        corridor_state=CorridorGrowState(),
    )
    if footprint is not None:
        ctx.session_footprints["seed"] = footprint
    return ctx


def test_geodesic_relay_nodes_parallel_matches_serial() -> None:
    eligible = box(-116.5, 38.5, -114.5, 39.5)
    kwargs = dict(
        attach_lon=-116.2,
        attach_lat=39.0,
        goal_lon=-114.8,
        goal_lat=39.0,
        eligible=eligible,
        spacing_m=200.0,
        max_snap_m=750.0,
        max_hop_m=120_000.0,
        extra_nodes=[(39.0, -115.5)],
    )
    serial = _geodesic_relay_nodes(**kwargs, jobs=1)
    parallel = _geodesic_relay_nodes(**kwargs, jobs=4)
    assert serial
    assert {tuple(n) for n in serial} == {tuple(n) for n in parallel}


def test_geodesic_relay_nodes_appends_backbone_after_chain() -> None:
    eligible = box(-116.5, 38.5, -114.5, 39.5)
    nodes = _geodesic_relay_nodes(
        attach_lon=-116.2,
        attach_lat=39.0,
        goal_lon=-114.8,
        goal_lat=39.0,
        eligible=eligible,
        spacing_m=200.0,
        max_snap_m=750.0,
        max_hop_m=120_000.0,
        extra_nodes=[(38.75, -115.25)],
        jobs=1,
    )
    assert len(nodes) >= 2
    assert abs(nodes[0][0] - 39.0) < 0.02 and abs(nodes[0][1] - (-116.2)) < 0.02
    off_line = (38.75, -115.25)
    off_idx = next(i for i, n in enumerate(nodes) if _node_key(n[0], n[1]) == _node_key(off_line[0], off_line[1]))
    assert off_idx > len(nodes) // 2


def test_attachment_boundary_scan_parallel_matches_serial() -> None:
    eligible = box(-117.0, 38.5, -114.5, 39.5)
    boundary_m = gpd.GeoDataFrame(geometry=[eligible.boundary], crs="EPSG:4326").to_crs("EPSG:3857").geometry.iloc[0]
    length = float(boundary_m.length)
    base = [tuple(boundary_m.interpolate(d).coords[0]) for d in range(0, int(length), 100)]
    coords_m = base * 400
    goal_m = gpd.GeoDataFrame(geometry=[eligible.representative_point()], crs="EPSG:4326").to_crs(
        "EPSG:3857"
    ).geometry.iloc[0]
    gx, gy = float(goal_m.x), float(goal_m.y)
    serial, serial_d = _scan_attachment_boundary_coords(
        coords_m,
        gx=gx,
        gy=gy,
        eligible=eligible,
        jobs=1,
        verbose=False,
    )
    parallel, parallel_d = _scan_attachment_boundary_coords(
        coords_m,
        gx=gx,
        gy=gy,
        eligible=eligible,
        jobs=4,
        verbose=False,
    )
    assert serial is not None
    assert parallel == serial
    assert parallel_d == serial_d


@patch("peaky_finders.site_suggestions.corridor.max_hop_range_m", return_value=120_000.0)
@patch("peaky_finders.site_suggestions.corridor.rf_json_for_preset", return_value=_RF_FIXTURE)
@patch("peaky_finders.site_suggestions.corridor.ensure_dem_for_points", side_effect=_mock_ensure_dem)
@patch("peaky_finders.site_suggestions.corridor.mutual_hop_batches", side_effect=_mock_mutual_hop_batches)
def test_plan_corridor_through_eligible_strip(_mock_batch, _mock_dem, _mock_rf, _mock_range) -> None:
    eligible = box(-116.5, 38.5, -114.5, 39.5)
    seed_fp = box(-116.4, 38.9, -116.0, 39.1)
    ctx = _ctx(
        eligible=eligible,
        goals={"east": (39.0, -114.8)},
        footprint=seed_fp,
    )
    routes = plan_corridors_for_goal(ctx, goal_key="east", k=1, cell_m=200.0)
    assert len(routes) == 1
    assert routes[0].length_m > 0
    assert routes[0].hop_count >= 1
    start = routes[0].line.coords[0]
    end = routes[0].line.coords[-1]
    assert end[0] > start[0]


@patch("peaky_finders.site_suggestions.corridor.max_hop_range_m", return_value=120_000.0)
@patch("peaky_finders.site_suggestions.corridor.rf_json_for_preset", return_value=_RF_FIXTURE)
@patch("peaky_finders.site_suggestions.corridor.ensure_dem_for_points", side_effect=_mock_ensure_dem)
@patch("peaky_finders.site_suggestions.corridor.mutual_hop_batches", side_effect=_mock_mutual_hop_batches)
def test_plan_corridor_rejects_trivial_start_at_goal(_mock_batch, _mock_dem, _mock_rf, _mock_range) -> None:
    eligible = box(-116.5, 38.5, -114.5, 39.5)
    seed_fp = box(-116.4, 38.9, -116.0, 39.1)
    ctx = _ctx(
        eligible=eligible,
        goals={"east": (39.0, -114.8)},
        footprint=seed_fp,
    )

    with patch(
        "peaky_finders.site_suggestions.corridor._existing_eligible_backbone_nodes",
        return_value=[(39.0, -114.8)],
    ):
        routes = plan_corridors_for_goal(ctx, goal_key="east", k=1, cell_m=200.0)

    assert len(routes) == 1
    assert routes[0].hop_count >= 1
    assert routes[0].length_m > 0


@patch("peaky_finders.site_suggestions.corridor.max_hop_range_m", return_value=120_000.0)
@patch("peaky_finders.site_suggestions.corridor.rf_json_for_preset", return_value=_RF_FIXTURE)
@patch("peaky_finders.site_suggestions.corridor.ensure_dem_for_points", side_effect=_mock_ensure_dem)
@patch("peaky_finders.site_suggestions.corridor.mutual_hop_batches", side_effect=_mock_mutual_hop_batches)
def test_plan_corridor_routes_from_backbone_when_attachment_isolated(
    _mock_batch, _mock_dem, _mock_rf, _mock_range
) -> None:
    eligible = box(-116.5, 38.5, -114.5, 39.5)
    seed_fp = box(-116.4, 38.9, -116.0, 39.1)
    ctx = _ctx(
        eligible=eligible,
        goals={"east": (39.0, -114.8)},
        footprint=seed_fp,
    )

    attach_lat, attach_lon = 39.0, -116.2

    def _mock_hop_batches(pairs, *, rf_json: str, **kwargs) -> list[bool]:
        del rf_json, kwargs
        out: list[bool] = []
        for lat_a, lon_a, lat_b, lon_b in pairs:
            involves_attach = (abs(lat_a - attach_lat) < 1e-3 and abs(lon_a - attach_lon) < 1e-3) or (
                abs(lat_b - attach_lat) < 1e-3 and abs(lon_b - attach_lon) < 1e-3
            )
            out.append(not involves_attach)
        return out

    with patch(
        "peaky_finders.site_suggestions.corridor.mutual_hop_batches",
        side_effect=_mock_hop_batches,
    ):
        with patch(
            "peaky_finders.site_suggestions.corridor.attachment_point_toward_goal",
            return_value=(attach_lon, attach_lat),
        ):
            routes = plan_corridors_for_goal(ctx, goal_key="east", k=1, cell_m=200.0, verbose=True)

    assert len(routes) == 1
    assert routes[0].hop_count >= 1


@patch("peaky_finders.site_suggestions.corridor.max_hop_range_m", return_value=120_000.0)
@patch("peaky_finders.site_suggestions.corridor.rf_json_for_preset", return_value=_RF_FIXTURE)
@patch("peaky_finders.site_suggestions.corridor.ensure_dem_for_points", side_effect=_mock_ensure_dem)
@patch("peaky_finders.site_suggestions.corridor.mutual_hop_batches", side_effect=_mock_mutual_hop_batches)
def test_plan_corridor_min_hop_direct_over_hole(_mock_batch, _mock_dem, _mock_rf, _mock_range) -> None:
    """RF hops may cross ineligible land; nodes stay on eligible geometry."""
    outer = box(-116.5, 38.5, -114.5, 39.5)
    hole = box(-115.6, 38.85, -115.2, 39.15)
    eligible = outer.difference(hole)
    seed_fp = box(-116.4, 38.9, -116.0, 39.1)
    ctx = _ctx(
        eligible=eligible,
        goals={"east": (39.0, -114.8)},
        footprint=seed_fp,
    )
    routes = plan_corridors_for_goal(ctx, goal_key="east", k=2, cell_m=150.0)
    assert routes
    assert routes[0].hop_count <= routes[-1].hop_count + 1


def test_max_covered_arc_m_increases_with_footprint() -> None:
    line = LineString([(-116.0, 39.0), (-115.0, 39.0)])
    line_m = gpd.GeoDataFrame(geometry=[line], crs="EPSG:4326").to_crs("EPSG:3857").geometry.iloc[0]
    s0 = max_covered_arc_m(None, line_m, buffer_m=1000.0)
    fp_m = gpd.GeoDataFrame(geometry=[box(-116.2, 38.9, -115.8, 39.1)], crs="EPSG:4326").to_crs(
        "EPSG:3857"
    ).geometry.iloc[0]
    s1 = max_covered_arc_m(fp_m, line_m, buffer_m=1000.0)
    assert s1 > s0


def test_max_covered_arc_m_multipart_coverage() -> None:
    line = LineString([(-116.0, 39.0), (-115.0, 39.0)])
    line_m = gpd.GeoDataFrame(geometry=[line], crs="EPSG:4326").to_crs("EPSG:3857").geometry.iloc[0]
    multi = gpd.GeoDataFrame(
        geometry=[box(-116.2, 38.9, -115.8, 39.1), box(-115.4, 38.9, -115.0, 39.1)],
        crs="EPSG:4326",
    ).to_crs("EPSG:3857").geometry.unary_union
    s = max_covered_arc_m(multi, line_m, buffer_m=1000.0)
    assert s > 0.0


@patch("peaky_finders.site_suggestions.corridor.max_hop_range_m", return_value=120_000.0)
@patch("peaky_finders.site_suggestions.corridor.rf_json_for_preset", return_value=_RF_FIXTURE)
@patch("peaky_finders.site_suggestions.corridor.ensure_dem_for_points", side_effect=_mock_ensure_dem)
@patch("peaky_finders.site_suggestions.corridor.mutual_hop_batches", side_effect=_mock_mutual_hop_batches)
def test_corridor_scoring_prefers_forward_progress(_mock_batch, _mock_dem, _mock_rf, _mock_range) -> None:
    eligible = box(-116.5, 38.5, -114.5, 39.5)
    seed_fp = box(-116.4, 38.9, -116.0, 39.1)
    ctx = _ctx(
        eligible=eligible,
        goals={"east": (39.0, -114.8)},
        footprint=seed_fp,
    )
    routes = plan_corridors_for_goal(ctx, goal_key="east", k=1, cell_m=200.0)
    assert routes
    # Use a multi-segment spine so forward trial extends covered arc along the corridor.
    spine = LineString([(-116.0, 39.0), (-115.5, 39.0), (-114.8, 39.0)])
    routes[0] = routes[0].__class__(
        goal_key=routes[0].goal_key,
        line=spine,
        length_m=routes[0].length_m,
        attachment_lon=routes[0].attachment_lon,
        attachment_lat=routes[0].attachment_lat,
        variant=routes[0].variant,
        hop_count=max(routes[0].hop_count, 2),
    )
    ctx.corridor_state.corridors = routes
    ctx.corridor_state.active_goal_key = "east"
    ctx.corridor_state.corridor_index = 0
    score_ctx = build_corridor_score_context(ctx)
    assert score_ctx is not None

    forward_fp = box(-116.35, 38.88, -115.85, 39.12)
    side_fp = box(-116.15, 39.05, -115.75, 39.25)
    relay_lat, relay_lon = 39.0, -116.15

    forward = score_corridor_trial(
        score_ctx=score_ctx,
        lat=relay_lat,
        lon=relay_lon,
        trial_footprint=forward_fp,
    )
    sideways = score_corridor_trial(
        score_ctx=score_ctx,
        lat=39.12,
        lon=relay_lon,
        trial_footprint=side_fp,
    )
    assert forward is not None
    assert corridor_trial_has_progress(forward)
    if sideways is not None:
        assert corridor_sort_key(forward) >= corridor_sort_key(sideways)


def test_score_corridor_trials_parallel_matches_serial() -> None:
    eligible = box(-116.5, 38.5, -114.5, 39.5)
    seed_fp = box(-116.4, 38.9, -116.0, 39.1)
    ctx = _ctx(
        eligible=eligible,
        goals={"east": (39.0, -114.8)},
        footprint=seed_fp,
    )
    line = LineString([(-116.0, 39.0), (-115.5, 39.0), (-114.8, 39.0)])
    from peaky_finders.site_suggestions.corridor import CorridorPath

    ctx.corridor_state.corridors = [
        CorridorPath(
            goal_key="east",
            line=line,
            length_m=100_000.0,
            attachment_lon=-116.0,
            attachment_lat=39.0,
            hop_count=2,
        )
    ]
    ctx.corridor_state.active_goal_key = "east"
    score_ctx = build_corridor_score_context(ctx)
    assert score_ctx is not None

    trials = [
        (39.0, -116.15, box(-116.35, 38.88, -115.85, 39.12)),
        (39.12, -116.15, box(-116.15, 39.05, -115.75, 39.25)),
    ]
    serial = score_corridor_trials(score_ctx=score_ctx, trials=trials, jobs=1)
    parallel = score_corridor_trials(score_ctx=score_ctx, trials=trials, jobs=4)
    assert len(serial) == len(parallel) == 2
    for s, p in zip(serial, parallel, strict=True):
        if s is None:
            assert p is None
        else:
            assert p is not None
            assert s.delta_s_m == p.delta_s_m
            assert s.captured == p.captured
            assert s.hop_neighbors == p.hop_neighbors


@patch("peaky_finders.site_suggestions.mesh_backbone_candidates.ensure_active_corridor")
def test_generate_corridor_candidates_active_goal(mock_ensure) -> None:
    from peaky_finders.site_suggestions.corridor import CorridorPath

    eligible = box(-116.5, 38.5, -114.5, 39.5)
    seed_fp = box(-116.4, 38.9, -116.0, 39.1)
    ctx = _ctx(
        eligible=eligible,
        goals={"east": (39.0, -114.8)},
        footprint=seed_fp,
    )
    line = LineString([(-116.2, 39.0), (-115.0, 39.0)])
    mock_ensure.return_value = CorridorPath(
        goal_key="east",
        line=line,
        length_m=100_000.0,
        attachment_lon=-116.2,
        attachment_lat=39.0,
        hop_count=2,
    )
    cands = generate_corridor_grow_candidates(ctx)
    assert cands
    assert all(c.strategy.startswith("corridor:") or c.strategy.startswith("goal:") for c in cands)
