"""Python incremental executor for peaky ``build`` — no Makefile generation."""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock

from peaky_finders.aggregate_kmz_cmd import run_aggregate_kmz
from peaky_finders.build_configure import BuildConfigurePlan, PlannedClipLayer, PlannedViewshedWorkspace, configure_preset_build, ConfigureError
from peaky_finders.build_fresh_checks import (
    artefact_mtime_stale,
    bundle_resolve_fresh,
    clip_layer_artefacts_fresh,
    composite_artefacts_fresh,
    eligible_artefacts_fresh,
    reference_artefacts_fresh,
    viewshed_request_digest_matches,
)
from peaky_finders.build_graph import (
    PeakyGraphTarget,
    build_target_graph,
    clip_target_id,
    dem_bulk_stamp_path,
    filter_subgraph,
    pairwise_target_id,
    subgraph_roots_for,
    topo_sort,
)
from peaky_finders.site_suggestions.runner import run_site_suggestion_pass
from peaky_finders.bundle_clips import composite_workspace_dir, eligible_land_use_workspace_dir
from peaky_finders.bundle_commands import (
    run_bundle_clip,
    run_bundle_composite,
    run_bundle_dem,
    run_bundle_eligible,
    run_bundle_plss,
    run_bundle_reference,
    run_bundle_resolve,
)
from peaky_finders.cli import run_splat
from peaky_finders.eligible_union_store import eligible_union_complete_matches, eligible_union_digest
from peaky_finders.mesh_commands import (
    run_mesh_depth,
    run_mesh_eligible_union,
    run_mesh_links,
    run_mesh_pairwise,
)
from peaky_finders.mesh_depth_store import mesh_depth_set_update_kind
from peaky_finders.mesh_pairwise_store import pairwise_complete_digest_matches
from peaky_finders.preset_mapping import preset_to_request
from peaky_finders.plss_mlrs_fetch import plss_bundle_build_stale
from peaky_finders.preset_stamps import ensure_stamp, stamp_file_is_current, stamp_path
from peaky_finders.sites_job import Preset, load_preset, resolved_preset_build_dir
from peaky_finders.skadi_dem import (
    read_dem_prefetch_stamp_fingerprint,
    skadi_missing_mirror_tiles_for_bounds,
    skadi_tile_set_fingerprint,
    write_dem_prefetch_stamp,
)
from peaky_finders.viewshed_batch import resolved_splatter_batch_jobs, run_viewshed_batch
from peaky_finders.viewshed_workspace import viewshed_workspace_digest
from peaky_finders.web.stream import emit_op

_print_lock = Lock()


