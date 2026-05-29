"""Tests for mesh-grow-ai tool dispatch."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from shapely.geometry import box

from peaky_finders.site_suggestions.context import BackboneSite, SiteSuggestionContext
from peaky_finders.site_suggestions.depth_grid import build_coverage_depth_grid
from peaky_finders.site_suggestions.providers.mesh_grow_ai.session import AgentSession
from peaky_finders.site_suggestions.providers.mesh_grow_ai.tools import dispatch_tool
from peaky_finders.sites_job import (
    BundleSiteSuggestionsConfig,
    MeshBackboneGoalEntry,
    MeshGrowAiStrategyConfig,
    SiteEntry,
    SiteSuggestionStrategy,
    load_preset,
    write_preset_document,
)

_SUGGEST_SIMULATION = {
    "provider": "los",
    "radius_km": 60.0,
    "modem_presets": {
        "meshcore-us": {
            "frequency_mhz": 910.525,
            "bandwidth_khz": 62.5,
            "spreading_factor": 7,
            "coding_rate": 5,
            "implementation_margin_db": 3.0,
            "power_dbm": 22.0,
            "sensitivity_dbm": -121.0,
        }
    },
    "environment_presets": {
        "test-desert": {
            "climate": "desert",
            "polarization": "vertical",
            "clutter_height_m": 1.0,
        }
    },
    "modem": "meshcore-us",
    "environment": "test-desert",
    "transmitter": {"height_m": 2.0, "gain_dbi": 2.0, "loss_db": 0.0},
    "receiver": {"height_m": 2.0, "gain_dbi": 2.0, "loss_db": 0.0},
}


def _ctx(tmp_path: Path) -> SiteSuggestionContext:
    eligible = box(-120.0, 35.0, -114.0, 42.0)
    grid = build_coverage_depth_grid(
        aoi_ll=eligible,
        target_ll=eligible,
        footprint_gpkg_paths=[],
        max_raster_dimension=128,
    )
    preset_path = tmp_path / "job.yaml"
    write_preset_document(
        preset_path,
        {
            "simulation": dict(_SUGGEST_SIMULATION),
            "display": {"colormap": "rainbow", "min_dbm": -130.0, "max_dbm": -80.0},
            "sites": {
                "seed": {"name": "Seed", "loc": [39.0, -117.0]},
            },
            "bundle": {
                "site_suggestions": {
                    "strategy": "mesh-grow-ai",
                    "mesh_grow_ai": {
                        "goals": {
                            "g1": {"loc": [39.5, -116.5]},
                            "g2": {"loc": "seed"},
                        },
                    },
                },
            },
        },
    )
    preset = load_preset(preset_path)
    cfg = preset.bundle.site_suggestions
    return SiteSuggestionContext(
        preset=preset,
        plan=type("Plan", (), {"viewsheds_root": tmp_path / "viewsheds"})(),
        grid=grid,
        eligible_ll=eligible,
        aoi_ll=eligible,
        target_ll=eligible,
        suggest_root=tmp_path / "suggest",
        cfg=cfg,
        dem_mirror_root=tmp_path / "dem",
        eligible_sha="test",
        jobs=1,
        verbose=False,
        session_sites=[BackboneSite(slug="seed", lat=39.0, lon=-117.0)],
    )


def test_snapshot_and_propose_requires_evaluate(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    session = AgentSession(iteration=1)
    snap = dispatch_tool("snapshot", {}, ctx=ctx, session=session, preset_path=tmp_path / "job.yaml")
    assert snap["iteration"] == 1
    assert len(snap["goals"]) == 2
    assert "g2" in {g["key"] for g in snap["goals"]}

    inside = dispatch_tool(
        "point_in_eligible",
        {"lat": 39.1, "lon": -116.9},
        ctx=ctx,
        session=session,
        preset_path=tmp_path / "job.yaml",
    )
    assert inside["eligible"] is True

    bad_propose = dispatch_tool(
        "propose_site",
        {"lat": 39.1, "lon": -116.9},
        ctx=ctx,
        session=session,
        preset_path=tmp_path / "job.yaml",
    )
    assert "error" in bad_propose


def test_submit_proposals_caps(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    session = AgentSession(iteration=2)
    session.evaluated[(3910000, -11690000)] = {"connected": True}
    session.proposals.append({"lat": 39.1, "lon": -116.9})
    result = dispatch_tool("submit_proposals", {}, ctx=ctx, session=session, preset_path=tmp_path / "job.yaml")
    assert result["count"] == 1
    assert session.submitted is True


def test_mock_evaluate_site_budget(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    ctx.cfg.mesh_grow_ai.max_viewshed_evals_per_episode = 1
    session = AgentSession(iteration=1)
    fp = box(-117.1, 38.9, -116.9, 39.1)

    with patch(
        "peaky_finders.site_suggestions.providers.mesh_grow_ai.tools.run_ephemeral_viewshed_footprint",
        return_value=fp,
    ):
        r1 = dispatch_tool(
            "evaluate_site",
            {"lat": 39.0, "lon": -117.0},
            ctx=ctx,
            session=session,
            preset_path=tmp_path / "job.yaml",
        )
        r2 = dispatch_tool(
            "evaluate_site",
            {"lat": 39.05, "lon": -116.95},
            ctx=ctx,
            session=session,
            preset_path=tmp_path / "job.yaml",
        )
    assert "connected" in r1
    assert r2.get("error") == "max_viewshed_evals_per_episode exceeded"


def test_mesh_grow_ai_registry() -> None:
    from peaky_finders.site_suggestions.providers.registry import resolve_site_suggestion_strategy

    cfg = BundleSiteSuggestionsConfig(
        strategy=SiteSuggestionStrategy.MESH_GROW_AI,
        mesh_grow_ai=MeshGrowAiStrategyConfig(
            goals={"g": MeshBackboneGoalEntry(loc=(39.0, -117.0))},
        ),
    )
    provider = resolve_site_suggestion_strategy(cfg)
    assert provider.name == "mesh-grow-ai"
    assert provider.planner_hooks(cfg).trial_mode.value == "mesh_grow"
