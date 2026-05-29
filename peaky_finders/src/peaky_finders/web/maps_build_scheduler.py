"""Background clip and mesh rebuild scheduling for the web Maps tab."""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Callable
from typing import Any, Literal

from peaky_finders.mesh_depth_store import mesh_depth_set_update_kind
from peaky_finders.mesh_pairwise_store import pairwise_complete_digest_matches
from peaky_finders.path_labels import mesh_depth_network_rel_dir, mesh_pairwise_rel_dir
from peaky_finders.preset_overlays import collect_site_workspace_assets
from peaky_finders.sites_job import (
    load_preset,
    resolved_bundle_dir,
    resolved_mesh_depth_dir,
    resolved_mesh_pairwise_dir,
    resolved_preset_clips_dir,
)
from peaky_finders.web.project_events import publish_build_status, publish_catalog_refresh, publish_layer_phase
from peaky_finders.web.mesh_layers import _rf_sites_with_footprints, run_mesh_rebuild
from peaky_finders.web.project_maps import _map_entry_build_status, run_maps_rebuild
from peaky_finders.web.settings import stderr_verbose_log

LayerBuildPhase = Literal["idle", "building", "built", "stale", "unavailable"]
LayerDisplayMode = Literal["off", "eligible", "all"]


@dataclass
class _ProjectBuildState:
    clips: LayerBuildPhase = "idle"
    mesh: LayerBuildPhase = "idle"
    clips_error: str | None = None
    mesh_error: str | None = None
    clips_job: threading.Thread | None = field(default=None, repr=False)
    mesh_job: threading.Thread | None = field(default=None, repr=False)
    planner_job: threading.Thread | None = field(default=None, repr=False)


_lock = threading.Lock()
_by_slug: dict[str, _ProjectBuildState] = {}


def _preset_path(slug: str) -> Path:
    from peaky_finders.sites_job import peaky_projects_dir

    cfg = peaky_projects_dir() / slug / "config.yaml"
    if not cfg.is_file():
        raise FileNotFoundError(f"project not found: {slug!r}")
    return cfg


def _state(slug: str) -> _ProjectBuildState:
    with _lock:
        st = _by_slug.get(slug)
        if st is None:
            st = _ProjectBuildState()
            _by_slug[slug] = st
        return st


def _snapshot_phases(slug: str) -> tuple[LayerBuildPhase, str | None, LayerBuildPhase, str | None]:
    with _lock:
        st = _by_slug.get(slug)
        if st is None:
            return "idle", None, "idle", None
        return st.clips, st.clips_error, st.mesh, st.mesh_error


def clips_need_rebuild(slug: str) -> bool:
    preset_path = _preset_path(slug)
    preset = load_preset(preset_path)
    for entry in preset.maps:
        if _map_entry_build_status(preset, preset_path, entry) == "missing":
            return True
    from peaky_finders.bundle_clips import eligible_gpkg_path

    if not eligible_gpkg_path(resolved_preset_clips_dir(preset_path)).is_file():
        return True
    return False


def mesh_need_rebuild(slug: str) -> bool:
    preset_path = _preset_path(slug)
    preset = load_preset(preset_path)
    mesh_cfg = preset.bundle.mesh_coverage if preset.bundle else None
    bundle_dir = resolved_bundle_dir(preset_path=preset_path)
    sites_items = _rf_sites_with_footprints(preset, preset_path, bundle_dir)
    if len(sites_items) < 2:
        return False

    assets = collect_site_workspace_assets(
        preset_path=preset_path,
        preset=preset,
        bundle_cache_root=bundle_dir,
        sites_items=sites_items,
    )
    slug_to_digest = assets.slug_to_digest
    footprints = [
        slug
        for slug, _ in sites_items
        if assets.coverage_gpkg_by_slug.get(slug) is not None
        and assets.coverage_gpkg_by_slug[slug].is_file()
    ]
    if len(footprints) < 2:
        return False

    from peaky_finders.bundle_clips import eligible_gpkg_path

    if not eligible_gpkg_path(resolved_preset_clips_dir(preset_path)).is_file():
        return True

    max_dim = int(mesh_cfg.max_raster_dimension) if mesh_cfg is not None else 4096
    vds = [slug_to_digest[s] for s in footprints]

    rel = mesh_depth_network_rel_dir(max_raster_dimension=max_dim)
    set_dir = resolved_mesh_depth_dir(bundle_dir) / rel
    kind = mesh_depth_set_update_kind(
        set_dir=set_dir,
        viewshed_digests=vds,
        site_slugs=footprints,
        max_raster_dimension=max_dim,
        site_slug_to_digest=slug_to_digest,
    )
    if kind != "current":
        return True

    pairwise_root = resolved_mesh_pairwise_dir(bundle_dir)
    for i, sa in enumerate(footprints):
        vd_a = slug_to_digest[sa]
        for sb in footprints[i + 1 :]:
            vd_b = slug_to_digest[sb]
            pair_dir = pairwise_root / mesh_pairwise_rel_dir(sa, sb)
            if not pairwise_complete_digest_matches(pair_dir, vd_a=vd_a, vd_b=vd_b):
                return True
    return False


