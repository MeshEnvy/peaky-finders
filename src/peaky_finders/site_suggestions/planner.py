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
from peaky_finders.site_suggestions.batch_viewshed import run_candidate_batch_viewsheds
from peaky_finders.site_suggestions.candidates import SiteCandidate, generate_refine_candidates
from peaky_finders.site_suggestions.context import BackboneSite, SiteSuggestionContext
from peaky_finders.site_suggestions.depth_grid import CoverageDepthGrid, build_coverage_depth_grid
from peaky_finders.site_suggestions.log import suggest_log, suggest_step
from peaky_finders.site_suggestions.strategies.base import StrategyRefineSettings
from peaky_finders.site_suggestions.strategies.registry import resolve_site_suggestion_strategy
from peaky_finders.site_suggestions.mesh_backbone_geom import validate_mesh_backbone_site_slugs
from peaky_finders.sites_job import Preset, SiteSuggestionCoverageTarget, SiteSuggestionStrategy, resolved_site_suggestions_config


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
    footprint: BaseGeometry | None = None


@dataclass(frozen=True)
class _CandidateTrialEval:
    trial: CandidateTrial
    pick: PlannedSuggestion | None


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


def _coverage_target_label(target: SiteSuggestionCoverageTarget) -> str:
    if target == SiteSuggestionCoverageTarget.ELIGIBLE:
        return "eligible"
    return "AOI"


def _coverage_target_geometry(
    target: SiteSuggestionCoverageTarget,
    *,
    aoi_ll: BaseGeometry,
    eligible_ll: BaseGeometry,
) -> BaseGeometry:
    if target == SiteSuggestionCoverageTarget.ELIGIBLE:
        return eligible_ll
    return aoi_ll


def _log_planner_config(
    *,
    verbose: bool,
    cfg,
    strategy_name: str,
    goal: int,
    grid: CoverageDepthGrid,
    seed_paths: list[Path],
    preset: Preset,
    jobs: int,
) -> None:
    if not verbose:
        return
    uncovered = grid.uncovered_fraction(goal_depth=goal)
    uncovered_cells = int(grid.uncovered_mask(goal_depth=goal).sum())
    by_type: dict[str, int] = {}
    for ent in preset.sites.values():
        by_type[ent.type.value] = by_type.get(ent.type.value, 0) + 1

    lg = cfg.land_grab
    mb = cfg.mesh_backbone
    suggest_log(verbose, "site suggest: ── planner configuration ──")
    suggest_log(verbose, f"  strategy: {strategy_name}")
    suggest_log(verbose, f"  coverage_target: {cfg.coverage_target.value}")
    suggest_log(verbose, f"  coverage_goal_depth: {goal}")
    suggest_log(verbose, f"  planner_raster_dimension: {cfg.planner_raster_dimension}")
    if strategy_name == "mesh-backbone":
        suggest_log(verbose, f"  max_candidates_per_round: {mb.max_candidates_per_round}")
        suggest_log(verbose, f"  link_buffer_m: {mb.link_buffer_m}")
        suggest_log(verbose, f"  sample_spacing_m: {mb.sample_spacing_m}")
        suggest_log(verbose, f"  configured links: {len(mb.links)}")
        if mb.max_nodes is not None:
            suggest_log(verbose, f"  max_nodes: {mb.max_nodes}")
        suggest_log(verbose, f"  refine_enabled: {mb.refine_enabled}")
        suggest_log(verbose, f"  refine_top_n: {mb.refine_top_n}")
        suggest_log(verbose, f"  refine_radius_m: {mb.refine_radius_m}")
        suggest_log(verbose, f"  refine_spacing_m: {mb.refine_spacing_m}")
    else:
        suggest_log(verbose, f"  max_candidates_per_round: {lg.max_candidates_per_round}")
        suggest_log(verbose, f"  max_clusters_per_round: {lg.max_clusters_per_round}")
        suggest_log(verbose, f"  peak_cluster_radius_m: {lg.peak_cluster_radius_m}")
        suggest_log(verbose, f"  cluster_sample_spacing_m: {lg.cluster_sample_spacing_m}")
        suggest_log(verbose, f"  cluster_sample_radius_m: {lg.cluster_sample_radius_m}")
        suggest_log(verbose, f"  refine_enabled: {lg.refine_enabled}")
        suggest_log(verbose, f"  refine_top_n: {lg.refine_top_n}")
        suggest_log(verbose, f"  refine_radius_m: {lg.refine_radius_m}")
        suggest_log(verbose, f"  refine_spacing_m: {lg.refine_spacing_m}")
        suggest_log(verbose, f"  uncovered_stop_pct: {cfg.uncovered_stop_pct}")
    suggest_log(verbose, f"  suggest_parallelism: {max(1, int(jobs))}")
    suggest_log(verbose, "site suggest: ── seed sites ──")
    for slug, ent in sorted(preset.sites.items()):
        suggest_log(
            verbose,
            f"  {slug}: type={ent.type.value} {_format_loc(ent.lat, ent.lon)}",
        )
    suggest_log(verbose, f"  totals: {by_type}")
    target_label = _coverage_target_label(cfg.coverage_target)
    suggest_log(verbose, "site suggest: ── initial coverage grid ──")
    suggest_log(verbose, f"  grid: {grid.cols}×{grid.rows} px  {target_label} cells: {grid.target_cell_count}")
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
    target_label: str,
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
    suggest_log(verbose, f"  marginal_gain: {best.gain_cells} {target_label} cells toward depth≥{goal}")
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
    max_steps: int | None,
) -> None:
    budget_s = "solve" if max_steps is None else str(max_steps)
    suggest_log(
        verbose,
        f"site suggest: ✓ pick {iteration}/{budget_s}  "
        f"gain={best.gain_cells} cells  uncovered={uncovered_pct:.2f}%  "
        f"{_format_loc(best.lat, best.lon)}  strategy={best.strategy}",
    )
    if verbose:
        suggest_log(verbose, f"  rationale: {best.rationale}")


