"""Greedy AOI coverage site planner."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path

import geopandas as gpd
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

from peaky_finders.build_configure import BuildConfigurePlan
from peaky_finders.bundle_clips import ELIGIBLE_LAYER
from peaky_finders.site_suggestions.candidates import SiteCandidate, generate_site_candidates
from peaky_finders.site_suggestions.depth_grid import CoverageDepthGrid, build_coverage_depth_grid
from peaky_finders.site_suggestions.ephemeral_viewshed import candidate_viewshed_workdir, run_ephemeral_viewshed_footprint
from peaky_finders.site_suggestions.log import suggest_log
from peaky_finders.sites_job import Preset, resolved_site_suggestions_config


@dataclass(frozen=True)
class PlannedSuggestion:
    lat: float
    lon: float
    elev_m: float | None
    gain_cells: int
    strategy: str
    iteration: int
    rationale: str


class CandidateOutcome(StrEnum):
    SELECTED = "selected"
    RUNNER_UP = "runner_up"
    ZERO_GAIN = "zero_gain"
    EMPTY_FOOTPRINT = "empty_footprint"
    VIEWSHED_FAILED = "viewshed_failed"


@dataclass
class CandidateTrial:
    index: int
    candidate: SiteCandidate
    outcome: CandidateOutcome
    point_gain_cells: int
    footprint_gain_cells: int | None
    footprint_area_km2: float | None
    workdir: Path | None
    detail: str


def _composite_by_role(plan: BuildConfigurePlan, role: str):
    for c in plan.composites:
        if c.role == role:
            return c
    raise ValueError(f"composite {role!r} missing from build plan")


def _read_union_geometry(gpkg: Path, layer: str) -> BaseGeometry:
    gdf = gpd.read_file(gpkg, layer=layer)
    if gdf.empty:
        raise ValueError(f"empty geometry in {gpkg} layer {layer!r}")
    geom = gdf.geometry.union_all()
    if geom is None or geom.is_empty:
        raise ValueError(f"empty union in {gpkg} layer {layer!r}")
    return geom


def _footprint_paths_for_seed_sites(plan: BuildConfigurePlan, preset: Preset) -> list[Path]:
    del preset
    paths: list[Path] = []
    for ws in plan.viewshed_workspaces:
        p = ws.coverage_gpkg.expanduser().resolve()
        if p.is_file():
            paths.append(p)
    return paths


def _footprint_area_km2(footprint_ll: BaseGeometry) -> float:
    gm = gpd.GeoDataFrame(geometry=[footprint_ll], crs="EPSG:4326").to_crs("EPSG:3857").geometry.iloc[0]
    return float(gm.area) / 1_000_000.0


def _format_loc(lat: float, lon: float) -> str:
    return f"{lat:.6f}°N, {lon:.6f}°W" if lon < 0 else f"{lat:.6f}°, {lon:.6f}°"


def _format_candidate_brief(c: SiteCandidate) -> str:
    elev = f" elev={c.elev_m:.0f}m" if c.elev_m is not None else " elev=?"
    return f"{c.strategy}{elev} @ {_format_loc(c.lat, c.lon)}"


def _log_planner_config(
    *,
    verbose: bool,
    cfg,
    goal: int,
    grid: CoverageDepthGrid,
    seed_paths: list[Path],
    preset: Preset,
) -> None:
    if not verbose:
        return
    uncovered = grid.uncovered_fraction(goal_depth=goal)
    uncovered_cells = int(grid.uncovered_mask(goal_depth=goal).sum())
    by_type: dict[str, int] = {}
    for ent in preset.sites.values():
        by_type[ent.type.value] = by_type.get(ent.type.value, 0) + 1

    suggest_log(verbose, "site suggest: ── planner configuration ──")
    suggest_log(verbose, f"  coverage_goal_depth: {goal}")
    suggest_log(verbose, f"  planner_raster_dimension: {cfg.planner_raster_dimension}")
    suggest_log(verbose, f"  max_candidates_per_round: {cfg.max_candidates_per_round}")
    suggest_log(verbose, f"  peak_cluster_radius_m: {cfg.peak_cluster_radius_m}")
    suggest_log(verbose, f"  uncovered_stop_pct: {cfg.uncovered_stop_pct}")
    suggest_log(verbose, "site suggest: ── seed sites ──")
    for slug, ent in sorted(preset.sites.items()):
        suggest_log(
            verbose,
            f"  {slug}: type={ent.type.value} {_format_loc(ent.lat, ent.lon)}",
        )
    suggest_log(verbose, f"  totals: {by_type}")
    suggest_log(verbose, "site suggest: ── initial coverage grid ──")
    suggest_log(verbose, f"  grid: {grid.cols}×{grid.rows} px  AOI cells: {grid.aoi_cell_count}")
    suggest_log(
        verbose,
        f"  uncovered vs depth≥{goal}: {100.0 * uncovered:.2f}% ({uncovered_cells} cells)",
    )
    suggest_log(verbose, f"  seed footprints loaded: {len(seed_paths)}")


def _log_candidate_shortlist(*, verbose: bool, iteration: int, candidates: list[SiteCandidate]) -> None:
    if not verbose:
        return
    suggest_log(verbose, f"site suggest: ── iteration {iteration} candidate shortlist ({len(candidates)}) ──")
    for i, c in enumerate(candidates, start=1):
        suggest_log(verbose, f"  [{i}] {_format_candidate_brief(c)}")


def _log_trial_results(
    *,
    verbose: bool,
    iteration: int,
    goal: int,
    trials: list[CandidateTrial],
    best: PlannedSuggestion | None,
) -> None:
    if not verbose:
        return
    suggest_log(verbose, f"site suggest: ── iteration {iteration} viewshed trials ({len(trials)}) ──")
    ranked = sorted(
        trials,
        key=lambda t: (
            t.footprint_gain_cells is None,
            -(t.footprint_gain_cells or 0),
            t.index,
        ),
    )
    for t in ranked:
        c = t.candidate
        gain_s = "—" if t.footprint_gain_cells is None else str(t.footprint_gain_cells)
        area_s = "—" if t.footprint_area_km2 is None else f"{t.footprint_area_km2:.1f} km²"
        wd_s = "—" if t.workdir is None else str(t.workdir)
        suggest_log(
            verbose,
            f"  [{t.index}] {_format_candidate_brief(c)}",
        )
        suggest_log(
            verbose,
            f"       point_pre_gain={t.point_gain_cells} cells  "
            f"footprint_gain={gain_s} cells  footprint_area={area_s}",
        )
        suggest_log(verbose, f"       outcome={t.outcome.value}  workdir={wd_s}")
        if t.detail:
            suggest_log(verbose, f"       note: {t.detail}")

    if best is None:
        suggest_log(verbose, f"site suggest: iteration {iteration} — no candidate improved coverage")
        return

    winners = [t for t in trials if t.outcome == CandidateOutcome.SELECTED]
    runners = [t for t in trials if t.outcome == CandidateOutcome.RUNNER_UP]
    suggest_log(verbose, f"site suggest: ── iteration {iteration} selection ──")
    suggest_log(
        verbose,
        f"  chosen: {_format_candidate_brief(winners[0].candidate) if winners else _format_loc(best.lat, best.lon)}",
    )
    suggest_log(verbose, f"  marginal_gain: {best.gain_cells} AOI cells toward depth≥{goal}")
    if runners:
        top_run = max(runners, key=lambda t: t.footprint_gain_cells or 0)
        margin = best.gain_cells - (top_run.footprint_gain_cells or 0)
        suggest_log(
            verbose,
            f"  runner-up: [{top_run.index}] gain={top_run.footprint_gain_cells} cells  "
            f"margin={margin} cells",
        )
    rejected = [t for t in trials if t.outcome not in (CandidateOutcome.SELECTED, CandidateOutcome.RUNNER_UP)]
    if rejected:
        suggest_log(verbose, f"  rejected {len(rejected)} candidate(s):")
        for t in rejected:
            suggest_log(verbose, f"    [{t.index}] {t.outcome.value}: {t.detail or '—'}")


def _log_selection_summary(
    *,
    verbose: bool,
    iteration: int,
    best: PlannedSuggestion,
    uncovered_pct: float,
    n_requested: int,
) -> None:
    suggest_log(
        verbose,
        f"site suggest: ✓ pick {iteration}/{n_requested}  "
        f"gain={best.gain_cells} cells  uncovered={uncovered_pct:.2f}%  "
        f"{_format_loc(best.lat, best.lon)}  strategy={best.strategy}",
    )
    if verbose:
        suggest_log(verbose, f"  rationale: {best.rationale}")


def plan_greedy_site_suggestions(
    *,
    preset: Preset,
    preset_path: Path,
    plan: BuildConfigurePlan,
    n_suggestions: int,
    suggest_root: Path,
    footprint_runner=run_ephemeral_viewshed_footprint,
    verbose: bool = False,
) -> list[PlannedSuggestion]:
    """Pick ``n_suggestions`` sites maximizing marginal AOI depth coverage."""
    if preset.bundle is None:
        raise ValueError("site suggestions require preset bundle.*")
    if n_suggestions <= 0:
        return []

    cfg = resolved_site_suggestions_config(preset.bundle)
    goal = int(cfg.coverage_goal_depth)

    aoi_gpkg = _composite_by_role(plan, "aoi").union_gpkg
    elig_gpkg = _composite_by_role(plan, "eligible").union_gpkg
    aoi_ll = _read_union_geometry(aoi_gpkg, "aoi")
    eligible_ll = _read_union_geometry(elig_gpkg, ELIGIBLE_LAYER)

    seed_paths = _footprint_paths_for_seed_sites(plan, preset)
    grid = build_coverage_depth_grid(
        aoi_ll=aoi_ll,
        footprint_gpkg_paths=seed_paths,
        max_raster_dimension=int(cfg.planner_raster_dimension),
    )

    _log_planner_config(
        verbose=verbose,
        cfg=cfg,
        goal=goal,
        grid=grid,
        seed_paths=seed_paths,
        preset=preset,
    )

    stop_frac = float(cfg.uncovered_stop_pct) / 100.0
    if grid.uncovered_fraction(goal_depth=goal) <= stop_frac:
        msg = (
            f"site suggest: AOI already ≥{goal} depth "
            f"(uncovered {100.0 * grid.uncovered_fraction(goal_depth=goal):.2f}% ≤ {cfg.uncovered_stop_pct}%)"
        )
        print(msg, flush=True)
        suggest_log(verbose, msg)
        return []

    dem_mirror = plan.splat_tiles_root
    winners: list[PlannedSuggestion] = []

    for iteration in range(1, n_suggestions + 1):
        uncovered_before = grid.uncovered_fraction(goal_depth=goal)
        if uncovered_before <= stop_frac:
            print(f"site suggest: stopping at iter {iteration - 1} — AOI goal met", flush=True)
            break

        suggest_log(
            verbose,
            f"site suggest: ══ iteration {iteration}/{n_suggestions} "
            f"(uncovered {100.0 * uncovered_before:.2f}% below depth≥{goal}) ══",
        )

        candidates = generate_site_candidates(
            eligible_ll=eligible_ll,
            grid=grid,
            goal_depth=goal,
            dem_mirror_root=dem_mirror,
            max_candidates=int(cfg.max_candidates_per_round),
            cluster_radius_m=float(cfg.peak_cluster_radius_m),
            iteration=iteration - 1,
            verbose=verbose,
        )
        _log_candidate_shortlist(verbose=verbose, iteration=iteration, candidates=candidates)

        if not candidates:
            print(f"site suggest: no candidates at iteration {iteration}", flush=True)
            break

        best: PlannedSuggestion | None = None
        best_footprint: BaseGeometry | None = None
        trials: list[CandidateTrial] = []

        for ci, cand in enumerate(candidates, start=1):
            pt = Point(cand.lon, cand.lat)
            point_gain = grid.marginal_gain_cells(pt, goal_depth=goal)
            wd = candidate_viewshed_workdir(
                preset=preset,
                viewshed_root=plan.viewsheds_root,
                lat=cand.lat,
                lon=cand.lon,
            )

            suggest_log(
                verbose,
                f"site suggest:   trial [{ci}/{len(candidates)}] {_format_candidate_brief(cand)} "
                f"point_pre_gain={point_gain} cells → viewshed…",
            )

            try:
                footprint = footprint_runner(
                    preset=preset,
                    preset_path=preset_path,
                    lat=cand.lat,
                    lon=cand.lon,
                    workdir=wd,
                )
            except Exception as exc:
                msg = f"viewshed failed: {exc}"
                print(f"site suggest: candidate [{ci}] failed ({cand.lat:.5f},{cand.lon:.5f}): {exc}", flush=True)
                trials.append(
                    CandidateTrial(
                        index=ci,
                        candidate=cand,
                        outcome=CandidateOutcome.VIEWSHED_FAILED,
                        point_gain_cells=point_gain,
                        footprint_gain_cells=None,
                        footprint_area_km2=None,
                        workdir=wd,
                        detail=msg,
                    )
                )
                continue

            if footprint is None or footprint.is_empty:
                trials.append(
                    CandidateTrial(
                        index=ci,
                        candidate=cand,
                        outcome=CandidateOutcome.EMPTY_FOOTPRINT,
                        point_gain_cells=point_gain,
                        footprint_gain_cells=None,
                        footprint_area_km2=None,
                        workdir=wd,
                        detail="viewshed produced no footprint polygon",
                    )
                )
                continue

            gain = grid.marginal_gain_cells(footprint, goal_depth=goal)
            area_km2 = _footprint_area_km2(footprint)
            if gain <= 0:
                trials.append(
                    CandidateTrial(
                        index=ci,
                        candidate=cand,
                        outcome=CandidateOutcome.ZERO_GAIN,
                        point_gain_cells=point_gain,
                        footprint_gain_cells=gain,
                        footprint_area_km2=area_km2,
                        workdir=wd,
                        detail=(
                            f"footprint covers {area_km2:.1f} km² but adds 0 new AOI cells "
                            f"toward depth≥{goal} (already covered or outside AOI need)"
                        ),
                    )
                )
                continue

            rationale = (
                f"Greedy suggest #{iteration}: +{gain} AOI cells toward depth≥{goal} "
                f"({cand.strategy} candidate)"
            )
            pick = PlannedSuggestion(
                lat=cand.lat,
                lon=cand.lon,
                elev_m=cand.elev_m,
                gain_cells=gain,
                strategy=cand.strategy,
                iteration=iteration,
                rationale=rationale,
            )
            trials.append(
                CandidateTrial(
                    index=ci,
                    candidate=cand,
                    outcome=CandidateOutcome.RUNNER_UP,
                    point_gain_cells=point_gain,
                    footprint_gain_cells=gain,
                    footprint_area_km2=area_km2,
                    workdir=wd,
                    detail=f"footprint {area_km2:.1f} km²",
                )
            )
            if best is None or gain > best.gain_cells:
                if best is not None:
                    for t in trials:
                        if t.outcome == CandidateOutcome.SELECTED:
                            t.outcome = CandidateOutcome.RUNNER_UP
                best = pick
                best_footprint = footprint
                trials[-1].outcome = CandidateOutcome.SELECTED
                trials[-1].detail = f"current best gain={gain} cells  footprint {area_km2:.1f} km²"

        if best is None or best_footprint is None:
            print(f"site suggest: no improving candidate at iteration {iteration}", flush=True)
            _log_trial_results(verbose=verbose, iteration=iteration, goal=goal, trials=trials, best=None)
            break

        _log_trial_results(verbose=verbose, iteration=iteration, goal=goal, trials=trials, best=best)

        grid.add_footprint(best_footprint)
        winners.append(best)
        uncovered_after = 100.0 * grid.uncovered_fraction(goal_depth=goal)
        print(
            f"site suggest: pick {iteration}/{n_suggestions} "
            f"gain={best.gain_cells} cells uncovered={uncovered_after:.2f}% "
            f"({best.lat:.5f}, {best.lon:.5f})",
            flush=True,
        )
        _log_selection_summary(
            verbose=verbose,
            iteration=iteration,
            best=best,
            uncovered_pct=uncovered_after,
            n_requested=n_suggestions,
        )

    if verbose and winners:
        suggest_log(verbose, "site suggest: ── final summary ──")
        for w in winners:
            suggest_log(
                verbose,
                f"  #{w.iteration}: gain={w.gain_cells}  {_format_loc(w.lat, w.lon)}  {w.strategy}",
            )

    return winners


def planned_to_preset_entries(winners: list[PlannedSuggestion]) -> list[dict]:
    out: list[dict] = []
    for w in winners:
        name = f"Suggested {w.iteration}"
        if w.elev_m is not None:
            name = f"{name} ({int(w.elev_m)} m)"
        ent: dict = {
            "_suggest_iteration": w.iteration,
            "name": name,
            "loc": [w.lat, w.lon],
            "rationale": w.rationale,
        }
        if w.elev_m is not None:
            ent["elevation_m"] = float(w.elev_m)
        out.append(ent)
    return out


def write_suggest_run_manifest(
    suggest_root: Path,
    *,
    preset_path: Path,
    n_requested: int,
    winners: list[PlannedSuggestion],
    new_slugs: list[str],
) -> Path:
    root = Path(suggest_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    out = root / "last_run.json"
    body = {
        "format": "peaky_site_suggest/v1",
        "preset": str(preset_path.resolve()),
        "at": datetime.now(timezone.utc).isoformat(),
        "n_requested": int(n_requested),
        "n_written": len(new_slugs),
        "slugs": new_slugs,
        "picks": [
            {
                "slug": slug,
                "lat": w.lat,
                "lon": w.lon,
                "elev_m": w.elev_m,
                "gain_cells": w.gain_cells,
                "strategy": w.strategy,
                "rationale": w.rationale,
            }
            for slug, w in zip(new_slugs, winners, strict=False)
        ],
    }
    out.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out
