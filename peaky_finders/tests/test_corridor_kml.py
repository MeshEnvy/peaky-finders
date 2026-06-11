"""Tests for per-goal corridor debug KML."""

from __future__ import annotations

from pathlib import Path

from shapely.geometry import LineString

from peaky_finders.site_suggestions.candidates import SiteCandidate
from peaky_finders.site_suggestions.context import SiteSuggestionContext
from peaky_finders.site_suggestions.corridor import CorridorGrowState, CorridorPath
from peaky_finders.site_suggestions.corridor_kml import (
    CORRIDOR_PATH_LINE_COLOR,
    CORRIDOR_PATH_STYLE_ID,
    CorridorGoalDebug,
    CorridorRouteRecord,
    CorridorTrialRecord,
    OUTPUT_KML_NAME,
    _ensure_viewshed_png,
    _prepare_output_kml_for_earth,
    _write_goal_kml,
    corridor_goal_kml_path,
    init_corridor_goal_kml,
    mark_active_corridor,
    record_corridor_pick,
    record_corridor_trial_viewshed,
    record_corridor_trials,
    record_planned_corridors,
    update_corridor_planning_state,
    write_corridor_goal_kml,
)
from peaky_finders.site_suggestions.planner import CandidateOutcome, CandidateTrial
from rf_fixtures import make_goal_entry
from peaky_finders.sites_job import (
    SuggestConfig,
    MeshBackboneStrategyConfig,
)


def _minimal_ctx(tmp_path: Path) -> SiteSuggestionContext:
    mb = MeshBackboneStrategyConfig(goal_order=["east"])
    cfg = SuggestConfig(mesh_backbone=mb)
    ctx = SiteSuggestionContext(
        preset=type(
            "P",
            (),
            {
                "sites": {"seed": type("E", (), {"lat": 39.0, "lon": -116.0})()},
                "goals": {"east": make_goal_entry("East", (39.0, -115.0))},
                "repeaters": {"seed": type("E", (), {"lat": 39.0, "lon": -116.0})()},
            },
        )(),
        plan=None,  # type: ignore[arg-type]
        grid=None,  # type: ignore[arg-type]
        eligible_ll=None,  # type: ignore[arg-type]
        aoi_ll=None,  # type: ignore[arg-type]
        target_ll=None,  # type: ignore[arg-type]
        suggest_root=tmp_path / "suggest",
        cfg=cfg,
        dem_mirror_root=tmp_path,
        eligible_sha="abc",
        jobs=1,
        verbose=False,
    )
    ctx.corridor_state = CorridorGrowState()
    ctx.corridor_state.active_goal_key = "east"
    return ctx


def test_viewshed_overlay_href_uses_splat_png(tmp_path: Path) -> None:
    workdir = tmp_path / "viewsheds" / "trial_b"
    workdir.mkdir(parents=True)
    (workdir / "splat.png").write_bytes(b"png")
    (workdir / OUTPUT_KML_NAME).write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <GroundOverlay>
    <Icon><href>output.ppm</href></Icon>
    <LatLonBox>
      <north>39.1</north><south>39.0</south><east>-115.7</east><west>-115.9</west>
      <rotation>0</rotation>
    </LatLonBox>
  </GroundOverlay>