def _evaluate_footprint_trial(
    *,
    ci: int,
    cand: SiteCandidate,
    iteration: int,
    goal: int,
    grid: CoverageDepthGrid,
    target_label: str,
    footprint,
    workdir: Path,
    error_detail: str | None = None,
) -> _CandidateTrialEval:
    point_gain = grid.point_marginal_gain_cells(cand.lon, cand.lat, goal_depth=goal)

    if error_detail is not None:
        return _CandidateTrialEval(
            trial=CandidateTrial(
                index=ci,
                candidate=cand,
                outcome=CandidateOutcome.VIEWSHED_FAILED,
                point_gain_cells=point_gain,
                footprint_gain_cells=None,
                footprint_area_km2=None,
                workdir=workdir,
                detail=error_detail,
            ),
            pick=None,
        )

    if footprint is None or footprint.is_empty:
        return _CandidateTrialEval(
            trial=CandidateTrial(
                index=ci,
                candidate=cand,
                outcome=CandidateOutcome.EMPTY_FOOTPRINT,
                point_gain_cells=point_gain,
                footprint_gain_cells=None,
                footprint_area_km2=None,
                workdir=workdir,
                detail="viewshed produced no footprint polygon",
            ),
            pick=None,
        )

    gain = grid.marginal_gain_cells(footprint, goal_depth=goal)
    area_km2 = _footprint_area_km2(footprint)
    if gain <= 0:
        return _CandidateTrialEval(
            trial=CandidateTrial(
                index=ci,
                candidate=cand,
                outcome=CandidateOutcome.ZERO_GAIN,
                point_gain_cells=point_gain,
                footprint_gain_cells=gain,
                footprint_area_km2=area_km2,
                workdir=workdir,
                detail=(
                    f"footprint covers {area_km2:.1f} km² but adds 0 new {target_label} cells "
                    f"toward depth≥{goal} (already covered or outside coverage target)"
                ),
            ),
            pick=None,
        )

    rationale = (
        f"Greedy suggest #{iteration}: +{gain} {target_label} cells toward depth≥{goal} "
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
    return _CandidateTrialEval(
        trial=CandidateTrial(
            index=ci,
            candidate=cand,
            outcome=CandidateOutcome.RUNNER_UP,
            point_gain_cells=point_gain,
            footprint_gain_cells=gain,
            footprint_area_km2=area_km2,
            workdir=workdir,
            detail=f"footprint {area_km2:.1f} km²",
            footprint=footprint,
        ),
        pick=pick,
    )


def _trials_from_batch_results(
    *,
    candidates: list[SiteCandidate],
    batch_results: dict,
    iteration: int,
    goal: int,
    grid: CoverageDepthGrid,
    target_label: str,
    start_index: int = 1,
) -> list[_CandidateTrialEval]:
    evals: list[_CandidateTrialEval] = []
    for offset, cand in enumerate(candidates):
        ci = start_index + offset
        key = (int(round(cand.lat * 1e5)), int(round(cand.lon * 1e5)))
        hit = batch_results.get(key)
        if hit is None:
            evals.append(
                _evaluate_footprint_trial(
                    ci=ci,
                    cand=cand,
                    iteration=iteration,
                    goal=goal,
                    grid=grid,
                    target_label=target_label,
                    footprint=None,
                    workdir=Path("."),
                    error_detail="missing batch viewshed result",
                )
            )
            continue
        evals.append(
            _evaluate_footprint_trial(
                ci=ci,
                cand=cand,
                iteration=iteration,
                goal=goal,
                grid=grid,
                target_label=target_label,
                footprint=hit.footprint,
                workdir=hit.workdir,
            )
        )
    return evals


def _select_best_from_evals(
    evals: list[_CandidateTrialEval],
) -> tuple[PlannedSuggestion | None, BaseGeometry | None, list[CandidateTrial]]:
    best: PlannedSuggestion | None = None
    best_footprint: BaseGeometry | None = None
    trials: list[CandidateTrial] = []
    for ev in evals:
        trial = ev.trial
        if ev.pick is not None and trial.footprint is not None:
            if best is None or ev.pick.gain_cells > best.gain_cells:
                if best is not None:
                    for t in trials:
                        if t.outcome == CandidateOutcome.SELECTED:
                            t.outcome = CandidateOutcome.RUNNER_UP
                best = ev.pick
                best_footprint = trial.footprint
                trial.outcome = CandidateOutcome.SELECTED
                trial.detail = (
                    f"current best gain={ev.pick.gain_cells} cells  "
                    f"footprint {trial.footprint_area_km2:.1f} km²"
                )
        trials.append(trial)
    return best, best_footprint, trials


def _run_candidate_trials(
    *,
    candidates: list[SiteCandidate],
    iteration: int,
    goal: int,
    grid: CoverageDepthGrid,
    target_label: str,
    preset: Preset,
    preset_path: Path,
    plan: BuildConfigurePlan,
    refine: StrategyRefineSettings,
    eligible_ll: BaseGeometry,
    footprint_runner,
    jobs: int,
    verbose: bool,
) -> tuple[PlannedSuggestion | None, BaseGeometry | None, list[CandidateTrial]]:
    workers = max(1, int(jobs))

    with suggest_step(verbose, f"coarse batch viewshed ({len(candidates)} candidates, workers={workers})"):
        for ci, cand in enumerate(candidates, start=1):
            point_gain = grid.point_marginal_gain_cells(cand.lon, cand.lat, goal_depth=goal)
            suggest_log(
                verbose,
                f"site suggest:     trial [{ci}/{len(candidates)}] {_format_candidate_brief(cand)} "
                f"point_pre_gain={point_gain} cells",
            )
        try:
            coarse_batch = run_candidate_batch_viewsheds(
                preset=preset,
                preset_path=preset_path,
                viewshed_root=plan.viewsheds_root,
                candidates=candidates,
                verbose=verbose,
                jobs=workers,
                footprint_runner=footprint_runner,
            )
        except Exception as exc:
            print(f"site suggest: batch viewshed failed: {exc}", flush=True)
            raise

    coarse_evals = _trials_from_batch_results(
        candidates=candidates,
        batch_results=coarse_batch,
        iteration=iteration,
        goal=goal,
        grid=grid,
        target_label=target_label,
    )

    refine_candidates: list[SiteCandidate] = []
    if refine.refine_enabled and int(refine.refine_top_n) > 0:
        ranked = sorted(
            (ev for ev in coarse_evals if ev.pick is not None and ev.trial.footprint_gain_cells),
            key=lambda ev: int(ev.trial.footprint_gain_cells or 0),
            reverse=True,
        )
        refine_centers = [ev.pick for ev in ranked[: int(refine.refine_top_n)] if ev.pick is not None]
        if refine_centers:
            refine_candidates = generate_refine_candidates(
                centers=[
                    SiteCandidate(
                        lat=p.lat,
                        lon=p.lon,
                        elev_m=p.elev_m,
                        strategy="refine_seed",
                    )
                    for p in refine_centers
                ],
                eligible_ll=eligible_ll,
                refine_radius_m=float(refine.refine_radius_m),
                refine_spacing_m=float(refine.refine_spacing_m),
            )
            coarse_keys = {
                (int(round(c.lat * 1e5)), int(round(c.lon * 1e5))) for c in candidates
            }
            refine_candidates = [
                c
                for c in refine_candidates
                if (int(round(c.lat * 1e5)), int(round(c.lon * 1e5))) not in coarse_keys
            ]

    refine_evals: list[_CandidateTrialEval] = []
    if refine_candidates:
        with suggest_step(
            verbose,
            f"refine batch viewshed ({len(refine_candidates)} samples around top {refine.refine_top_n})",
        ):
            refine_batch = run_candidate_batch_viewsheds(
                preset=preset,
                preset_path=preset_path,
                viewshed_root=plan.viewsheds_root,
                candidates=refine_candidates,
                verbose=verbose,
                jobs=workers,
                footprint_runner=footprint_runner,
            )
        refine_evals = _trials_from_batch_results(
            candidates=refine_candidates,
            batch_results=refine_batch,
            iteration=iteration,
            goal=goal,
            grid=grid,
            target_label=target_label,
            start_index=len(candidates) + 1,
        )

    return _select_best_from_evals([*coarse_evals, *refine_evals])


def plan_greedy_site_suggestions(
    *,
    preset: Preset,
    preset_path: Path,
    plan: BuildConfigurePlan,
    suggest_cli_n: int,
    suggest_root: Path,
    footprint_runner=None,
    verbose: bool = False,
    jobs: int = 1,
) -> list[PlannedSuggestion]:
    """Run the site-suggestion solver for ``suggest_cli_n`` steps or until ``planning_complete``."""
    if preset.bundle is None:
        raise ValueError("site suggestions require preset bundle.*")

    cfg = resolved_site_suggestions_config(preset.bundle)
    provider = resolve_site_suggestion_strategy(cfg)
    if cfg.strategy == SiteSuggestionStrategy.MESH_BACKBONE and cfg.mesh_backbone.links:
        validate_mesh_backbone_site_slugs(preset.sites, cfg.mesh_backbone)
    max_steps = provider.resolve_step_budget(cfg, suggest_cli_n)
    if max_steps is not None and max_steps <= 0:
        return []

    goal = provider.goal_depth(cfg)
    refine = provider.refine_settings(cfg)
    target_label = _coverage_target_label(cfg.coverage_target)

    suggest_log(verbose, "site suggest: ── planner setup ──")
    with suggest_step(verbose, "load AOI and eligible geometries"):
        aoi_gpkg = _composite_by_role(plan, "aoi").union_gpkg
        elig_gpkg = _composite_by_role(plan, "eligible").union_gpkg
        aoi_ll = _read_union_geometry(aoi_gpkg, "aoi")
        eligible_ll = _read_union_geometry(elig_gpkg, ELIGIBLE_LAYER)
        target_ll = _coverage_target_geometry(
            cfg.coverage_target,
            aoi_ll=aoi_ll,
            eligible_ll=eligible_ll,
        )

    seed_paths = _footprint_paths_for_seed_sites(plan, preset)
    with suggest_step(verbose, f"build initial coverage grid ({len(seed_paths)} seed footprint(s))"):
        grid = build_coverage_depth_grid(
            aoi_ll=aoi_ll,
            target_ll=target_ll,
            footprint_gpkg_paths=seed_paths,
            max_raster_dimension=int(cfg.planner_raster_dimension),
            verbose=verbose,
            target_label=target_label,
        )

    _log_planner_config(
        verbose=verbose,
        cfg=cfg,
        strategy_name=provider.name,
        goal=goal,
        grid=grid,
        seed_paths=seed_paths,
        preset=preset,
        jobs=jobs,
    )

    eligible_sha = _composite_by_role(plan, "eligible").sha
    dem_mirror = plan.splat_tiles_root
    suggest_ctx = SiteSuggestionContext(
        preset=preset,
        plan=plan,
        grid=grid,
        eligible_ll=eligible_ll,
        aoi_ll=aoi_ll,
        target_ll=target_ll,
        suggest_root=suggest_root,
        cfg=cfg,
        dem_mirror_root=dem_mirror,
        eligible_sha=eligible_sha,
        jobs=jobs,
        verbose=verbose,
    )

    if provider.planning_complete(suggest_ctx):
        msg = f"site suggest: {provider.name} goal already met — no picks needed"
        print(msg, flush=True)
        suggest_log(verbose, msg)
        return []

    winners: list[PlannedSuggestion] = []
    steps_done = 0
    budget_label = "solve" if max_steps is None else str(max_steps)

    while True:
        if provider.planning_complete(suggest_ctx):
            if steps_done > 0:
                print(f"site suggest: stopping — {provider.name} goal met after {steps_done} pick(s)", flush=True)
            break
        if max_steps is not None and steps_done >= max_steps:
            break

        steps_done += 1
        iteration = steps_done
        uncovered_before = grid.uncovered_fraction(goal_depth=goal)

        suggest_log(
            verbose,
            f"site suggest: ══ iteration {iteration}/{budget_label} "
            f"(uncovered {100.0 * uncovered_before:.2f}% below depth≥{goal}) ══",
        )

        candidates = provider.generate_candidates(suggest_ctx, iteration=iteration - 1)
        _log_candidate_shortlist(verbose=verbose, iteration=iteration, candidates=candidates)

        if not candidates:
            print(f"site suggest: no candidates at iteration {iteration}", flush=True)
            break

        best, best_footprint, trials = _run_candidate_trials(
            candidates=candidates,
            iteration=iteration,
            goal=goal,
            grid=grid,
            target_label=target_label,
            preset=preset,
            preset_path=preset_path,
            plan=plan,
            refine=refine,
            eligible_ll=eligible_ll,
            footprint_runner=footprint_runner,
            jobs=jobs,
            verbose=verbose,
        )

        if best is None or best_footprint is None:
            print(f"site suggest: no improving candidate at iteration {iteration}", flush=True)
            _log_trial_results(
                verbose=verbose,
                iteration=iteration,
                goal=goal,
                target_label=target_label,
                trials=trials,
                best=None,
            )
            break

        _log_trial_results(
            verbose=verbose,
            iteration=iteration,
            goal=goal,
            target_label=target_label,
            trials=trials,
            best=best,
        )

        grid.add_footprint(best_footprint)
        session_slug = f"_session_{iteration:04d}"
        suggest_ctx.session_sites.append(
            BackboneSite(slug=session_slug, lat=best.lat, lon=best.lon)
        )
        suggest_ctx.session_footprints[session_slug] = best_footprint
        winners.append(best)
        uncovered_after = 100.0 * grid.uncovered_fraction(goal_depth=goal)
        print(
            f"site suggest: pick {iteration}/{budget_label} "
            f"gain={best.gain_cells} cells uncovered={uncovered_after:.2f}% "
            f"({best.lat:.5f}, {best.lon:.5f})",
            flush=True,
        )
        _log_selection_summary(
            verbose=verbose,
            iteration=iteration,
            best=best,
            uncovered_pct=uncovered_after,
            max_steps=max_steps,
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
    n_requested: int | None,
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
        "n_requested": n_requested,
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
