"""Python incremental executor for peaky ``build`` — no Makefile generation."""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock

from peaky_finders.aggregate_kmz_cmd import run_aggregate_kmz
from peaky_finders.build_configure import BuildConfigurePlan, PlannedClipLayer, configure_preset_build, ConfigureError
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
from peaky_finders.mesh_depth_store import mesh_depth_set_complete_matches
from peaky_finders.mesh_pairwise_store import pairwise_complete_digest_matches
from peaky_finders.preset_mapping import preset_to_request
from peaky_finders.plss_mlrs_fetch import plss_bundle_build_stale
from peaky_finders.preset_stamps import ensure_stamp, stamp_file_is_current
from peaky_finders.sites_job import Preset, load_preset, resolved_preset_build_dir
from peaky_finders.skadi_dem import (
    read_dem_prefetch_stamp_fingerprint,
    skadi_missing_mirror_tiles_for_bounds,
    skadi_tile_set_fingerprint,
    write_dem_prefetch_stamp,
)
from peaky_finders.viewshed_workspace import viewshed_workspace_digest

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


def _workspace_by_rep(plan: BuildConfigurePlan, rep: str):
    for ws in plan.viewshed_workspaces:
        if ws.rep_slug == rep:
            return ws
    raise KeyError(rep)


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
        rp = Path(plan.bundle_resolve).expanduser().resolve()
        if artefact_mtime_stale(node.outputs, node.mtime_prereqs):
            return True

        return not bundle_resolve_fresh(rp, clips_root_expected=plan.clips_root)
    if tid.startswith("viewshed:"):
        tail = tid.removeprefix("viewshed:")
        rep, phase = tail.rsplit(":", 1)
        ws = _workspace_by_rep(plan, rep)

        mq = tuple(Path(x).expanduser().resolve() for x in node.mtime_prereqs if Path(x).expanduser().is_file())
        if phase == "request":
            if artefact_mtime_stale(node.outputs, mq):

                return True

            return not viewshed_request_digest_matches(ws.workdir, expected_workspace_digest=ws.digest)
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
        mq = tuple(Path(x).expanduser().resolve() for x in node.mtime_prereqs if Path(x).expanduser().is_file())


        if artefact_mtime_stale(node.outputs, mq):
            return True
        mx = 4096
        if preset.bundle and preset.bundle.mesh_coverage is not None:
            mx = preset.bundle.mesh_coverage.max_raster_dimension
        sdir = plan.mesh_depth_complete.parent.expanduser().resolve()
        return not mesh_depth_set_complete_matches(
            set_dir=sdir,
            viewshed_digests=_sorted_viewshed_digests(preset),
            max_raster_dimension=mx,

        )


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
        ec = run_bundle_dem(preset_path_r, tile=None)
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
        return run_bundle_eligible(preset_path_r)

    if tid.startswith("bundle:reference:"):
        return run_bundle_reference(tid.removeprefix("bundle:reference:"), preset_path_r)

    if tid == "bundle:resolve":
        return run_bundle_resolve(preset_path_r)

    if tid.startswith("viewshed:"):
        tail = tid.removeprefix("viewshed:")
        rep, phase = tail.rsplit(":", 1)
        vs = argparse.Namespace(
            preset_yaml=preset_path_r,
            granular_viewshed_slug=rep,
            viewshed_workspace_only=True,
            viewshed_phase=phase,
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
        ns = argparse.Namespace(preset_yaml=preset_path_r, data_dir=bundle_data_dir)
        return run_aggregate_kmz(ns)




    raise ValueError(f"execute unsupported for target {tid!r}")


def run_incremental_build(
    *,
    preset_path: Path,
    data_dir_arg: Path | None,
    selection: str,
    force: bool,
    dry_run: bool,

    jobs: int,
    verbose: bool,

) -> int:
    preset_path_r = preset_path.expanduser().resolve()


    preset = load_preset(preset_path_r)



    try:
        plan = configure_preset_build(preset_path=preset_path_r, data_dir=data_dir_arg)
    except ConfigureError as exc:
        print(str(exc), file=sys.stderr)



        return 2


    nodes_all = build_target_graph(plan, preset)
    roots = subgraph_roots_for(selection, plan, preset, nodes_all)
    subset = filter_subgraph(nodes_all, roots)



    seq = topo_sort(subset)


    if dry_run:
        for tid in seq:
            node = subset[tid]


            stale = force or target_stale(plan, preset, node)



            tag = "build" if stale else "fresh"






            print(f"{tag}: {tid}")


        return 0


    pending = set(seq)

    completed = set()

    parallel = max(1, jobs)

    while pending:
        runnable = sorted(
            (tid for tid in pending if all(d not in pending for d in subset[tid].depends_on)),
        )
        if not runnable:
            print("build: internal error — stalled waiting on unresolved nodes", file=sys.stderr)
            return 2

        wave = runnable if len(runnable) <= parallel else runnable[:parallel]


        def runner(tid_: str, node_: PeakyGraphTarget) -> tuple[str, int]:
            stale = force or target_stale(plan, preset, node_)
            if verbose:
                verb = "run" if stale or force else "skip"


                _log(f"build: {tid_} [{verb}]")
            if not stale and not force:
                return tid_, 0
            rc = execute_target(plan, preset, node_, bundle_data_dir=data_dir_arg, verbose=False)
            return tid_, rc

        codes: dict[str, int] = {}

        if parallel <= 1 or len(wave) == 1:
            for tid in wave:
                nid, rc = runner(tid, subset[tid])
                codes[nid] = rc
        else:


            mx = min(parallel, len(wave))
            with ThreadPoolExecutor(max_workers=mx) as pool:
                futs = {pool.submit(runner, tid, subset[tid]): tid for tid in wave}
                for fut in as_completed(futs):

                    tid_, rc = fut.result()
                    codes[tid_] = rc

        failures = [(t, codes[t]) for t in wave if codes[t] != 0]
        if failures:
            tt, rr = failures[0]
            print(f"build: target {tt!r} exited {rr}", file=sys.stderr)
            return rr

        pending.difference_update(wave)



        completed.update(wave)

    return 0