</kml>""",
        encoding="utf-8",
    )
    from_kml = tmp_path / "suggest" / "corridors" / "east.kml"
    from_kml.parent.mkdir(parents=True)
    png = _ensure_viewshed_png(workdir)
    assert png is not None
    assert png.name == "splat.png"


def test_prepare_output_kml_patches_ppm_href(tmp_path: Path, monkeypatch) -> None:
    workdir = tmp_path / "ws"
    workdir.mkdir()
    (workdir / "output.ppm").write_bytes(b"ppm")
    (workdir / OUTPUT_KML_NAME).write_text(
        '<?xml version="1.0"?><kml><GroundOverlay><Icon><href>output.ppm</href></Icon></GroundOverlay></kml>',
        encoding="utf-8",
    )
    called: list[Path] = []

    def _fake_ensure(*, site_name: str, data_dir: Path) -> None:
        del site_name
        called.append(data_dir)
        (data_dir / "splat.png").write_bytes(b"png")
        raw = (data_dir / OUTPUT_KML_NAME).read_text(encoding="utf-8")
        (data_dir / OUTPUT_KML_NAME).write_text(
            raw.replace("output.ppm", "splat.png"),
            encoding="utf-8",
        )

    monkeypatch.setattr(
        "peaky_finders.splat_pipeline.ensure_splat_raster_png",
        _fake_ensure,
    )
    out = _prepare_output_kml_for_earth(workdir)
    assert out is not None
    assert called == [workdir.resolve()]
    assert "splat.png" in out.read_text(encoding="utf-8")


def test_init_corridor_goal_kml_writes_immediately(tmp_path: Path) -> None:
    ctx = _minimal_ctx(tmp_path)
    out = init_corridor_goal_kml(ctx, "east")
    assert out is not None
    assert out.is_file()
    xml = out.read_text(encoding="utf-8")
    assert "Corridor east (planning)" in xml
    assert "Goal: east" in xml


def test_update_corridor_planning_state_refreshes_kml(tmp_path: Path) -> None:
    ctx = _minimal_ctx(tmp_path)
    init_corridor_goal_kml(ctx, "east")
    update_corridor_planning_state(
        ctx,
        "east",
        attachment=(39.01, -115.5),
        relay_nodes=[(39.0, -115.8), (39.0, -115.6)],
        note="evaluating RF hops",
    )
    out = corridor_goal_kml_path(ctx.suggest_root, "east")
    xml = out.read_text(encoding="utf-8")
    assert "Attachment" in xml
    assert "Relay candidates (2)" in xml
    assert "evaluating RF hops" in xml


def test_corridor_goal_kml_visibility_and_hrefs(tmp_path: Path) -> None:
    ctx = _minimal_ctx(tmp_path)
    suggest_root = ctx.suggest_root
    workdir = tmp_path / "viewsheds" / "trial_a"
    workdir.mkdir(parents=True)
    (workdir / "splat.png").write_bytes(b"png")
    (workdir / OUTPUT_KML_NAME).write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <GroundOverlay>
    <Icon><href>output.ppm</href></Icon>
    <LatLonBox>
      <north>39.1</north><south>39.0</south><east>-115.7</east><west>-115.9</west>
      <rotation>0</rotation>
    </LatLonBox>
  </GroundOverlay>
</kml>""",
        encoding="utf-8",
    )

    line_a = LineString([(-116.0, 39.0), (-115.5, 39.0)])
    line_b = LineString([(-116.0, 39.1), (-115.5, 39.1)])

    record_planned_corridors(
        ctx,
        goal_key="east",
        corridors=[
            CorridorPath(
                goal_key="east",
                line=line_a,
                length_m=50_000.0,
                attachment_lon=-116.0,
                attachment_lat=39.0,
                variant=0,
            ),
            CorridorPath(
                goal_key="east",
                line=line_b,
                length_m=52_000.0,
                attachment_lon=-116.0,
                attachment_lat=39.0,
                variant=1,
            ),
        ],
        plan_generation=1,
    )
    mark_active_corridor(ctx, goal_key="east", variant=0, plan_generation=1)

    record_corridor_trial_viewshed(
        ctx,
        goal_key="east",
        iteration=3,
        phase="coarse",
        index=1,
        lat=39.05,
        lon=-115.8,
        elev_m=2100.0,
        strategy="corridor:line",
        workdir=workdir,
        has_footprint=True,
    )

    trial = CandidateTrial(
        index=1,
        candidate=SiteCandidate(lat=39.05, lon=-115.8, elev_m=2100.0, strategy="corridor:line"),
        outcome=CandidateOutcome.ZERO_GAIN,
        point_gain_cells=0,
        footprint_gain_cells=0,
        footprint_area_km2=1.0,
        workdir=workdir,
        detail="no progress",
        phase="coarse",
    )
    record_corridor_trials(ctx, goal_key="east", iteration=3, trials=[trial])
    record_corridor_pick(
        ctx,
        goal_key="east",
        iteration=3,
        lat=39.06,
        lon=-115.7,
        elev_m=2200.0,
        strategy="corridor:line",
        workdir=workdir,
    )

    out = write_corridor_goal_kml(ctx, goal_key="east")
    assert out is not None
    assert out == corridor_goal_kml_path(suggest_root, "east")
    xml = out.read_text(encoding="utf-8")

    assert "Coarse trials" in xml
    assert "Refine trials" not in xml
    assert "Gen 1 variant 2" in xml
    assert "Pick #3" in xml
    assert "Viewshed #3" in xml
    assert "Viewshed [1]" in xml
    assert "GroundOverlay" in xml
    assert "NetworkLink" not in xml
    assert f'id="{CORRIDOR_PATH_STYLE_ID}"' in xml
    assert CORRIDOR_PATH_LINE_COLOR in xml
    assert f"#{CORRIDOR_PATH_STYLE_ID}" in xml

    rel = Path("../../viewsheds/trial_a/splat.png").as_posix()
    assert rel in xml
    assert "output.ppm" not in xml
    assert "output.kml" not in xml


def test_write_goal_kml_direct(tmp_path: Path) -> None:
    dbg = CorridorGoalDebug(
        goal_key="vegas",
        goal_lat=36.17,
        goal_lon=-115.14,
        status="blocked",
        routes=[
            CorridorRouteRecord(
                plan_generation=1,
                variant=0,
                line=LineString([(-116.0, 36.0), (-115.2, 36.1)]),
                length_m=80_000.0,
            )
        ],
        active_variant=0,
        active_plan_generation=1,
    )
    out = tmp_path / "vegas.kml"
    _write_goal_kml(out_kml=out, dbg=dbg)
    xml = out.read_text(encoding="utf-8")
    assert "Corridor vegas (blocked)" in xml
    assert "Corridor path (gen 1, variant 1)" in xml
    assert CORRIDOR_PATH_LINE_COLOR in xml
    assert '\n    <Document>' in xml
    assert "-116.00000000,36.00000000,0\n-115.20000000,36.10000000,0" in xml


def test_write_goal_kml_planning_defers_trial_overlays(tmp_path: Path) -> None:
    dbg = CorridorGoalDebug(
        goal_key="east",
        goal_lat=39.0,
        goal_lon=-114.8,
        status="planning",
        trials=[
            CorridorTrialRecord(
                iteration=1,
                phase="coarse",
                index=1,
                lat=39.0,
                lon=-115.0,
                elev_m=2000.0,
                strategy="corridor:east",
                outcome="runner_up",
                detail="test",
                workdir=tmp_path / "trial_a",
            )
        ],
    )
    out = tmp_path / "east.kml"
    _write_goal_kml(out_kml=out, dbg=dbg, verbose=False)
    xml = out.read_text(encoding="utf-8")
    assert "Coarse trials" not in xml
    assert "GroundOverlay" not in xml
