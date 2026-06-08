"""Greedy AOI coverage site planner."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import geopandas as gpd
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

from peaky_finders.build_configure import BuildConfigurePlan
from peaky_finders.bundle_clips import ELIGIBLE_LAYER
from peaky_finders.site_suggestions.batch_viewshed import run_candidate_batch_viewsheds
from peaky_finders.site_suggestions.candidates import SiteCandidate, generate_refine_candidates
from peaky_finders.site_suggestions.refine_peaks import generate_mesh_refine_candidates
from peaky_finders.site_suggestions.context import BackboneSite, SiteSuggestionContext
from peaky_finders.site_suggestions.corridor import corridor_stall_recovery
from peaky_finders.site_suggestions.corridor_scoring import (
    CorridorTrialScore,
    build_corridor_score_context,
    corridor_sort_key,
    corridor_trial_has_progress,
    score_corridor_trials,
)
from peaky_finders.site_suggestions.depth_grid import CoverageDepthGrid, build_coverage_depth_grid
from peaky_finders.site_suggestions.log import suggest_log, suggest_progress, suggest_step, SuggestProgressTicker
from peaky_finders.site_suggestions.providers.protocol import (
    CandidateTrialMode,
    StrategyPlannerHooks,
    StrategyRefineSettings,
)
from peaky_finders.site_suggestions.providers.registry import resolve_site_suggestion_strategy
from peaky_finders.site_suggestions.providers.mesh_backbone.completion import (
    all_backbone_sites,
    site_location_key,
)
from peaky_finders.site_suggestions.providers.mesh_backbone.scoring import (
    MeshGrowTrialScore,
    build_mesh_grow_score_context,
    mesh_grow_sort_key,
    mesh_grow_trial_has_progress,
    score_mesh_grow_trials,
    summarize_mesh_grow_outcomes,
)
from peaky_finders.site_suggestions.providers.mesh_backbone.goals import (
    captured_tracked_goals,
    goals_satisfied_since,
    tracked_goal_keys,
)
from peaky_finders.site_suggestions.preset_io import count_suggested_sites, next_suggest_iteration
from peaky_finders.sites_job import (
    Preset,
    SiteSuggestionCoverageTarget,
    load_preset,
    resolved_suggest_config,
)


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
    NO_HOP = "no_hop"
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
    phase: str = "coarse"


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
        p = ws.splat_gpkg.expanduser().resolve()
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
    max_goals: int | None,
) -> None:
    budget_s = "solve" if max_goals is None else f"{max_goals} goal(s)"
    suggest_log(
        verbose,
        f"site suggest: ✓ site #{iteration} ({budget_s})  "
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
    retain_footprint: bool = False,
    phase: str = "coarse",
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
                phase=phase,
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
                phase=phase,
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
                footprint=footprint if retain_footprint else None,
                phase=phase,
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
            phase=phase,
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
    retain_footprint: bool = False,
    phase: str = "coarse",
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
                    phase=phase,
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
                retain_footprint=retain_footprint,
                phase=phase,
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


def _select_mesh_grow_from_evals(
    *,
    evals: list[_CandidateTrialEval],
    ctx: SiteSuggestionContext,
    iteration: int,
    target_label: str,
    verbose: bool = False,
    jobs: int = 1,
    score_cache: dict[int, MeshGrowTrialScore | None] | None = None,
) -> tuple[PlannedSuggestion | None, BaseGeometry | None, list[CandidateTrial]]:
    score_ctx = build_mesh_grow_score_context(ctx)
    if score_ctx is None:
        return None, None, [ev.trial for ev in evals]

    suggest_log(verbose, f"site suggest: ── iteration {iteration} mesh-grow scoring ──")
    suggest_log(
        verbose,
        f"  uncaptured goals: {len(score_ctx.uncaptured_goals)}  "
        f"(pick best hop-valid Δdist across all)",
    )

    best_score = None
    best_eval: _CandidateTrialEval | None = None
    trials: list[CandidateTrial] = []
    scorable = 0
    scored = 0
    cache = score_cache if score_cache is not None else {}

    pending_ev: list[_CandidateTrialEval] = []
    for ev in evals:
        trial = ev.trial
        if trial.footprint is None or trial.outcome == CandidateOutcome.VIEWSHED_FAILED:
            continue
        scorable += 1
        if trial.index not in cache:
            pending_ev.append(ev)

    if pending_ev:
        workers = max(1, int(jobs))
        batch_scores = score_mesh_grow_trials(
            score_ctx=score_ctx,
            trials=[
                (ev.trial.candidate.lat, ev.trial.candidate.lon, ev.trial.footprint)
                for ev in pending_ev
            ],
            jobs=workers,
        )
        for ev, score in zip(pending_ev, batch_scores, strict=True):
            cache[ev.trial.index] = score

    with suggest_step(
        verbose,
        f"mesh-grow score {len(evals)} trial(s) across {len(score_ctx.uncaptured_goals)} goal(s)",
    ):
        ticker = SuggestProgressTicker(verbose, label="mesh-grow score", interval_s=0.5)
        for ev in evals:
            trial = ev.trial
            if trial.footprint is None or trial.outcome == CandidateOutcome.VIEWSHED_FAILED:
                trials.append(trial)
                continue

            scored += 1
            ticker.maybe(
                f"[{scored}/{scorable}] {_format_candidate_brief(trial.candidate)}",
            )

            score = cache[trial.index] if trial.index in cache else None

            if score is None:
                trial.outcome = CandidateOutcome.NO_HOP
                trial.detail = "no confirmed mutual hop to an existing site"
                trials.append(trial)
                continue

            if not mesh_grow_trial_has_progress(score):
                trial.outcome = CandidateOutcome.ZERO_GAIN
                trial.detail = (
                    f"hop via {','.join(score.hop_neighbors)} but no progress toward any "
                    f"uncaptured goal (best Δdist={score.best_delta_m:.0f} m)"
                )
                trials.append(trial)
                continue

            sort_key = mesh_grow_sort_key(score)
            goal_label = score.best_goal_key or "?"
            capture_label = ",".join(score.captured_goal_keys) or "none"
            trial.outcome = CandidateOutcome.RUNNER_UP
            trial.detail = (
                f"best={goal_label} Δdist={score.best_delta_m:.0f} m  "
                f"total Δdist={score.total_delta_m:.0f} m  "
                f"capture={capture_label}  hop={','.join(score.hop_neighbors)}  "
                f"area={trial.footprint_area_km2:.1f} km²"
            )
            pick = PlannedSuggestion(
                lat=trial.candidate.lat,
                lon=trial.candidate.lon,
                elev_m=trial.candidate.elev_m,
                gain_cells=max(0, int(round(score.best_delta_m))),
                strategy=trial.candidate.strategy,
                iteration=iteration,
                rationale=(
                    f"Mesh-grow #{iteration}: Δ{score.best_delta_m / 1000.0:.1f} km toward "
                    f"{goal_label} ({target_label}, capture={capture_label})"
                ),
            )
            ev_scored = _CandidateTrialEval(trial=trial, pick=pick)
            if best_score is None or sort_key > best_score:
                if best_eval is not None:
                    best_eval.trial.outcome = CandidateOutcome.RUNNER_UP
                best_score = sort_key
                best_eval = ev_scored
                trial.outcome = CandidateOutcome.SELECTED
            trials.append(trial)

    summary = summarize_mesh_grow_outcomes(t.outcome.value for t in trials)
    winner = "yes" if best_eval is not None and best_eval.pick is not None else "no"
    print(
        f"site suggest: mesh-grow scoring done: {len(evals)} trial(s), "
        f"scorable={scorable}, winner={winner}, {summary}",
        flush=True,
    )

    if best_eval is None or best_eval.pick is None:
        return None, None, trials
    return best_eval.pick, best_eval.trial.footprint, trials


def _select_corridor_from_evals(
    *,
    evals: list[_CandidateTrialEval],
    ctx: SiteSuggestionContext,
    iteration: int,
    target_label: str,
    verbose: bool = False,
    jobs: int = 1,
    score_cache: dict[int, CorridorTrialScore | None] | None = None,
) -> tuple[PlannedSuggestion | None, BaseGeometry | None, list[CandidateTrial]]:
    score_ctx = build_corridor_score_context(ctx)
    if score_ctx is None:
        return None, None, [ev.trial for ev in evals]

    goal_key = score_ctx.goal.key
    suggest_log(verbose, f"site suggest: ── iteration {iteration} corridor scoring ──")
    suggest_log(
        verbose,
        f"  goal={goal_key}  corridor variant={score_ctx.corridor.variant}  "
        f"s_covered={score_ctx.s_before_m / 1000.0:.1f}/{score_ctx.corridor.length_m / 1000.0:.1f} km",
    )

    best_score = None
    best_eval: _CandidateTrialEval | None = None
    trials: list[CandidateTrial] = []
    scorable = 0
    scored = 0
    cache = score_cache if score_cache is not None else {}

    pending_ev: list[_CandidateTrialEval] = []
    for ev in evals:
        trial = ev.trial
        if trial.footprint is None or trial.outcome == CandidateOutcome.VIEWSHED_FAILED:
            continue
        scorable += 1
        if trial.index not in cache:
            pending_ev.append(ev)

    if pending_ev:
        workers = max(1, int(jobs))
        batch_scores = score_corridor_trials(
            score_ctx=score_ctx,
            trials=[
                (ev.trial.candidate.lat, ev.trial.candidate.lon, ev.trial.footprint)
                for ev in pending_ev
            ],
            jobs=workers,
            verbose=verbose,
        )
        for ev, score in zip(pending_ev, batch_scores, strict=True):
            cache[ev.trial.index] = score

    with suggest_step(
        verbose,
        f"corridor score {len(evals)} trial(s) toward {goal_key}",
    ):
        ticker = SuggestProgressTicker(verbose, label="corridor score", interval_s=0.5)
        for ev in evals:
            trial = ev.trial
            if trial.footprint is None or trial.outcome == CandidateOutcome.VIEWSHED_FAILED:
                trials.append(trial)
                continue

            scored += 1
            ticker.maybe(
                f"[{scored}/{scorable}] {_format_candidate_brief(trial.candidate)}",
            )

            score = cache.get(trial.index)
            if score is None:
                trial.outcome = CandidateOutcome.NO_HOP
                trial.detail = "no confirmed mutual hop to an existing site"
                trials.append(trial)
                continue

            if not corridor_trial_has_progress(score):
                trial.outcome = CandidateOutcome.ZERO_GAIN
                trial.detail = (
                    f"hop via {','.join(score.hop_neighbors)} but no corridor progress "
                    f"(Δs={score.delta_s_m:.0f} m, Δdist={score.delta_dist_to_goal_m:.0f} m)"
                )
                trials.append(trial)
                continue

            capture_label = goal_key if score.captured else "none"
            trial.outcome = CandidateOutcome.RUNNER_UP
            trial.detail = (
                f"Δs={score.delta_s_m:.0f} m  s={score.s_after_m / 1000.0:.1f} km  "
                f"Δdist={score.delta_dist_to_goal_m:.0f} m  capture={capture_label}  "
                f"hop={','.join(score.hop_neighbors)}  area={trial.footprint_area_km2:.1f} km²"
            )
            sort_key = corridor_sort_key(score)
            pick = PlannedSuggestion(
                lat=trial.candidate.lat,
                lon=trial.candidate.lon,
                elev_m=trial.candidate.elev_m,
                gain_cells=max(0, int(round(score.delta_s_m))),
                strategy=trial.candidate.strategy,
                iteration=iteration,
                rationale=(
                    f"Corridor #{iteration}: +{score.delta_s_m / 1000.0:.1f} km along path toward "
                    f"{goal_key} ({target_label}, capture={capture_label})"
                ),
            )
            ev_scored = _CandidateTrialEval(trial=trial, pick=pick)
            if best_score is None or sort_key > best_score:
                if best_eval is not None:
                    best_eval.trial.outcome = CandidateOutcome.RUNNER_UP
                best_score = sort_key
                best_eval = ev_scored
                trial.outcome = CandidateOutcome.SELECTED
            trials.append(trial)

    summary = summarize_mesh_grow_outcomes(t.outcome.value for t in trials)
    winner = "yes" if best_eval is not None and best_eval.pick is not None else "no"
    print(
        f"site suggest: corridor scoring done: {len(evals)} trial(s), "
        f"scorable={scorable}, winner={winner}, {summary}",
        flush=True,
    )

    if best_eval is None or best_eval.pick is None:
        return None, None, trials
    return best_eval.pick, best_eval.trial.footprint, trials


def _candidate_loc_key(c: SiteCandidate) -> tuple[int, int]:
    return (int(round(c.lat * 1e5)), int(round(c.lon * 1e5)))


def _corridor_viewshed_hook(
    ctx: SiteSuggestionContext | None,
    *,
    corridor_grow: bool,
    iteration: int,
    phase: str,
):
    if not corridor_grow or ctx is None:
        return None
    state = ctx.corridor_state
    if state is None or state.active_goal_key is None:
        return None
    goal_key = str(state.active_goal_key)

    def on_ready(
        trial_index: int,
        cand: SiteCandidate,
        workdir: Path,
        footprint: BaseGeometry | None,
    ) -> None:
        from peaky_finders.site_suggestions.corridor_kml import record_corridor_trial_viewshed

        has_fp = footprint is not None and not footprint.is_empty
        record_corridor_trial_viewshed(
            ctx,
            goal_key=goal_key,
            iteration=iteration,
            phase=phase,
            index=trial_index,
            lat=float(cand.lat),
            lon=float(cand.lon),
            elev_m=cand.elev_m,
            strategy=str(cand.strategy),
            workdir=workdir,
            has_footprint=has_fp,
        )

    return on_ready


def _record_corridor_iteration_kml(
    *,
    ctx: SiteSuggestionContext,
    iteration: int,
    trials: list[CandidateTrial],
    best: PlannedSuggestion | None,
) -> None:
    state = ctx.corridor_state
    if state is None or state.active_goal_key is None:
        return
    from peaky_finders.site_suggestions.corridor_kml import (
        record_corridor_pick,
        record_corridor_trials,
    )

    goal_key = str(state.active_goal_key)
    record_corridor_trials(ctx, goal_key=goal_key, iteration=iteration, trials=trials)
    if best is not None:
        selected = next((t for t in trials if t.outcome == CandidateOutcome.SELECTED), None)
        record_corridor_pick(
            ctx,
            goal_key=goal_key,
            iteration=best.iteration,
            lat=best.lat,
            lon=best.lon,
            elev_m=best.elev_m,
            strategy=best.strategy,
            workdir=selected.workdir if selected is not None else None,
        )


def _record_corridor_trials_kml(
    *,
    ctx: SiteSuggestionContext | None,
    corridor_grow: bool,
    iteration: int,
    trials: list[CandidateTrial],
) -> None:
    if not corridor_grow or ctx is None:
        return
    state = ctx.corridor_state
    if state is None or state.active_goal_key is None:
        return
    from peaky_finders.site_suggestions.corridor_kml import record_corridor_trials

    goal_key = str(state.active_goal_key)
    record_corridor_trials(ctx, goal_key=goal_key, iteration=iteration, trials=trials)


def _mesh_grow_refine_seeds_from_coarse_evals(
    *,
    coarse_evals: list[_CandidateTrialEval],
    ctx: SiteSuggestionContext,
    refine_top_n: int,
    jobs: int,
    verbose: bool,
    score_cache: dict[int, MeshGrowTrialScore | None],
) -> list[SiteCandidate]:
    """Pick refine centers from coarse trials ranked by mesh-grow score."""
    top_n = max(0, int(refine_top_n))
    if top_n <= 0:
        return []

    score_ctx = build_mesh_grow_score_context(ctx)
    if score_ctx is None:
        return []

    pending_ev: list[_CandidateTrialEval] = []
    for ev in coarse_evals:
        trial = ev.trial
        if trial.footprint is None or trial.outcome == CandidateOutcome.VIEWSHED_FAILED:
            continue
        if trial.index not in score_cache:
            pending_ev.append(ev)

    if pending_ev:
        workers = max(1, int(jobs))
        batch_scores = score_mesh_grow_trials(
            score_ctx=score_ctx,
            trials=[
                (ev.trial.candidate.lat, ev.trial.candidate.lon, ev.trial.footprint)
                for ev in pending_ev
            ],
            jobs=workers,
        )
        for ev, score in zip(pending_ev, batch_scores, strict=True):
            score_cache[ev.trial.index] = score

    ranked: list[tuple[tuple, _CandidateTrialEval, MeshGrowTrialScore]] = []
    for ev in coarse_evals:
        trial = ev.trial
        if trial.footprint is None or trial.outcome == CandidateOutcome.VIEWSHED_FAILED:
            continue
        score = score_cache.get(trial.index)
        if score is None or not mesh_grow_trial_has_progress(score):
            continue
        ranked.append((mesh_grow_sort_key(score), ev, score))

    ranked.sort(key=lambda row: row[0], reverse=True)

    centers: list[SiteCandidate] = []
    seen: set[tuple[int, int]] = set()
    for sort_key, ev, score in ranked:
        cand = ev.trial.candidate
        key = _candidate_loc_key(cand)
        if key in seen:
            continue
        seen.add(key)
        goal_label = score.best_goal_key or "?"
        capture_label = ",".join(score.captured_goal_keys) or "none"
        suggest_log(
            verbose,
            f"site suggest:     refine seed {_format_candidate_brief(cand)} "
            f"mesh_score captures={len(score.captured_goal_keys)} "
            f"best={goal_label} Δdist={score.best_delta_m:.0f} m capture={capture_label} "
            f"sort={sort_key}",
        )
        centers.append(
            SiteCandidate(
                lat=cand.lat,
                lon=cand.lon,
                elev_m=cand.elev_m,
                strategy="refine_seed",
            )
        )
        if len(centers) >= top_n:
            break

    if centers:
        suggest_log(verbose, f"site suggest:     refine seeds from coarse mesh score: {len(centers)}")
    else:
        suggest_log(verbose, "site suggest:     refine seeds: no scorable coarse mesh-grow trials")
    return centers


def _corridor_refine_seeds_from_coarse_evals(
    *,
    coarse_evals: list[_CandidateTrialEval],
    ctx: SiteSuggestionContext,
    refine_top_n: int,
    verbose: bool,
    jobs: int,
    score_cache: dict[int, CorridorTrialScore | None],
) -> list[SiteCandidate]:
    top_n = max(0, int(refine_top_n))
    if top_n <= 0:
        return []

    score_ctx = build_corridor_score_context(ctx)
    if score_ctx is None:
        return []

    pending_ev: list[_CandidateTrialEval] = []
    for ev in coarse_evals:
        trial = ev.trial
        if trial.footprint is None or trial.outcome == CandidateOutcome.VIEWSHED_FAILED:
            continue
        if trial.index not in score_cache:
            pending_ev.append(ev)

    if pending_ev:
        batch_scores = score_corridor_trials(
            score_ctx=score_ctx,
            trials=[
                (ev.trial.candidate.lat, ev.trial.candidate.lon, ev.trial.footprint)
                for ev in pending_ev
            ],
            jobs=max(1, int(jobs)),
            verbose=verbose,
        )
        for ev, score in zip(pending_ev, batch_scores, strict=True):
            score_cache[ev.trial.index] = score

    ranked: list[tuple[tuple, _CandidateTrialEval, CorridorTrialScore]] = []
    for ev in coarse_evals:
        trial = ev.trial
        if trial.footprint is None or trial.outcome == CandidateOutcome.VIEWSHED_FAILED:
            continue
        score = score_cache.get(trial.index)
        if score is None or not corridor_trial_has_progress(score):
            continue
        ranked.append((corridor_sort_key(score), ev, score))

    ranked.sort(key=lambda row: row[0], reverse=True)

    centers: list[SiteCandidate] = []
    seen: set[tuple[int, int]] = set()
    for sort_key, ev, score in ranked:
        cand = ev.trial.candidate
        key = _candidate_loc_key(cand)
        if key in seen:
            continue
        seen.add(key)
        capture_label = score_ctx.goal.key if score.captured else "none"
        suggest_log(
            verbose,
            f"site suggest:     refine seed {_format_candidate_brief(cand)} "
            f"corridor_score Δs={score.delta_s_m:.0f} m capture={capture_label} sort={sort_key}",
        )
        centers.append(
            SiteCandidate(
                lat=cand.lat,
                lon=cand.lon,
                elev_m=cand.elev_m,
                strategy="refine_seed",
            )
        )
        if len(centers) >= top_n:
            break

    if centers:
        suggest_log(verbose, f"site suggest:     refine seeds from coarse corridor score: {len(centers)}")
    else:
        suggest_log(verbose, "site suggest:     refine seeds: no scorable coarse corridor trials")
    return centers


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
    suggest_ctx: SiteSuggestionContext | None = None,
    hooks: StrategyPlannerHooks | None = None,
) -> tuple[PlannedSuggestion | None, BaseGeometry | None, list[CandidateTrial]]:
    hooks = hooks or StrategyPlannerHooks(
        trial_mode=CandidateTrialMode.COVERAGE,
        retain_trial_footprints=False,
        corridor_kml=False,
    )
    mesh_grow = hooks.trial_mode != CandidateTrialMode.COVERAGE
    corridor_grow = hooks.trial_mode == CandidateTrialMode.CORRIDOR_GROW
    workers = max(1, int(jobs))
    mesh_score_cache: dict[int, MeshGrowTrialScore | None] = {}
    corridor_score_cache: dict[int, CorridorTrialScore | None] = {}

    refine_candidates: list[SiteCandidate] = []

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
                extra_candidates=None,
                verbose=verbose,
                jobs=workers,
                footprint_runner=footprint_runner,
                trial_index_start=1,
                on_viewshed_ready=_corridor_viewshed_hook(
                    suggest_ctx,
                    corridor_grow=corridor_grow,
                    iteration=iteration,
                    phase="coarse",
                ),
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
        retain_footprint=mesh_grow,
    )
    if corridor_grow:
        _record_corridor_trials_kml(
            ctx=suggest_ctx,
            corridor_grow=corridor_grow,
            iteration=iteration,
            trials=[ev.trial for ev in coarse_evals],
        )

    if refine.refine_enabled and int(refine.refine_top_n) > 0 and corridor_grow and suggest_ctx is not None:
        with suggest_step(
            verbose,
            f"pick refine seeds from coarse corridor score (top {refine.refine_top_n})",
        ):
            refine_centers = _corridor_refine_seeds_from_coarse_evals(
                coarse_evals=coarse_evals,
                ctx=suggest_ctx,
                refine_top_n=int(refine.refine_top_n),
                verbose=verbose,
                jobs=workers,
                score_cache=corridor_score_cache,
            )
        if refine_centers:
            refine_candidates = generate_mesh_refine_candidates(
                centers=refine_centers,
                eligible_ll=eligible_ll,
                refine=refine,
                ctx=suggest_ctx,
                verbose=verbose,
            )
            coarse_keys = {_candidate_loc_key(c) for c in candidates}
            refine_candidates = [
                c for c in refine_candidates if _candidate_loc_key(c) not in coarse_keys
            ]
    elif refine.refine_enabled and int(refine.refine_top_n) > 0 and mesh_grow and suggest_ctx is not None:
        with suggest_step(
            verbose,
            f"pick refine seeds from coarse mesh score (top {refine.refine_top_n})",
        ):
            refine_centers = _mesh_grow_refine_seeds_from_coarse_evals(
                coarse_evals=coarse_evals,
                ctx=suggest_ctx,
                refine_top_n=int(refine.refine_top_n),
                jobs=workers,
                verbose=verbose,
                score_cache=mesh_score_cache,
            )
        if refine_centers:
            refine_candidates = generate_mesh_refine_candidates(
                centers=refine_centers,
                eligible_ll=eligible_ll,
                refine=refine,
                ctx=suggest_ctx,
                verbose=verbose,
            )
            coarse_keys = {_candidate_loc_key(c) for c in candidates}
            refine_candidates = [
                c for c in refine_candidates if _candidate_loc_key(c) not in coarse_keys
            ]
    elif refine.refine_enabled and int(refine.refine_top_n) > 0 and not mesh_grow:
        ranked = sorted(
            (ev for ev in coarse_evals if ev.pick is not None and ev.trial.footprint_gain_cells),
            key=lambda ev: int(ev.trial.footprint_gain_cells or 0),
            reverse=True,
        )
        refine_centers = [
            SiteCandidate(
                lat=ev.pick.lat,
                lon=ev.pick.lon,
                elev_m=ev.pick.elev_m,
                strategy="refine_seed",
            )
            for ev in ranked[: int(refine.refine_top_n)]
            if ev.pick is not None
        ]
        if refine_centers:
            refine_candidates = generate_refine_candidates(
                centers=refine_centers,
                eligible_ll=eligible_ll,
                refine_radius_m=float(refine.refine_radius_m),
                refine_spacing_m=float(refine.refine_spacing_m),
            )
            coarse_keys = {_candidate_loc_key(c) for c in candidates}
            refine_candidates = [
                c for c in refine_candidates if _candidate_loc_key(c) not in coarse_keys
            ]

    refine_evals: list[_CandidateTrialEval] = []
    if refine_candidates and (mesh_grow or corridor_grow):
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
                trial_index_start=len(candidates) + 1,
                on_viewshed_ready=_corridor_viewshed_hook(
                    suggest_ctx,
                    corridor_grow=corridor_grow,
                    iteration=iteration,
                    phase="refine",
                ),
            )
        refine_evals = _trials_from_batch_results(
            candidates=refine_candidates,
            batch_results=refine_batch,
            iteration=iteration,
            goal=goal,
            grid=grid,
            target_label=target_label,
            start_index=len(candidates) + 1,
            retain_footprint=True,
            phase="refine",
        )
    elif refine_candidates and not mesh_grow:
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
                trial_index_start=len(candidates) + 1,
                on_viewshed_ready=_corridor_viewshed_hook(
                    suggest_ctx,
                    corridor_grow=corridor_grow,
                    iteration=iteration,
                    phase="refine",
                ),
            )
        refine_evals = _trials_from_batch_results(
            candidates=refine_candidates,
            batch_results=refine_batch,
            iteration=iteration,
            goal=goal,
            grid=grid,
            target_label=target_label,
            start_index=len(candidates) + 1,
            retain_footprint=mesh_grow,
            phase="refine",
        )

    if corridor_grow and refine_evals:
        _record_corridor_trials_kml(
            ctx=suggest_ctx,
            corridor_grow=corridor_grow,
            iteration=iteration,
            trials=[ev.trial for ev in refine_evals],
        )

    all_evals = [*coarse_evals, *refine_evals]
    if corridor_grow and suggest_ctx is not None:
        return _select_corridor_from_evals(
            evals=all_evals,
            ctx=suggest_ctx,
            iteration=iteration,
            target_label=target_label,
            verbose=verbose,
            jobs=workers,
            score_cache=corridor_score_cache,
        )
    if mesh_grow and suggest_ctx is not None:
        return _select_mesh_grow_from_evals(
            evals=all_evals,
            ctx=suggest_ctx,
            iteration=iteration,
            target_label=target_label,
            verbose=verbose,
            jobs=workers,
            score_cache=mesh_score_cache,
        )
    return _select_best_from_evals(all_evals)


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
    on_pick: Callable[[PlannedSuggestion], str] | None = None,
) -> list[PlannedSuggestion]:
    """Run the site-suggestion solver until ``planning_complete`` or ``suggest_cli_n`` goal(s) are satisfied."""
    if preset.land is None:
        raise ValueError("site suggestions require preset land.*")

    cfg = resolved_suggest_config(preset.suggest)
    provider = resolve_site_suggestion_strategy(cfg)
    hooks = provider.planner_hooks(cfg)
    provider.validate_config(cfg)
    existing_suggested = count_suggested_sites(preset)
    max_goals = provider.resolve_step_budget(cfg, suggest_cli_n)

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
            jobs=jobs,
        )

    provider.log_planner_config(
        verbose=verbose,
        cfg=cfg,
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
    provider.prepare_suggest_context(suggest_ctx, suggest_root=suggest_root)

    if provider.planning_complete(suggest_ctx):
        msg = f"site suggest: {provider.name} goal already met — no picks needed"
        print(msg, flush=True)
        suggest_log(verbose, msg)
        return []

    winners: list[PlannedSuggestion] = []
    attempt = 0
    budget_label = "solve" if max_goals is None else f"{max_goals} goal(s)"
    first_iteration = next_suggest_iteration(preset)
    goals_at_start = captured_tracked_goals(
        suggest_ctx,
        planning_complete=provider.planning_complete(suggest_ctx),
    )
    tracked_keys_at_start = tracked_goal_keys(suggest_ctx)

    while True:
        planning_done = provider.planning_complete(suggest_ctx)
        goals_done = goals_satisfied_since(
            suggest_ctx,
            planning_complete=planning_done,
            baseline=goals_at_start,
            tracked_keys=tracked_keys_at_start,
        )
        if planning_done:
            if winners:
                max_nodes = provider.max_suggested_nodes(cfg)
                if (
                    max_nodes is not None
                    and len(all_backbone_sites(suggest_ctx)) >= int(max_nodes)
                ):
                    print(
                        f"site suggest: stopping — {provider.name} max_nodes={max_nodes} "
                        f"after {existing_suggested + len(winners)} suggested site(s)",
                        flush=True,
                    )
                else:
                    print(
                        f"site suggest: stopping — {provider.name} goal met after "
                        f"{existing_suggested + len(winners)} suggested site(s)",
                        flush=True,
                    )
            break
        if max_goals is not None and len(goals_done) >= max_goals:
            names = ", ".join(sorted(goals_done)) or "?"
            print(
                f"site suggest: stopping — {len(goals_done)} goal(s) satisfied "
                f"(budget {max_goals}: {names})",
                flush=True,
            )
            break

        attempt += 1
        site_n = first_iteration + len(winners)
        new_written = len(winners)
        uncovered_before = grid.uncovered_fraction(goal_depth=goal)
        if max_goals is None:
            goal_progress = "solve"
        else:
            goal_progress = f"{len(goals_done)}/{max_goals} goal(s)"
        site_progress = f"{new_written} site(s) written"

        suggest_log(
            verbose,
            f"site suggest: ══ attempt {attempt} (site #{site_n}, {goal_progress}, {site_progress}) "
            f"(uncovered {100.0 * uncovered_before:.2f}% below depth≥{goal}) ══",
        )

        with suggest_step(verbose, f"generate candidates for site #{site_n}"):
            suggest_progress(verbose, f"strategy={provider.name}")
            candidates = provider.generate_candidates(suggest_ctx, iteration=site_n - 1)
        _log_candidate_shortlist(verbose=verbose, iteration=site_n, candidates=candidates)

        if not candidates:
            print(
                f"site suggest: no candidates at attempt {attempt} ({site_progress}, {goal_progress})",
                flush=True,
            )
            break

        best, best_footprint, trials = _run_candidate_trials(
            candidates=candidates,
            iteration=site_n,
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
            suggest_ctx=suggest_ctx,
            hooks=hooks,
        )

        if hooks.corridor_kml:
            _record_corridor_iteration_kml(
                ctx=suggest_ctx,
                iteration=site_n,
                trials=trials,
                best=best,
            )

        if best is None or best_footprint is None:
            if hooks.corridor_kml and corridor_stall_recovery(suggest_ctx):
                print(
                    f"site suggest: corridor recovery at attempt {attempt} "
                    f"({site_progress}, {goal_progress})",
                    flush=True,
                )
                _log_trial_results(
                    verbose=verbose,
                    iteration=site_n,
                    goal=goal,
                    target_label=target_label,
                    trials=trials,
                    best=None,
                )
                continue
            print(
                f"site suggest: no improving candidate at attempt {attempt} "
                f"({site_progress}, {goal_progress})",
                flush=True,
            )
            _log_trial_results(
                verbose=verbose,
                iteration=site_n,
                goal=goal,
                target_label=target_label,
                trials=trials,
                best=None,
            )
            break

        placed_keys = {
            site_location_key(site.lat, site.lon) for site in all_backbone_sites(suggest_ctx)
        }
        if site_location_key(best.lat, best.lon) in placed_keys:
            print(
                f"site suggest: stopping — selected location already placed "
                f"({_format_loc(best.lat, best.lon)}, {site_progress}, {goal_progress})",
                flush=True,
            )
            _log_trial_results(
                verbose=verbose,
                iteration=site_n,
                goal=goal,
                target_label=target_label,
                trials=trials,
                best=best,
            )
            break

        _log_trial_results(
            verbose=verbose,
            iteration=site_n,
            goal=goal,
            target_label=target_label,
            trials=trials,
            best=best,
        )

        grid.add_footprint(best_footprint)
        if on_pick is not None:
            slug = on_pick(best)
            suggest_ctx.session_footprints[slug] = best_footprint
            suggest_ctx.preset = load_preset(preset_path)
        else:
            session_slug = f"_session_{site_n:04d}"
            suggest_ctx.session_sites.append(
                BackboneSite(slug=session_slug, lat=best.lat, lon=best.lon)
            )
            suggest_ctx.session_footprints[session_slug] = best_footprint
        if hooks.corridor_kml and suggest_ctx.corridor_state is not None:
            suggest_ctx.corridor_state.stall_count = 0
            suggest_ctx.corridor_state.recovery_buffer_bonus_m = 0.0
        winners.append(best)
        new_after = len(winners)
        uncovered_after = 100.0 * grid.uncovered_fraction(goal_depth=goal)
        goals_after = goals_satisfied_since(
            suggest_ctx,
            planning_complete=provider.planning_complete(suggest_ctx),
            baseline=goals_at_start,
            tracked_keys=tracked_keys_at_start,
        )
        if max_goals is None:
            goal_progress_after = "solve"
        else:
            goal_progress_after = f"{len(goals_after)}/{max_goals} goal(s)"
        print(
            f"site suggest: wrote site #{new_after} ({goal_progress_after}) "
            f"gain={best.gain_cells} cells uncovered={uncovered_after:.2f}% "
            f"({best.lat:.5f}, {best.lon:.5f})",
            flush=True,
        )
        if max_goals is not None and len(goals_after) >= max_goals:
            names = ", ".join(sorted(goals_after)) or "?"
            print(
                f"site suggest: stopping — {len(goals_after)} goal(s) satisfied "
                f"(budget {max_goals}: {names})",
                flush=True,
            )
            break
        _log_selection_summary(
            verbose=verbose,
            iteration=len(winners),
            best=best,
            uncovered_pct=uncovered_after,
            max_goals=max_goals,
        )

    if hooks.corridor_kml:
        from peaky_finders.site_suggestions.corridor_kml import flush_corridor_kml

        flush_corridor_kml(suggest_ctx)

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