def _log(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


def _eligible_composite(plan: BuildConfigurePlan):
    for c in plan.composites:
        if c.role == "eligible":
            return c
    raise ValueError("eligible composite missing from plan")


def _composite_by_role(plan: BuildConfigurePlan, role: str):
    for c in plan.composites:
        if c.role == role:
            return c
    raise ValueError(role)


def _clip_for_target(plan: BuildConfigurePlan, tid: str) -> PlannedClipLayer:
    suf = tid.rsplit(":", 1)[-1]
    if not suf.isdigit():
        raise ValueError(tid)
    idx = int(suf)
    lyr = plan.clip_layers[idx]
    if clip_target_id(lyr, idx) != tid:
        raise KeyError(tid)
    return lyr


def _workspace_by_digest(plan: BuildConfigurePlan, digest: str):
    for ws in plan.viewshed_workspaces:
        if ws.digest == digest:
            return ws
    raise KeyError(digest)


def _sorted_viewshed_digests(job: Preset) -> tuple[str, ...]:
    out = []
    for slug in sorted(job.sites.keys()):
        site = job.sites[slug]
        req = preset_to_request(job, float(site.lat), float(site.lon))
        out.append(viewshed_workspace_digest(request=req))
    return tuple(sorted(out))


def _dem_bulk_prefetch_bounds(plan: BuildConfigurePlan) -> tuple[float, float, float, float] | None:
    import geopandas as gpd

    from peaky_finders.bundle_build import ELIGIBLE_LAND_USE_LAYER

    gpkg = _eligible_composite(plan).union_gpkg
    if not gpkg.is_file():
        return None
    try:
        loaded = gpd.read_file(gpkg, layer=ELIGIBLE_LAND_USE_LAYER)
    except (OSError, ValueError):
        return None
    if loaded.empty or loaded.geometry.is_empty.all():
        return None
    minx, miny, maxx, maxy = map(float, loaded.total_bounds)
    return (minx, miny, maxx, maxy)


def _dem_bulk_prefetch_stale(plan: BuildConfigurePlan) -> bool:
    bounds = _dem_bulk_prefetch_bounds(plan)
    if bounds is None:
        return False
    minx, miny, maxx, maxy = bounds
    mirror_root = dem_bulk_stamp_path(plan).parent
    fp, missing = skadi_missing_mirror_tiles_for_bounds(
        minx=minx,
        miny=miny,
        maxx=maxx,
        maxy=maxy,
        mirror_root=mirror_root,
    )
    stamp = dem_bulk_stamp_path(plan)
    got = read_dem_prefetch_stamp_fingerprint(stamp)
    if got != fp:
        return True
    return bool(missing)


def _composite_sha(plan: BuildConfigurePlan, role: str) -> str:
    for c in plan.composites:
        if c.role == role:
            return c.sha
    raise ValueError(f"composite role missing: {role!r}")


def _bundle_resolve_content_fresh(plan: BuildConfigurePlan) -> bool:
    return bundle_resolve_fresh(
        Path(plan.bundle_resolve).expanduser().resolve(),
        clips_root_expected=plan.clips_root,
        aoi_sha=_composite_sha(plan, "aoi"),
        include_sha=_composite_sha(plan, "include"),
        exclude_sha=_composite_sha(plan, "exclude"),
        eligible_sha=_composite_sha(plan, "eligible"),
        reference={ref.entry_id: ref.sha for ref in plan.references},
    )


def _site_slug_to_viewshed_digest(job: Preset) -> dict[str, str]:
    out: dict[str, str] = {}
    for slug in job.sites.keys():
        site = job.sites[slug]
        req = preset_to_request(job, float(site.lat), float(site.lon))
        out[slug] = viewshed_workspace_digest(request=req)
    return out


def _mesh_depth_stamp_mtime_prereqs(plan: BuildConfigurePlan) -> tuple[Path, ...]:
    preset_f = Path(plan.preset_path).expanduser().resolve()
    parts: list[Path] = []
    if plan.has_bundle:
        for sec in ("bundle_kml_overlay", "bundle_mesh_coverage"):
            parts.append(stamp_path(preset_f, sec).resolve())
    return tuple(p for p in parts if p.is_file())


def target_stale(plan: BuildConfigurePlan, preset: Preset, node: PeakyGraphTarget) -> bool:
    tid = node.id
    if tid.startswith("stamp:"):
        sec = tid.removeprefix("stamp:")
        preset_path_r = Path(plan.preset_path).expanduser().resolve()
        return not stamp_file_is_current(sec, preset_path_r)
    if tid == "bundle:plss":
        return plss_bundle_build_stale(
            cache_base=resolved_preset_build_dir(Path(plan.preset_path)),
            preset=preset,
        )
    if tid == "dem:bulk":
        return _dem_bulk_prefetch_stale(plan)
    if tid.startswith("clip:"):
        lyr = _clip_for_target(plan, tid)
        return not clip_layer_artefacts_fresh(lyr.gpkg, expected_sha=lyr.sha)
    if tid.startswith("composite:"):
        role = tid.removeprefix("composite:")
        if role == "eligible":
            c = _eligible_composite(plan)
            ed = eligible_land_use_workspace_dir(plan.clips_root)
            return not eligible_artefacts_fresh(c.union_gpkg, eligible_dir=ed, expected_eligible_sha=c.sha)
        c = _composite_by_role(plan, role)
        cw = composite_workspace_dir(plan.clips_root, role)
        return not composite_artefacts_fresh(
            c.union_gpkg,
            composite_workspace_dir=cw,
            expected_workspace_sha=c.sha,
            role=role,
        )
    if tid == "bundle:eligible":
        c = _eligible_composite(plan)
        ed = eligible_land_use_workspace_dir(plan.clips_root)
        if any(not Path(o).expanduser().is_file() for o in node.outputs):
            return True
        return not eligible_artefacts_fresh(c.union_gpkg, eligible_dir=ed, expected_eligible_sha=c.sha)
    if tid.startswith("bundle:reference:"):
        eid = tid.removeprefix("bundle:reference:")
        for ref in plan.references:
            if ref.entry_id != eid:
                continue

            return not reference_artefacts_fresh(
                ref.gpkg,
                ref_dir=ref.gpkg.parent,
                reference_sha_expected=ref.sha,
            )


        raise KeyError(eid)
    if tid == "bundle:resolve":
        if any(not Path(o).expanduser().is_file() for o in node.outputs):
            return True
        return not _bundle_resolve_content_fresh(plan)
    if tid.startswith("viewshed:"):
        tail = tid.removeprefix("viewshed:")
        rep, phase = tail.rsplit(":", 1)
        ws = _workspace_by_digest(plan, rep)

        mq = tuple(Path(x).expanduser().resolve() for x in node.mtime_prereqs if Path(x).expanduser().is_file())
        if phase == "request":
            if not viewshed_request_digest_matches(ws.workdir, expected_workspace_digest=ws.digest):
                return True
            return artefact_mtime_stale(node.outputs, mq)
        return artefact_mtime_stale(node.outputs, mq)
    if tid.startswith("mesh:pair:"):
        s_lo, s_hi = tid.removeprefix("mesh:pair:").split("::", 1)
        vd_a = viewshed_workspace_digest(
            request=preset_to_request(preset, float(preset.sites[s_lo].lat), float(preset.sites[s_lo].lon))
        )
        vd_b = viewshed_workspace_digest(
            request=preset_to_request(preset, float(preset.sites[s_hi].lat), float(preset.sites[s_hi].lon))
        )
        for pair in plan.mesh_pairs:
            if pairwise_target_id(pair.slug_a, pair.slug_b) != tid:
                continue
            mq = tuple(
                Path(x).expanduser().resolve() for x in node.mtime_prereqs if Path(x).expanduser().is_file()
            )
            if not pairwise_complete_digest_matches(pair.complete_json.parent, vd_a=vd_a, vd_b=vd_b):
                return True
            return artefact_mtime_stale(node.outputs, mq)
        raise KeyError(tid)
    if tid == "mesh:eligible_union":
        if plan.eligible_union_complete is None:
            return False
        c = _eligible_composite(plan)
        eu_dir = Path(plan.eligible_union_complete).expanduser().resolve().parent
        dg = eligible_union_digest(eligible_gpkg=c.union_gpkg, eligible_sha=c.sha)
        mq = tuple(Path(x).expanduser().resolve() for x in node.mtime_prereqs if Path(x).expanduser().is_file())


        if artefact_mtime_stale(node.outputs, mq):
            return True
        return not eligible_union_complete_matches(
            cache_dir=eu_dir,
            cache_digest=dg,
            eligible_sha=c.sha,
            source_gpkg=c.union_gpkg,

        )


    if tid == "mesh:depth":

        if plan.mesh_depth_complete is None:
            return False
        mx = 4096
        if preset.bundle and preset.bundle.mesh_coverage is not None:
            mx = preset.bundle.mesh_coverage.max_raster_dimension
        sdir = plan.mesh_depth_complete.parent.expanduser().resolve()
        vds = _sorted_viewshed_digests(preset)
        slug_to_digest = _site_slug_to_viewshed_digest(preset)
        kind = mesh_depth_set_update_kind(
            set_dir=sdir,
            viewshed_digests=vds,
            site_slugs=sorted(preset.sites.keys()),
            max_raster_dimension=mx,
            site_slug_to_digest=slug_to_digest,
        )
        if kind == "current":
            mq = _mesh_depth_stamp_mtime_prereqs(plan)
            return artefact_mtime_stale(node.outputs, mq)
        return True


    if tid == "mesh:links":

        return artefact_mtime_stale(node.outputs, node.mtime_prereqs)

    if tid == "kmz:out":
        return artefact_mtime_stale(node.outputs, node.mtime_prereqs)
    raise ValueError(f"staleness unsupported for target {tid!r}")


def execute_target(
    plan: BuildConfigurePlan,
    preset: Preset,
    node: PeakyGraphTarget,
    *,
    bundle_data_dir: Path | None,
    verbose: bool,
) -> int:
    preset_path_r = Path(plan.preset_path).expanduser().resolve()
    tid = node.id

    if verbose:
        _log(f"+ {tid}")

    if tid.startswith("stamp:"):
        sec = tid.removeprefix("stamp:")
        ensure_stamp(sec, preset_path_r, quiet=not verbose)
        return 0

    if tid == "bundle:plss":
        return run_bundle_plss(preset_path_r)

    if tid == "dem:bulk":
        ec = run_bundle_dem(preset_path_r, tile=None, verbose=verbose)
        if ec:
            return ec
        bounds = _dem_bulk_prefetch_bounds(plan)
        if bounds is not None:
            minx, miny, maxx, maxy = bounds
            fp = skadi_tile_set_fingerprint(minx, miny, maxx, maxy)
            write_dem_prefetch_stamp(dem_bulk_stamp_path(plan), fp)
        return 0

    if tid.startswith("clip:"):
        lyr = _clip_for_target(plan, tid)
        return run_bundle_clip(str(lyr.role), lyr.layer, preset_path_r)

    if tid.startswith("composite:"):
        return run_bundle_composite(tid.removeprefix("composite:"), preset_path_r)

    if tid == "bundle:eligible":
        return run_bundle_eligible(preset_path_r, verbose=verbose)

    if tid.startswith("bundle:reference:"):
        return run_bundle_reference(tid.removeprefix("bundle:reference:"), preset_path_r)

    if tid == "bundle:resolve":
        return run_bundle_resolve(preset_path_r)

    if tid.startswith("viewshed:"):
        tail = tid.removeprefix("viewshed:")
        rep, phase = tail.rsplit(":", 1)
        if phase == "coverage":
            raise RuntimeError(f"viewshed coverage must run via splatter run-batch, not {tid!r}")
        ws = _workspace_by_digest(plan, rep)
        vs = argparse.Namespace(
            granular_viewshed_slug=ws.site_slugs[0],
            viewshed_workspace_only=True,
            viewshed_phase=phase,
            verbose=verbose,
            preset_path=str(preset_path_r),
        )
        return run_splat(vs)

    if tid.startswith("mesh:pair:"):
        slug_lo, slug_hi = tid.removeprefix("mesh:pair:").split("::", 1)


        return run_mesh_pairwise(slug_lo, slug_hi, preset_path_r)




    if tid == "mesh:eligible_union":
        return run_mesh_eligible_union(preset_path_r)

    if tid == "mesh:depth":
        return run_mesh_depth(preset_path_r)

    if tid == "mesh:links":
        return run_mesh_links(preset_path_r)

    if tid == "kmz:out":
        ns = argparse.Namespace(data_dir=bundle_data_dir)
        return run_aggregate_kmz(ns)




    raise ValueError(f"execute unsupported for target {tid!r}")


def _viewshed_coverage_digest(tid: str) -> str | None:
    if not tid.startswith("viewshed:") or not tid.endswith(":coverage"):
        return None
    return tid.removeprefix("viewshed:").removesuffix(":coverage")


def _has_pending_viewshed_requests(pending: set[str]) -> bool:
    return any(t.startswith("viewshed:") and t.endswith(":request") for t in pending)


def _flush_viewshed_coverage_batch(
    *,
    plan: BuildConfigurePlan,
    preset: Preset,
    pending: set[str],
    subset: dict[str, PeakyGraphTarget],
    force: bool,
    verbose: bool,
    jobs: int,
) -> dict[str, int]:
    """Run one splatter ``run-batch`` for every stale ``viewshed:*:coverage`` still pending."""
    stale_coverage: list[str] = []
    fresh_coverage: list[str] = []
    for tid in sorted(pending):
        digest = _viewshed_coverage_digest(tid)
        if digest is None:
            continue
        node = subset[tid]
        if any(dep in pending for dep in node.depends_on):
            continue
        if force or target_stale(plan, preset, node):
            stale_coverage.append(tid)
        else:
            fresh_coverage.append(tid)

    if not stale_coverage and not fresh_coverage:
        return {}

    rc = 0
    if stale_coverage:
        workspaces: list[PlannedViewshedWorkspace] = []
        seen_digest: set[str] = set()
        for tid in stale_coverage:
            digest = _viewshed_coverage_digest(tid)
            assert digest is not None
            if digest in seen_digest:
                continue
            seen_digest.add(digest)
            workspaces.append(_workspace_by_digest(plan, digest))

        workers = resolved_splatter_batch_jobs(
            preset=preset,
            workspace_count=len(workspaces),
            build_jobs=jobs,
        )
        if verbose:
            _log(f"build: viewshed coverage batch ({len(workspaces)} workspace(s), workers={workers})")
        rc = run_viewshed_batch(
            preset=preset,
            viewshed_root=plan.viewsheds_root,
            workspaces=workspaces,
            coverage_verbose=verbose,
            build_jobs=jobs,
        )

    return dict.fromkeys([*stale_coverage, *fresh_coverage], rc)


def _subgraph_without_target(
    nodes: dict[str, PeakyGraphTarget],
    target_id: str,
) -> dict[str, PeakyGraphTarget]:
    sub = filter_subgraph(nodes, (target_id,))
    return {k: v for k, v in sub.items() if k != target_id}


def _run_target_subgraph(
    *,
    plan: BuildConfigurePlan,
    preset: Preset,
    subset: dict[str, PeakyGraphTarget],
    force: bool,
    dry_run: bool,
    jobs: int,
    verbose: bool,
    bundle_data_dir: Path | None,
) -> int:
    seq = topo_sort(subset)
    if dry_run:
        for tid in seq:
            node = subset[tid]
            stale = force or target_stale(plan, preset, node)
            tag = "build" if stale else "fresh"
            print(f"{tag}: {tid}")
        return 0

    pending = set(seq)
    parallel = max(1, jobs)
    while pending:
        batched_codes: dict[str, int] = {}
        if not dry_run and not _has_pending_viewshed_requests(pending):
            batched_codes = _flush_viewshed_coverage_batch(
                plan=plan,
                preset=preset,
                pending=pending,
                subset=subset,
                force=force,
                verbose=verbose,
                jobs=parallel,
            )
            batch_failures = [(t, rc) for t, rc in batched_codes.items() if rc != 0]
            if batch_failures:
                tt, rr = batch_failures[0]
                print(f"build: viewshed batch target {tt!r} exited {rr}", file=sys.stderr)
                return rr
            pending.difference_update(batched_codes)

        runnable = sorted(
            (
                tid
                for tid in pending
                if not (tid.startswith("viewshed:") and tid.endswith(":coverage"))
                and all(d not in pending for d in subset[tid].depends_on)
            ),
        )
        if not runnable and not batched_codes:
            print("build: internal error — stalled waiting on unresolved nodes", file=sys.stderr)
            return 2
        if not runnable:
            continue

        wave = runnable if len(runnable) <= parallel else runnable[:parallel]

        def runner(tid_: str, node_: PeakyGraphTarget) -> tuple[str, int]:
            stale = force or target_stale(plan, preset, node_)
            if verbose:
                verb = "run" if stale or force else "skip"
                _log(f"build: {tid_} [{verb}]")
            if not stale and not force:
                emit_op("build.target", target=tid_, status="skip")
                return tid_, 0
            emit_op("build.target", target=tid_, status="start")
            rc = execute_target(plan, preset, node_, bundle_data_dir=bundle_data_dir, verbose=verbose)
            emit_op("build.target", target=tid_, status="done" if rc == 0 else "error")
            return tid_, rc

        codes: dict[str, int] = dict(batched_codes)
        if parallel <= 1 or len(wave) <= 1:
            for tid in wave:
                nid, rc = runner(tid, subset[tid])
                codes[nid] = rc
        elif wave:
            mx = min(parallel, len(wave))
            with ThreadPoolExecutor(max_workers=mx) as pool:
                futs = {pool.submit(runner, tid, subset[tid]): tid for tid in wave}
                for fut in as_completed(futs):
                    tid_, rc = fut.result()
                    codes[tid_] = rc

        wave_done = wave
        failures = [(t, codes[t]) for t in wave_done if codes.get(t, 0) != 0]
        if failures:
            tt, rr = failures[0]
            print(f"build: target {tt!r} exited {rr}", file=sys.stderr)
            return rr
        pending.difference_update(wave_done)
    return 0


def run_incremental_build(
    *,
    preset_path: Path,
    data_dir_arg: Path | None,
    selection: str,
    force: bool,
    dry_run: bool,

    jobs: int,
    verbose: bool,
    suggest_n: int | None = None,
    replace_suggested: bool = False,

) -> int:
    preset_path_r = preset_path.expanduser().resolve()


    preset = load_preset(preset_path_r)



    try:
        plan = configure_preset_build(preset_path=preset_path_r, data_dir=data_dir_arg)
    except ConfigureError as exc:
        print(str(exc), file=sys.stderr)



        return 2


    nodes_all = build_target_graph(plan, preset)

    if suggest_n is not None:
        if selection.strip().lower() not in ("all", "kmz"):
            print(
                "build: --suggest requires --target all (default) or kmz",
                file=sys.stderr,
            )
            return 2
        pre_nodes = _subgraph_without_target(nodes_all, "kmz:out")
        print(f"build: suggest pass — materialize known sites ({len(pre_nodes)} targets)…", flush=True)
        rc = _run_target_subgraph(
            plan=plan,
            preset=preset,
            subset=pre_nodes,
            force=force,
            dry_run=dry_run,
            jobs=jobs,
            verbose=verbose,
            bundle_data_dir=data_dir_arg,
        )
        if rc != 0:
            return rc
        from peaky_finders.site_suggestions.solver import SOLVE_UNTIL_COMPLETE

        budget_label = "solve" if int(suggest_n) == SOLVE_UNTIL_COMPLETE else str(int(suggest_n))
        if dry_run:
            print(f"build: would run site suggest pass ({budget_label})")
        else:
            emit_op("build.phase", name="suggest")
            run_site_suggestion_pass(
                preset=preset,
                preset_path=preset_path_r,
                plan=plan,
                suggest_cli_n=int(suggest_n),
                replace_suggested=replace_suggested,
                verbose=verbose,
                jobs=jobs,
            )
            preset = load_preset(preset_path_r)
            try:
                plan = configure_preset_build(preset_path=preset_path_r, data_dir=data_dir_arg)
            except ConfigureError as exc:
                print(str(exc), file=sys.stderr)
                return 2
            nodes_all = build_target_graph(plan, preset)

    roots = subgraph_roots_for(selection, plan, preset, nodes_all)
    subset = filter_subgraph(nodes_all, roots)

    return _run_target_subgraph(
        plan=plan,
        preset=preset,
        subset=subset,
        force=force,
        dry_run=dry_run,
        jobs=jobs,
        verbose=verbose,
        bundle_data_dir=data_dir_arg,
    )



