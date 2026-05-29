"""Batch splatter viewshed evaluation for site-suggestion candidates."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from shapely.geometry.base import BaseGeometry

from peaky_finders.workspace_plan import PlannedViewshedWorkspace
from peaky_finders.viewshed_workspace import viewshed_request_digest_matches
from peaky_finders.coverage_footprint import read_coverage_footprint
from peaky_finders.models import SplatCoverageRequest
from peaky_finders.preset_mapping import preset_to_request
from peaky_finders.site_suggestions.candidates import SiteCandidate
from peaky_finders.site_suggestions.log import suggest_log, suggest_progress, suggest_step, SuggestProgressTicker
from peaky_finders.sites_job import (
    BundleKmlLayerStyle,
    Preset,
    resolved_viewshed_coverage_kml_style,
)
from peaky_finders.splat_polygonize import SPLAT_GPKG_NAME, SPLAT_OUTPUT_PPM_BASENAME
from peaky_finders.splat_pipeline import footprint_vectorize_needed, write_coverage_footprints
from peaky_finders.viewshed_batch import run_viewshed_batch
from peaky_finders.viewshed_workspace import (
    resolved_viewshed_workdir,
    viewshed_workspace_digest,
)


FootprintRunner = Callable[..., BaseGeometry | None]
ViewshedReadyCallback = Callable[[int, SiteCandidate, Path, BaseGeometry | None], None]


@dataclass(frozen=True)
class CandidateViewshedResult:
    candidate: SiteCandidate
    footprint: BaseGeometry | None
    workdir: Path
    from_cache: bool


def _candidate_key(c: SiteCandidate) -> tuple[int, int]:
    return (int(round(c.lat * 1e5)), int(round(c.lon * 1e5)))


def _planned_workspace(
    *,
    preset: Preset,
    viewshed_root: Path,
    lat: float,
    lon: float,
) -> tuple[PlannedViewshedWorkspace, SplatCoverageRequest, str]:
    req = preset_to_request(preset, float(lat), float(lon))
    digest = viewshed_workspace_digest(request=req)
    workdir = resolved_viewshed_workdir(digest=digest, viewshed_root=viewshed_root)
    return (
        PlannedViewshedWorkspace(
            digest=digest,
            workdir=workdir,
            output_ppm=workdir / SPLAT_OUTPUT_PPM_BASENAME,
            splat_png=workdir / "splat.png",
            splat_gpkg=workdir / SPLAT_GPKG_NAME,
            request_json=workdir / "request.json",
            site_slugs=(),
        ),
        req,
        digest,
    )


def _ensure_request_json(workdir: Path, req: SplatCoverageRequest) -> None:
    wd = Path(workdir).expanduser().resolve()
    wd.mkdir(parents=True, exist_ok=True)
    req_path = wd / "request.json"
    body = req.model_dump_json(indent=2, exclude_none=True)
    if req_path.is_file() and req_path.read_text(encoding="utf-8") == body:
        return
    req_path.write_text(body, encoding="utf-8")


def _needs_coverage_run(ws: PlannedViewshedWorkspace, *, digest: str) -> bool:
    ppm = ws.output_ppm
    if not ppm.is_file():
        return True
    return not viewshed_request_digest_matches(ws.workdir, expected_workspace_digest=digest)


def _limit_nested_blas_threads() -> None:
    import os

    for key in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
        "NUMEXPR_NUM_THREADS",
    ):
        os.environ.setdefault(key, "1")


def _polygon_style_dict(preset: Preset) -> dict:
    kml_ov = preset.bundle.kml_overlay if preset.bundle else None
    return resolved_viewshed_coverage_kml_style(kml_ov).model_dump()


def _read_footprint_worker(workdir: str) -> BaseGeometry | None:
    """Thread-pool worker: read an already-vectorized footprint GPKG."""
    gpkg = Path(workdir) / SPLAT_GPKG_NAME
    if not gpkg.is_file():
        return None
    return read_coverage_footprint(gpkg)


def _ensure_footprint_worker(workdir: str, style_dict: dict) -> BaseGeometry | None:
    """Process-pool worker: read footprint GPKG (vectorize runs after docker batch)."""
    wd = Path(workdir)
    gpkg = wd / SPLAT_GPKG_NAME
    if not gpkg.is_file() and footprint_vectorize_needed(wd):
        polygon_style = BundleKmlLayerStyle.model_validate(style_dict)
        if not write_coverage_footprints(data_dir=wd, polygon_style=polygon_style):
            return None
    if not gpkg.is_file():
        return None
    return read_coverage_footprint(gpkg)


def _ensure_footprint(
    ws: PlannedViewshedWorkspace,
    *,
    preset: Preset,
) -> BaseGeometry | None:
    if not ws.output_ppm.is_file():
        return None
    if footprint_vectorize_needed(ws.workdir):
        kml_ov = preset.bundle.kml_overlay if preset.bundle else None
        style = resolved_viewshed_coverage_kml_style(kml_ov)
        if not write_coverage_footprints(data_dir=ws.workdir, polygon_style=style):
            return None
    if not ws.splat_gpkg.is_file():
        return None
    return read_coverage_footprint(ws.splat_gpkg)


def _footprint_status(footprint: BaseGeometry | None) -> str:
    return "empty" if footprint is None or footprint.is_empty else "ok"


def _collect_one_candidate_footprint(
    *,
    index: int,
    cand: SiteCandidate,
    ws: PlannedViewshedWorkspace,
    was_cached: bool,
    preset: Preset,
    preset_path: Path,
    footprint_runner: FootprintRunner | None,
) -> tuple[int, CandidateViewshedResult, str]:
    if footprint_runner is not None:
        fp = footprint_runner(
            preset=preset,
            preset_path=preset_path,
            lat=cand.lat,
            lon=cand.lon,
            workdir=ws.workdir,
        )
    else:
        fp = _ensure_footprint(ws, preset=preset)
    result = CandidateViewshedResult(
        candidate=cand,
        footprint=fp,
        workdir=ws.workdir,
        from_cache=was_cached and fp is not None,
    )
    return index, result, _footprint_status(fp)


def _notify_viewshed_ready(
    callback: ViewshedReadyCallback | None,
    *,
    trial_index: int,
    cand: SiteCandidate,
    workdir: Path,
    footprint: BaseGeometry | None,
) -> None:
    if callback is None:
        return
    callback(trial_index, cand, workdir, footprint)


def _collect_candidate_footprints(
    *,
    planned: list[tuple[SiteCandidate, PlannedViewshedWorkspace, str, bool]],
    preset: Preset,
    preset_path: Path,
    verbose: bool,
    jobs: int,
    footprint_runner: FootprintRunner | None = None,
    trial_index_start: int = 1,
    on_viewshed_ready: ViewshedReadyCallback | None = None,
) -> dict[tuple[int, int], CandidateViewshedResult]:
    n = len(planned)
    workers = max(1, int(jobs))
    mx = min(workers, n) if n else 1
    need_vectorize = footprint_runner is None and any(
        footprint_vectorize_needed(ws.workdir) for _c, ws, _digest, _cached in planned
    )
    use_process_pool = footprint_runner is None and need_vectorize and mx > 1 and n > 1
    use_thread_pool = footprint_runner is None and not need_vectorize and mx > 1 and n > 1
    if use_process_pool:
        pool_kind = "process"
    elif use_thread_pool:
        pool_kind = "thread"
    else:
        pool_kind = "serial"
    label = f"read {n} candidate footprint(s), workers={mx} ({pool_kind})"
    out: dict[tuple[int, int], CandidateViewshedResult] = {}

    with suggest_step(verbose, label):
        ticker = SuggestProgressTicker(verbose, label="footprint", interval_s=0.5)
        if mx <= 1 or n <= 1:
            for offset, (cand, ws, _digest, was_cached) in enumerate(planned):
                trial_index = trial_index_start + offset
                if footprint_runner is not None and verbose:
                    suggest_progress(
                        verbose,
                        f"mock viewshed [{trial_index}/{trial_index_start + n - 1}] "
                        f"{_format_loc(cand.lat, cand.lon)}…",
                    )
                _index, result, area_s = _collect_one_candidate_footprint(
                    index=trial_index,
                    cand=cand,
                    ws=ws,
                    was_cached=was_cached,
                    preset=preset,
                    preset_path=preset_path,
                    footprint_runner=footprint_runner,
                )
                out[_candidate_key(cand)] = result
                _notify_viewshed_ready(
                    on_viewshed_ready,
                    trial_index=trial_index,
                    cand=cand,
                    workdir=ws.workdir,
                    footprint=result.footprint,
                )
                if verbose:
                    tag = "footprint" if footprint_runner is None else "mock viewshed"
                    ticker.maybe(f"{tag} [{trial_index}/{trial_index_start + n - 1}] {area_s}")
            return out

        done = 0
        if use_thread_pool:
            with ThreadPoolExecutor(max_workers=mx) as pool:
                futs = {
                    pool.submit(_read_footprint_worker, str(ws.workdir.resolve())): (
                        trial_index_start + offset,
                        cand,
                        ws,
                        was_cached,
                    )
                    for offset, (cand, ws, _digest, was_cached) in enumerate(planned)
                }
                for fut in as_completed(futs):
                    trial_index, cand, ws, was_cached = futs[fut]
                    fp = fut.result()
                    result = CandidateViewshedResult(
                        candidate=cand,
                        footprint=fp,
                        workdir=ws.workdir,
                        from_cache=was_cached and fp is not None,
                    )
                    out[_candidate_key(cand)] = result
                    _notify_viewshed_ready(
                        on_viewshed_ready,
                        trial_index=trial_index,
                        cand=cand,
                        workdir=ws.workdir,
                        footprint=fp,
                    )
                    done += 1
                    ticker.maybe(f"[{done}/{n}] {_footprint_status(fp)}")
            return out

        if use_process_pool:
            style_dict = _polygon_style_dict(preset)
            with ProcessPoolExecutor(max_workers=mx, initializer=_limit_nested_blas_threads) as pool:
                futs = {
                    pool.submit(_ensure_footprint_worker, str(ws.workdir.resolve()), style_dict): (
                        trial_index_start + offset,
                        cand,
                        ws,
                        was_cached,
                    )
                    for offset, (cand, ws, _digest, was_cached) in enumerate(planned)
                }
                for fut in as_completed(futs):
                    trial_index, cand, ws, was_cached = futs[fut]
                    fp = fut.result()
                    result = CandidateViewshedResult(
                        candidate=cand,
                        footprint=fp,
                        workdir=ws.workdir,
                        from_cache=was_cached and fp is not None,
                    )
                    out[_candidate_key(cand)] = result
                    _notify_viewshed_ready(
                        on_viewshed_ready,
                        trial_index=trial_index,
                        cand=cand,
                        workdir=ws.workdir,
                        footprint=fp,
                    )
                    done += 1
                    ticker.maybe(f"[{done}/{n}] {_footprint_status(fp)}")
            return out

        with ThreadPoolExecutor(max_workers=mx) as pool:
            futs = {
                pool.submit(
                    _collect_one_candidate_footprint,
                    index=trial_index_start + offset,
                    cand=cand,
                    ws=ws,
                    was_cached=was_cached,
                    preset=preset,
                    preset_path=preset_path,
                    footprint_runner=footprint_runner,
                ): trial_index_start + offset
                for offset, (cand, ws, _digest, was_cached) in enumerate(planned)
            }
            for fut in as_completed(futs):
                index, result, area_s = fut.result()
                out[_candidate_key(result.candidate)] = result
                _notify_viewshed_ready(
                    on_viewshed_ready,
                    trial_index=index,
                    cand=result.candidate,
                    workdir=result.workdir,
                    footprint=result.footprint,
                )
                done += 1
                tag = "footprint" if footprint_runner is None else "mock viewshed"
                ticker.maybe(f"{tag} [{done}/{n}] {area_s}")
    return out


def run_candidate_batch_viewsheds(
    *,
    preset: Preset,
    preset_path: Path,
    viewshed_root: Path,
    candidates: list[SiteCandidate],
    verbose: bool = False,
    jobs: int = 1,
    footprint_runner: FootprintRunner | None = None,
    extra_candidates: list[SiteCandidate] | None = None,
    trial_index_start: int = 1,
    on_viewshed_ready: ViewshedReadyCallback | None = None,
) -> dict[tuple[int, int], CandidateViewshedResult]:
    """Evaluate ``candidates`` with one ``splatter run-batch`` when possible.

    When ``extra_candidates`` is set, plan both lists first and run a single
    ``run-batch`` for all stale workspaces before collecting footprints.
    """
    groups = [candidates]
    if extra_candidates:
        groups.append(extra_candidates)
    return _run_candidate_batch_viewshed_groups(
        preset=preset,
        preset_path=preset_path,
        viewshed_root=viewshed_root,
        candidate_groups=groups,
        verbose=verbose,
        jobs=jobs,
        footprint_runner=footprint_runner,
        trial_index_start=trial_index_start,
        on_viewshed_ready=on_viewshed_ready,
    )


def _run_candidate_batch_viewshed_groups(
    *,
    preset: Preset,
    preset_path: Path,
    viewshed_root: Path,
    candidate_groups: list[list[SiteCandidate]],
    verbose: bool = False,
    jobs: int = 1,
    footprint_runner: FootprintRunner | None = None,
    trial_index_start: int = 1,
    on_viewshed_ready: ViewshedReadyCallback | None = None,
) -> dict[tuple[int, int], CandidateViewshedResult]:
    if not any(candidate_groups):
        return {}

    viewshed_root_r = Path(viewshed_root).expanduser().resolve()
    planned: list[tuple[SiteCandidate, PlannedViewshedWorkspace, str, bool]] = []

    total = sum(len(group) for group in candidate_groups)
    with suggest_step(verbose, f"prepare {total} candidate workspace(s)"):
        for group in candidate_groups:
            for cand in group:
                ws, req, digest = _planned_workspace(
                    preset=preset,
                    viewshed_root=viewshed_root_r,
                    lat=cand.lat,
                    lon=cand.lon,
                )
                _ensure_request_json(ws.workdir, req)
                cached = not _needs_coverage_run(ws, digest=digest)
                planned.append((cand, ws, digest, cached))
                if verbose:
                    tag = "cache" if cached else "stale"
                    suggest_progress(
                        verbose,
                        f"workspace {_format_loc(cand.lat, cand.lon)} digest={digest[:8]}… ({tag})",
                    )

    if footprint_runner is not None:
        return _collect_candidate_footprints(
            planned=planned,
            preset=preset,
            preset_path=preset_path,
            verbose=verbose,
            jobs=jobs,
            footprint_runner=footprint_runner,
            trial_index_start=trial_index_start,
            on_viewshed_ready=on_viewshed_ready,
        )

    stale_workspaces = [ws for _c, ws, _digest, cached in planned if not cached]
    if stale_workspaces:
        with suggest_step(verbose, f"splatter run-batch ({len(stale_workspaces)} workspace(s))"):
            rc = run_viewshed_batch(
                preset=preset,
                viewshed_root=viewshed_root_r,
                workspaces=stale_workspaces,
                coverage_verbose=verbose,
                build_jobs=jobs,
            )
            if rc != 0:
                raise RuntimeError(f"splatter run-batch failed with exit code {rc}")

    return _collect_candidate_footprints(
        planned=planned,
        preset=preset,
        preset_path=preset_path,
        verbose=verbose,
        jobs=jobs,
        trial_index_start=trial_index_start,
        on_viewshed_ready=on_viewshed_ready,
    )


def _format_loc(lat: float, lon: float) -> str:
    return f"{lat:.6f}°N, {lon:.6f}°W" if lon < 0 else f"{lat:.6f}°, {lon:.6f}°"