def auto_rebuild_enabled() -> bool:
    """Off under pytest unless ``PEAKY_WEB_AUTO_REBUILD=1`` (avoids background GDAL work in tests)."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return os.environ.get("PEAKY_WEB_AUTO_REBUILD", "").strip() in ("1", "true", "yes")
    return os.environ.get("PEAKY_WEB_AUTO_REBUILD", "1").strip() not in ("0", "false", "no")


def layer_build_phase(slug: str, *, built: bool, mesh_layer: bool = False) -> LayerBuildPhase:
    """Per-row phase from local artifact presence and in-flight jobs only (no full staleness scan)."""
    st = _state(slug)
    if mesh_layer and st.mesh == "building":
        return "building"
    if not mesh_layer and st.clips == "building":
        return "building"
    if built:
        return "built"
    return "stale"


def maps_build_status(slug: str) -> dict[str, Any]:
    """In-memory build phases only (no staleness scan, no job scheduling)."""
    clips_phase, clips_err, mesh_phase, mesh_err = _snapshot_phases(slug)
    return {
        "clips": {"phase": clips_phase, "error": clips_err},
        "mesh": {"phase": mesh_phase, "error": mesh_err},
    }


def maps_build_status_for_preset(slug: str, preset: Preset) -> dict[str, Any]:
    """Like ``maps_build_status`` but reuses an already-loaded preset (catalog path)."""
    clips_phase, clips_err, mesh_phase, mesh_err = _snapshot_phases(slug)
    return {
        "clips": {"phase": clips_phase, "error": clips_err},
        "mesh": {"phase": mesh_phase, "error": mesh_err},
    }


def catalog_clips_row_phase(slug: str) -> str:
    """Per-map row phase from in-memory scheduler state (no clip scan)."""
    clips_phase, _, _, _ = _snapshot_phases(slug)
    if clips_phase in ("idle", "stale"):
        return "stale"
    return clips_phase


def _notify_build_status(slug: str) -> None:
    publish_build_status(slug, **maps_build_status(slug))


def _mesh_rebuild_sse_callbacks(slug: str) -> tuple[Callable[[str, str], None], Callable[[str], None]]:
    def on_pair_complete(sa: str, sb: str) -> None:
        publish_layer_phase(slug, layer_id=f"mesh_pair:{mesh_pairwise_rel_dir(sa, sb)}", phase="built")

    def on_depth_band_complete(band: str) -> None:
        publish_layer_phase(slug, layer_id=f"mesh_depth:{band}", phase="built")

    return on_pair_complete, on_depth_band_complete


def _maintenance_prefix(slug: str) -> str:
    return f"maps maintenance ({slug}):"


def _run_clips_job(slug: str) -> None:
    st = _state(slug)
    st.clips = "building"
    st.clips_error = None
    _notify_build_status(slug)
    vlog = stderr_verbose_log()
    if vlog:
        vlog(f"{_maintenance_prefix(slug)} clips rebuild start")
    try:
        run_maps_rebuild(slug, verbose_log=vlog, progress_log=vlog)
        st.clips = "built"
        publish_layer_phase(slug, layer_id="eligible", phase="built")
        publish_catalog_refresh(slug, reason="clips")
        if vlog:
            vlog(f"{_maintenance_prefix(slug)} clips rebuild done")
    except Exception as exc:
        st.clips = "stale"
        st.clips_error = str(exc)
        print(f"maps rebuild ({slug}): {exc}", flush=True)
        if vlog:
            vlog(f"{_maintenance_prefix(slug)} clips rebuild failed: {exc}")
    finally:
        st.clips_job = None
        _notify_build_status(slug)


def _run_mesh_job(slug: str) -> None:
    st = _state(slug)
    st.mesh = "building"
    st.mesh_error = None
    _notify_build_status(slug)
    vlog = stderr_verbose_log()
    if vlog:
        vlog(f"{_maintenance_prefix(slug)} mesh rebuild start (background thread)")
    on_pair, on_band = _mesh_rebuild_sse_callbacks(slug)
    try:
        run_mesh_rebuild(
            slug,
            verbose_log=vlog,
            progress_log=vlog,
            on_pair_complete=on_pair,
            on_depth_band_complete=on_band,
        )
        st.mesh = "built"
        publish_catalog_refresh(slug, reason="mesh")
        if vlog:
            vlog(f"{_maintenance_prefix(slug)} mesh rebuild done")
    except Exception as exc:
        st.mesh = "stale"
        st.mesh_error = str(exc)
        print(f"mesh rebuild ({slug}): {exc}", flush=True)
        if vlog:
            vlog(f"{_maintenance_prefix(slug)} mesh rebuild failed: {exc}")
    finally:
        st.mesh_job = None
        _notify_build_status(slug)


def _run_clips_then_mesh(slug: str) -> None:
    _run_clips_job(slug)
    st = _state(slug)
    if st.clips != "built":
        return
    if not mesh_need_rebuild(slug):
        return
    with _lock:
        st = _by_slug.get(slug)
        if st is None or st.mesh_job is not None or st.mesh == "building":
            return
        _start_mesh_job_locked(slug, st)


def _thread_alive(t: threading.Thread | None) -> bool:
    return t is not None and t.is_alive()


def _start_clips_job_locked(slug: str, st: _ProjectBuildState) -> None:
    if _thread_alive(st.clips_job) or st.clips == "building":
        return
    t = threading.Thread(
        target=_run_clips_then_mesh,
        args=(slug,),
        name=f"peaky-clips-{slug}",
        daemon=True,
    )
    st.clips_job = t
    st.clips = "building"
    t.start()


def _start_mesh_job_locked(slug: str, st: _ProjectBuildState) -> None:
    if _thread_alive(st.mesh_job) or st.mesh == "building":
        return
    t = threading.Thread(target=_run_mesh_job, args=(slug,), name=f"peaky-mesh-{slug}", daemon=True)
    st.mesh_job = t
    st.mesh = "building"
    t.start()


def _maintenance_planner(slug: str) -> None:
    """Scan staleness and enqueue rebuild jobs (never call from request thread)."""
    prefix = _maintenance_prefix(slug)
    vlog = stderr_verbose_log()
    if vlog:
        vlog(f"{prefix} scan start")
    try:
        clips_stale = clips_need_rebuild(slug)
        mesh_stale = mesh_need_rebuild(slug) if auto_rebuild_enabled() else False
    except Exception as exc:
        print(f"maps maintenance planner ({slug}): {exc}", flush=True)
        if vlog:
            vlog(f"{prefix} scan failed: {exc}")
        return

    if vlog:
        auto = auto_rebuild_enabled()
        mesh_note = "stale" if mesh_stale else "current"
        if not auto:
            mesh_note = "auto_rebuild off"
        vlog(
            f"{prefix} clips={'stale' if clips_stale else 'current'}, "
            f"mesh={mesh_note}"
        )

    notify = False
    enqueue_clips = False
    enqueue_mesh = False
    with _lock:
        st = _by_slug.get(slug)
        if st is None:
            st = _ProjectBuildState()
            _by_slug[slug] = st
        st.planner_job = None
        if clips_stale:
            st.clips = "stale" if st.clips != "building" else st.clips
        elif st.clips != "building":
            st.clips = "built"
        notify = True

        if auto_rebuild_enabled():
            if mesh_stale:
                st.mesh = "stale" if st.mesh != "building" else st.mesh
            elif st.mesh != "building":
                st.mesh = "built"

        if auto_rebuild_enabled():
            if clips_stale:
                enqueue_clips = True
                _start_clips_job_locked(slug, st)
            elif mesh_stale:
                enqueue_mesh = True
                _start_mesh_job_locked(slug, st)

    if vlog:
        if enqueue_clips:
            vlog(f"{prefix} enqueue clips rebuild (background thread)")
        elif enqueue_mesh:
            vlog(f"{prefix} enqueue mesh rebuild (pairwise/depth thread pool)")
        else:
            vlog(f"{prefix} scan done: nothing to rebuild")
    if notify:
        _notify_build_status(slug)


def schedule_maps_maintenance(slug: str) -> None:
    """Kick off background staleness scan + rebuild (returns immediately)."""
    if not slug:
        return
    try:
        _preset_path(slug)
    except FileNotFoundError:
        return

    vlog = stderr_verbose_log()
    with _lock:
        st = _by_slug.get(slug)
        if st is None:
            st = _ProjectBuildState()
            _by_slug[slug] = st
        if st.planner_job is not None and st.planner_job.is_alive():
            if vlog:
                vlog(f"{_maintenance_prefix(slug)} planner already running, skip")
            return
        if vlog:
            vlog(f"{_maintenance_prefix(slug)} planner start (background thread)")
        t = threading.Thread(
            target=_maintenance_planner,
            args=(slug,),
            name=f"peaky-maps-planner-{slug}",
            daemon=True,
        )
        st.planner_job = t
        t.start()


def schedule_all_projects_maps_maintenance() -> None:
    """Enqueue maintenance for every project (web server boot; not request path)."""
    from peaky_finders.web.projects import list_projects

    rows = list_projects()
    vlog = stderr_verbose_log()
    if vlog:
        vlog(f"boot maps maintenance: start: {len(rows)} project(s)")
    for row in rows:
        slug = str(row.get("slug") or "")
        if slug:
            schedule_maps_maintenance(slug)
    if vlog:
        vlog(f"boot maps maintenance: planners queued for {len(rows)} project(s)")
