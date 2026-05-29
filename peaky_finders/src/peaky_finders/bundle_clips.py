"""Clip cache layout under ``<preset>/build/clips/``.

Layout::

    build/clips/layer_jobs/{aoi|include|exclude}/<stem>/clip.{gpkg,kml,png} + clip_job.json

``<stem>`` is preset GDB path + layer (+ optional ``where``); no content hash in the path.
    build/clips/{aoi|include|exclude}/composite_workspace.json + manifest.json + union.{gpkg,kml,png}
    build/clips/eligible/… (:func:`eligible_land_use_workspace_dir`, etc.)
    build/clips/reference/<safe-entry-id>/reference_workspace.json + reference.{gpkg,kml,png|.empty}

``resolve.json`` still stores logical fingerprints; directories use stable labels, not fingerprint segments.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Literal

try:
    import fcntl
except ImportError:
    fcntl = None  # pragma: no cover — Windows lacks flock; rely on caller not parallelizing

import geopandas as gpd
from shapely import make_valid
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry

from peaky_finders.sites_job import (
    BundleConfig,
    BundleKmlOverlayStyles,
    GeneralOverlayEntry,
    GdbLayerGroup,
    Preset,
    _gdb_layer_group_payload,
    _gdb_layer_group_sort_key,
    _general_overlay_entry_payload,
    preset_aoi_groups,
    preset_exclude_groups,
    preset_general_overlays,
    preset_include_groups,
)

CLIP_LAYER_FORMAT = "clip_layer/v1"
COMPOSITE_AOI_FORMAT = "composite_aoi/v1"
COMPOSITE_INCLUDE_FORMAT = "composite_include/v1"
COMPOSITE_EXCLUDE_FORMAT = "composite_exclude/v1"
COMPOSITE_ELIGIBLE_FORMAT = "composite_eligible/v2"
REFERENCE_ENTRY_FORMAT = "reference_entry/v1"
BUNDLE_RESOLVE_FORMAT = "bundle_resolve/v2"

MANIFEST_BASENAME = "manifest.json"
LAYER_JOBS_SUBDIR = "layer_jobs"
COMPOSITE_WORKSPACE_JSON_NAME = "composite_workspace.json"
COMPOSITE_WORKSPACE_FMT = "peaky_clip_composite_workspace/v1"
CLIP_JOB_WORKSPACE_NAME = "clip_job.json"
CLIP_JOB_WORKSPACE_FMT = "peaky_clip_job_workspace/v1"
REFERENCE_WORKSPACE_JSON_NAME = "reference_workspace.json"
REFERENCE_WORKSPACE_FMT = "peaky_clip_reference_workspace/v1"

CLIP_GPKG_BASENAME = "clip.gpkg"
UNION_GPKG_BASENAME = "union.gpkg"
ELIGIBLE_GPKG_BASENAME = "eligible_land_use.gpkg"
ELIGIBLE_LAYER = "eligible_land_use"
# Slice sidecars in KMZ: subsets of aggregated eligible attributable to each include clip.
ELIGIBLE_SLICE_LAYERS_SUBDIR = "layers"
ELIGIBLE_WORKSPACE_MANIFEST_NAME = "eligible_workspace.json"
ELIGIBLE_WORKSPACE_MANIFEST_FMT = "peaky_eligible_workspace/v1"
REFERENCE_GPKG_BASENAME = "reference.gpkg"
REFERENCE_GPKG_LAYER = "reference"
REFERENCE_EMPTY_MARKER = ".empty"

ClipRole = Literal["aoi", "include", "exclude"]


@contextmanager
def _clips_cache_exclusive_lock(clips_root: Path) -> Iterator[None]:
    """Exclusive lock so parallel ``make -j`` bundle jobs cannot clobber clip cache writes.

    Granular ``bundle clip`` / ``bundle composite`` / ``bundle eligible`` jobs may run under
    ``make -j``; the lock prevents concurrent writers from clobbering the same clip workspace.
    """
    root = Path(clips_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".peaky_bundle_clip_cache.lock"
    lock_path.touch(exist_ok=True)
    if fcntl is None:
        yield
        return
    handle = lock_path.open("a", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def resolved_clips_cache_root(cache_base: Path) -> Path:
    return Path(cache_base).expanduser().resolve() / "clips"


def _sha16(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def composite_workspace_dir(clips_root: Path, role: ClipRole | str) -> Path:
    """Stable directory for AOI / include / exclude union artifacts."""

    rr = str(role)
    if rr not in {"aoi", "include", "exclude"}:
        raise ValueError(f"composite_workspace_dir: bad role {role!r}")
    return Path(clips_root).expanduser().resolve() / rr


def clip_job_dir(clips_root: Path, role: ClipRole, stem: str) -> Path:
    return Path(clips_root).expanduser().resolve() / LAYER_JOBS_SUBDIR / role / stem


def layer_job_gpkg_path(clips_root: Path, role: ClipRole, stem: str) -> Path:
    return clip_job_dir(clips_root, role, stem) / CLIP_GPKG_BASENAME


def layer_job_kml_path(clips_root: Path, role: ClipRole, stem: str) -> Path:
    return clip_job_dir(clips_root, role, stem) / CLIP_GPKG_BASENAME.replace(".gpkg", ".kml")


def composite_union_gpkg(clips_root: Path, role: str, composite_sha: str | None = None) -> Path:
    _ = composite_sha
    return composite_workspace_dir(clips_root, role) / UNION_GPKG_BASENAME


def reference_entry_dir(clips_root: Path, entry_id: str) -> Path:
    from peaky_finders.path_labels import filesystem_safe_slug

    return Path(clips_root).expanduser().resolve() / "reference" / filesystem_safe_slug(entry_id)


def reference_gpkg_path(clips_root: Path, entry_id: str) -> Path:
    return reference_entry_dir(clips_root, entry_id) / REFERENCE_GPKG_BASENAME


def reference_kml_path(clips_root: Path, entry_id: str) -> Path:
    return reference_entry_dir(clips_root, entry_id) / REFERENCE_GPKG_BASENAME.replace(".gpkg", ".kml")





def eligible_land_use_workspace_dir(clips_root: Path) -> Path:
    """Stable directory ``clips/eligible`` (eligible SHA is recorded in workspace manifest only)."""

    return Path(clips_root).expanduser().resolve() / "eligible"


def eligible_gpkg_path(clips_root: Path, eligible_sha: str | None = None) -> Path:
    """Path to aggregated eligible GeoPackage under ``clips/eligible`` (SHA argument ignored; kept for callers)."""

    _ = eligible_sha
    return eligible_land_use_workspace_dir(clips_root) / ELIGIBLE_GPKG_BASENAME


def clip_layer_fingerprint_body(
    *,
    role: ClipRole,
    preset_path: str,
    resolved: Path,
    layer: str,
    where: str | None,
    mask_body: str,
    gdb_tree: str,
) -> str:
    root = resolved.expanduser().resolve()
    parts = [
        f"format={CLIP_LAYER_FORMAT}",
        f"role={role}",
        "mask",
        mask_body.rstrip("\n"),
        "layer",
        f"path={preset_path}",
        f"name={layer}",
        f"resolved={root}",
        f"where={where or ''}",
        "gdb_tree",
        gdb_tree.rstrip("\n"),
    ]
    return "\n".join(parts) + "\n"


def clip_layer_sha(body: str) -> str:
    return _sha16(body)


def composite_aoi_fingerprint_body(clip_shas: list[str]) -> str:
    lines = "\n".join(sorted(clip_shas))
    return f"format={COMPOSITE_AOI_FORMAT}\nclips\n{lines}\n"


def _include_config_json(preset: Preset) -> str:
    sorted_groups = sorted(preset_include_groups(preset), key=_gdb_layer_group_sort_key)
    payload = {"include": [_gdb_layer_group_payload(g) for g in sorted_groups]}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _exclude_config_json(preset: Preset) -> str:
    sorted_groups = sorted(preset_exclude_groups(preset), key=_gdb_layer_group_sort_key)
    payload = {"exclude": [_gdb_layer_group_payload(g) for g in sorted_groups]}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def composite_include_fingerprint_body(
    preset: Preset, data_dir: Path, *, aoi_mask_sha: str, gdb_fingerprint_fn: Any
) -> str:
    from peaky_finders.bundle_build import _unique_sorted_include_gdb_roots

    data_dir = Path(data_dir).expanduser().resolve()
    parts = [
        f"format={COMPOSITE_INCLUDE_FORMAT}",
        f"aoi_mask={aoi_mask_sha}",
        "config",
        _include_config_json(preset),
        "gdb_roots",
    ]
    for root in _unique_sorted_include_gdb_roots(preset, data_dir):
        if not root.exists():
            raise FileNotFoundError(f"GDB path not found for include composite fingerprint: {root}")
        parts.append(str(root.resolve()))
        parts.append(gdb_fingerprint_fn(root).rstrip("\n"))
    return "\n".join(parts) + "\n"


def composite_exclude_fingerprint_body(
    preset: Preset, data_dir: Path, *, aoi_mask_sha: str, gdb_fingerprint_fn: Any
) -> str:
    from peaky_finders.bundle_build import _unique_sorted_exclude_gdb_roots

    data_dir = Path(data_dir).expanduser().resolve()
    parts = [
        f"format={COMPOSITE_EXCLUDE_FORMAT}",
        f"aoi_mask={aoi_mask_sha}",
        "config",
        _exclude_config_json(preset),
        "gdb_roots",
    ]
    for root in _unique_sorted_exclude_gdb_roots(preset, data_dir):
        if not root.exists():
            raise FileNotFoundError(f"GDB path not found for exclude composite fingerprint: {root}")
        parts.append(str(root.resolve()))
        parts.append(gdb_fingerprint_fn(root).rstrip("\n"))
    return "\n".join(parts) + "\n"


def eligible_fingerprint_body(*, include_sha: str, exclude_sha: str, bundle_land_digest: str) -> str:
    """Inputs for aggregated eligible polygon (include/exclude composites + bundle-wide land-use tree digest)."""

    return (
        f"format={COMPOSITE_ELIGIBLE_FORMAT}\n"
        f"include={include_sha}\n"
        f"exclude={exclude_sha}\n"
        f"bundle_land_digest={bundle_land_digest}\n"
    )


def plan_clip_build_result(
    *,
    preset: Preset,
    data_dir: Path,
    clips_root: Path,
) -> ClipBuildResult:
    """Resolve clip/composite SHAs and eligible GPKG path without building."""
    from peaky_finders.bundle_build import (
        _file_tree_mtime_size_fingerprint,
        _flatten_gdb_layer_jobs,
        aoi_inputs_fingerprint_body,
        land_use_inputs_fingerprint_body,
    )

    data_dir = Path(data_dir).expanduser().resolve()
    clips_root = Path(clips_root).expanduser().resolve()
    mask_body = aoi_inputs_fingerprint_body(preset, data_dir)
    mask_sha = aoi_mask_sha_from_body(mask_body)
    gdb_fp = _file_tree_mtime_size_fingerprint

    aoi_clip_shas: list[str] = []
    for preset_path, resolved, layer_name, where in _flatten_gdb_layer_jobs(preset_aoi_groups(preset), data_dir):
        body = clip_layer_fingerprint_body(
            role="aoi",
            preset_path=preset_path,
            resolved=resolved,
            layer=layer_name,
            where=where,
            mask_body=mask_body,
            gdb_tree=gdb_fp(resolved),
        )
        aoi_clip_shas.append(clip_layer_sha(body))

    aoi_sha = _sha16(composite_aoi_fingerprint_body(aoi_clip_shas))
    include_sha = _sha16(
        composite_include_fingerprint_body(preset, data_dir, aoi_mask_sha=mask_sha, gdb_fingerprint_fn=gdb_fp)
    )
    exclude_sha = _sha16(
        composite_exclude_fingerprint_body(preset, data_dir, aoi_mask_sha=mask_sha, gdb_fingerprint_fn=gdb_fp)
    )
    bundle_land_digest = _sha16(land_use_inputs_fingerprint_body(preset, data_dir))
    eligible_sha = _sha16(
        eligible_fingerprint_body(
            include_sha=include_sha,
            exclude_sha=exclude_sha,
            bundle_land_digest=bundle_land_digest,
        )
    )
    return ClipBuildResult(
        aoi_sha=aoi_sha,
        include_sha=include_sha,
        exclude_sha=exclude_sha,
        eligible_sha=eligible_sha,
        eligible_gpkg=eligible_gpkg_path(clips_root),
    )


@dataclass(frozen=True)
class PlannedClipLayer:
    """One GDB layer clip under ``clips/layer_jobs/{role}/{stem}/clip.gpkg``."""

    role: ClipRole
    preset_path: str
    layer: str
    where: str | None
    stem: str
    sha: str
    gpkg: Path
    input_files: tuple[Path, ...]


def _plan_clip_layer_job(
    *,
    clips_root: Path,
    role: ClipRole,
    preset_path: str,
    resolved: Path,
    layer_name: str,
    where: str | None,
    mask_body: str,
    gdb_fp: Any,
) -> PlannedClipLayer:
    from peaky_finders.bundle_build import _clip_stem, list_gdb_input_files

    body = clip_layer_fingerprint_body(
        role=role,
        preset_path=preset_path,
        resolved=resolved,
        layer=layer_name,
        where=where,
        mask_body=mask_body,
        gdb_tree=gdb_fp(resolved),
    )
    sha = clip_layer_sha(body)
    stem = _clip_stem(preset_path, layer_name, where)
    return PlannedClipLayer(
        role=role,
        preset_path=preset_path,
        layer=layer_name,
        where=where,
        stem=stem,
        sha=sha,
        gpkg=layer_job_gpkg_path(clips_root, role, stem),
        input_files=list_gdb_input_files(resolved),
    )


def plan_clip_layer_jobs(
    *,
    preset: Preset,
    data_dir: Path,
    clips_root: Path,
) -> tuple[tuple[PlannedClipLayer, ...], ClipBuildResult]:
    """Resolve every clip layer job plus composite SHAs (no builds)."""
    from peaky_finders.bundle_build import (
        _file_tree_mtime_size_fingerprint,
        _flatten_gdb_layer_jobs,
        aoi_inputs_fingerprint_body,
    )

    data_dir = Path(data_dir).expanduser().resolve()
    clips_root = Path(clips_root).expanduser().resolve()
    composites = plan_clip_build_result(preset=preset, data_dir=data_dir, clips_root=clips_root)
    mask_body = aoi_inputs_fingerprint_body(preset, data_dir)
    gdb_fp = _file_tree_mtime_size_fingerprint

    layers: list[PlannedClipLayer] = []
    for role, groups in (
        ("aoi", preset_aoi_groups(preset)),
        ("include", preset_include_groups(preset)),
        ("exclude", preset_exclude_groups(preset)),
    ):
        for preset_path, resolved, layer_name, where in _flatten_gdb_layer_jobs(groups, data_dir):
            if not resolved.exists():
                raise FileNotFoundError(
                    f"{role} GDB not found: {resolved} (preset path {preset_path!r})"
                )
            job = _plan_clip_layer_job(
                clips_root=clips_root,
                role=role,  # type: ignore[arg-type]
                preset_path=preset_path,
                resolved=resolved,
                layer_name=layer_name,
                where=where,
                mask_body=mask_body,
                gdb_fp=gdb_fp,
            )
            layers.append(
                PlannedClipLayer(
                    role=job.role,
                    preset_path=job.preset_path,
                    layer=job.layer,
                    where=job.where,
                    stem=job.stem,
                    sha=job.sha,
                    gpkg=job.gpkg,
                    input_files=job.input_files,
                )
            )

    return tuple(layers), composites


def aoi_mask_sha_from_body(mask_body: str) -> str:
    return _sha16(mask_body)


def _sorted_layer_job_gpkgs_for_clip_shas(
    *,
    preset: Preset,
    role: ClipRole,
    clips_root: Path,
    data_dir: Path,
    mask_body: str,
    clip_shas: list[str],
) -> list[Path]:
    from peaky_finders.bundle_build import _clip_stem, _file_tree_mtime_size_fingerprint, _flatten_gdb_layer_jobs

    gdb_fp = _file_tree_mtime_size_fingerprint
    groups = preset_aoi_groups(preset) if role == "aoi" else preset_include_groups(preset) if role == "include" else preset_exclude_groups(preset)
    want = frozenset(clip_shas)
    by_sha: dict[str, Path] = {}
    for preset_path, resolved, layer_name, where in _flatten_gdb_layer_jobs(groups, data_dir):
        body = clip_layer_fingerprint_body(
            role=role,
            preset_path=preset_path,
            resolved=resolved,
            layer=layer_name,
            where=where,
            mask_body=mask_body,
            gdb_tree=gdb_fp(resolved),
        )
        sha = clip_layer_sha(body)
        if sha not in want:
            continue
        stem = _clip_stem(preset_path, layer_name, where)
        by_sha[sha] = layer_job_gpkg_path(clips_root, role, stem)
    return [by_sha[s] for s in sorted(clip_shas) if s in by_sha]


def bundle_resolve_path(bundle_dir: Path) -> Path:
    return bundle_dir / "resolve.json"


def read_bundle_resolve(bundle_dir: Path) -> dict[str, Any]:
    path = bundle_resolve_path(bundle_dir)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Invalid bundle resolve manifest: {path}")
    fmt = raw.get("format")
    if fmt not in (BUNDLE_RESOLVE_FORMAT, "bundle_resolve/v1"):
        raise ValueError(f"Invalid bundle resolve manifest: {path}")
    return raw


def write_bundle_resolve(
    bundle_dir: Path,
    *,
    clips_root: Path,
    aoi_sha: str,
    include_sha: str,
    exclude_sha: str,
    eligible_sha: str,
    reference: dict[str, str] | None = None,
) -> None:
    bundle_dir.mkdir(parents=True, exist_ok=True)
    clips_root = clips_root.resolve()
    payload: dict[str, Any] = {
        "format": BUNDLE_RESOLVE_FORMAT,
        "clips_root": str(clips_root),
        "aoi": aoi_sha,
        "include": include_sha,
        "exclude": exclude_sha,
        "eligible": eligible_sha,
    }
    if reference:
        payload["reference"] = dict(sorted(reference.items()))
    path = bundle_resolve_path(bundle_dir)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def reference_entry_fingerprint_body(
    ent: GeneralOverlayEntry,
    data_dir: Path,
    *,
    aoi_mask_sha: str,
    gdb_tree: str,
) -> str:
    from peaky_finders.bundle_build import resolve_land_use_gdb_path

    resolved = resolve_land_use_gdb_path(data_dir, ent.path)
    entry_json = json.dumps(_general_overlay_entry_payload(ent), ensure_ascii=False, sort_keys=True)
    return (
        f"format={REFERENCE_ENTRY_FORMAT}\n"
        f"aoi_mask={aoi_mask_sha}\n"
        f"entry\n{entry_json}\n"
        f"resolved={resolved.resolve()}\n"
        "gdb_tree\n"
        f"{gdb_tree.rstrip()}\n"
    )


def reference_entry_sha(body: str) -> str:
    return _sha16(body)


def plan_reference_entries(
    *,
    preset: Preset,
    data_dir: Path,
    clips_root: Path,
) -> dict[str, str]:
    """Map ``bundle.reference`` entry id → content-addressed sha.

    GDB trees are fingerprinted when local paths exist. Missing GDB paths use a sentinel
    body so preset configure succeeds; adding the GDB later yields a different sha and
    invalidates ``bundle resolve`` / downstream dependents.
    """

    from peaky_finders.bundle_build import (
        _file_tree_mtime_size_fingerprint,
        aoi_inputs_fingerprint_body,
        resolve_land_use_gdb_path,
    )

    if not preset_general_overlays(preset):
        return {}
    data_dir = Path(data_dir).expanduser().resolve()
    mask_sha = aoi_mask_sha_from_body(aoi_inputs_fingerprint_body(preset, data_dir))
    gdb_fp = _file_tree_mtime_size_fingerprint
    out: dict[str, str] = {}
    for ent in preset_general_overlays(preset):
        resolved = resolve_land_use_gdb_path(data_dir, ent.path)
        if resolved.exists():
            body = reference_entry_fingerprint_body(
                ent, data_dir, aoi_mask_sha=mask_sha, gdb_tree=gdb_fp(resolved)
            )
        else:
            entry_json = json.dumps(_general_overlay_entry_payload(ent), ensure_ascii=False, sort_keys=True)
            body = (
                f"format={REFERENCE_ENTRY_FORMAT}\n"
                f"aoi_mask={mask_sha}\n"
                f"entry\n{entry_json}\n"
                f"resolved={resolved.resolve()}\n"
                "gdb_tree\n"
                "<missing-reference-gdb>\n"
            )
        out[ent.id] = reference_entry_sha(body)
    return out


def reference_kml_from_bundle_dir(bundle_dir: Path, entry_id: str) -> Path:
    """Sidecar KML for one reference entry (clips cache via ``resolve.json``)."""
    r = read_bundle_resolve(bundle_dir)
    ref = r.get("reference")
    if not isinstance(ref, dict):
        raise KeyError(f"resolve.json has no reference map: {entry_id!r}")
    sha = ref.get(entry_id)
    if not isinstance(sha, str):
        raise KeyError(f"reference entry not in resolve.json: {entry_id!r}")
    root = Path(str(r["clips_root"])).resolve()
    return reference_kml_path(root, entry_id)


def eligible_gpkg_from_bundle_dir(bundle_dir: Path) -> Path:
    r = read_bundle_resolve(bundle_dir)
    root = Path(str(r["clips_root"])).resolve()
    return eligible_gpkg_path(root)


def composite_kml_from_bundle_dir(bundle_dir: Path, role: Literal["aoi", "include", "exclude"]) -> Path:
    r = read_bundle_resolve(bundle_dir)
    root = Path(str(r["clips_root"])).resolve()
    _ = str(r[role])
    return composite_workspace_dir(root, role) / UNION_GPKG_BASENAME.replace(".gpkg", ".kml")


def read_composite_exclude_manifest(clips_root: Path) -> list[str]:
    """Clip SHAs referenced by the composite exclude union manifest (sorted order)."""
    path = composite_workspace_dir(clips_root, "exclude") / MANIFEST_BASENAME
    if not path.is_file():
        return []
    raw_obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw_obj, dict) or raw_obj.get("format") != COMPOSITE_EXCLUDE_FORMAT:
        return []
    clips = raw_obj.get("clips")
    if not isinstance(clips, list):
        return []
    return sorted(str(x) for x in clips if isinstance(x, str))


def read_composite_include_manifest(clips_root: Path) -> list[str]:
    """Clip SHAs referenced by the composite include union manifest (sorted order)."""
    path = composite_workspace_dir(clips_root, "include") / MANIFEST_BASENAME
    if not path.is_file():
        return []
    raw_obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw_obj, dict) or raw_obj.get("format") != COMPOSITE_INCLUDE_FORMAT:
        return []
    clips = raw_obj.get("clips")
    if not isinstance(clips, list):
        return []
    return sorted(str(x) for x in clips if isinstance(x, str))


def list_exclude_layer_kmz_entries(
    bundle_dir: Path,
    *,
    preset: Preset,
    data_dir: Path,
) -> list[tuple[str, Path, str]]:
    """Sidecar exclude layers for KMZ: ``(NetworkLink label, disk KML path, KMZ arcname)``.

    Requires ``bundle/resolve.json``.
    """
    from peaky_finders.bundle_build import (
        _clip_stem,
        _file_tree_mtime_size_fingerprint,
        _flatten_gdb_layer_jobs,
        _kml_label_gdb_job,
        aoi_inputs_fingerprint_body,
    )

    plc = preset.bundle
    if plc is None:
        return []

    data_dir = Path(data_dir).expanduser().resolve()
    bundle_dir = Path(bundle_dir).expanduser().resolve()

    resolve_path = bundle_resolve_path(bundle_dir)
    if not resolve_path.is_file():
        raise FileNotFoundError(
            f"bundle resolve manifest missing: {resolve_path}. "
            "Bundle resolve manifest missing — ensure bundle GDB paths in preset are valid."
        )

    rows: list[tuple[str, Path, str]] = []

    resolve = read_bundle_resolve(bundle_dir)
    clips_root = Path(str(resolve["clips_root"])).resolve()
    manifest_shas = set(read_composite_exclude_manifest(clips_root))
    if not manifest_shas:
        return []

    mask_body = aoi_inputs_fingerprint_body(preset, data_dir)
    gdb_fp = _file_tree_mtime_size_fingerprint

    for preset_path, resolved, layer_name, where in _flatten_gdb_layer_jobs(preset_exclude_groups(preset), data_dir):
        body = clip_layer_fingerprint_body(
            role="exclude",
            preset_path=preset_path,
            resolved=resolved,
            layer=layer_name,
            where=where,
            mask_body=mask_body,
            gdb_tree=gdb_fp(resolved),
        )
        sha = clip_layer_sha(body)
        if sha not in manifest_shas:
            continue
        stem = _clip_stem(preset_path, layer_name, where)
        kml_disk = layer_job_kml_path(clips_root, "exclude", stem)
        if not kml_disk.is_file():
            continue
        label = _kml_label_gdb_job(preset_path, layer_name)
        arcname = f"exclude/layers/{stem}.kml"
        rows.append((label, kml_disk.resolve(), arcname))
    rows.sort(key=lambda t: t[2])
    return rows


def list_include_layer_kmz_entries(
    bundle_dir: Path,
    *,
    preset: Preset,
    data_dir: Path,
) -> list[tuple[str, Path, str]]:
    """Sidecar include layers for KMZ: ``(NetworkLink label, disk KML path, KMZ arcname)``.

    Requires ``bundle/resolve.json``.
    """
    from peaky_finders.bundle_build import (
        _clip_stem,
        _file_tree_mtime_size_fingerprint,
        _flatten_gdb_layer_jobs,
        _kml_label_gdb_job,
        aoi_inputs_fingerprint_body,
    )

    plc = preset.bundle
    if plc is None:
        return []

    data_dir = Path(data_dir).expanduser().resolve()
    bundle_dir = Path(bundle_dir).expanduser().resolve()

    resolve_path = bundle_resolve_path(bundle_dir)
    if not resolve_path.is_file():
        raise FileNotFoundError(
            f"bundle resolve manifest missing: {resolve_path}. "
            "Bundle resolve manifest missing — ensure bundle GDB paths in preset are valid."
        )

    rows: list[tuple[str, Path, str]] = []
    resolve = read_bundle_resolve(bundle_dir)
    clips_root = Path(str(resolve["clips_root"])).resolve()
    manifest_shas = set(read_composite_include_manifest(clips_root))
    if not manifest_shas:
        return []

    mask_body = aoi_inputs_fingerprint_body(preset, data_dir)
    gdb_fp = _file_tree_mtime_size_fingerprint

    for preset_path, resolved, layer_name, where in _flatten_gdb_layer_jobs(preset_include_groups(preset), data_dir):
        body = clip_layer_fingerprint_body(
            role="include",
            preset_path=preset_path,
            resolved=resolved,
            layer=layer_name,
            where=where,
            mask_body=mask_body,
            gdb_tree=gdb_fp(resolved),
        )
        sha = clip_layer_sha(body)
        if sha not in manifest_shas:
            continue
        stem = _clip_stem(preset_path, layer_name, where)
        kml_disk = layer_job_kml_path(clips_root, "include", stem)
        if not kml_disk.is_file():
            continue
        label = _kml_label_gdb_job(preset_path, layer_name)
        arcname = f"include/layers/{stem}.kml"
        rows.append((label, kml_disk.resolve(), arcname))
    rows.sort(key=lambda t: t[2])
    return rows




def sync_eligible_include_layer_slices(
    *,
    preset: Preset,
    data_dir: Path,
    clips_root: Path,
    mask_body: str,
    eligible_gpkg: Path,
    kml_overlay: BundleKmlOverlayStyles | None,
    verbose_log: Callable[[str], None] | None = None,
) -> None:
    """Write ``clips/eligible/layers/<stem>.kml``: global eligible ∩ each include clip (post-exclusions)."""

    from peaky_finders.bundle_build import (
        eligible_land_slice_from_include_clip_gpkg,
        _clip_stem,
        _file_tree_mtime_size_fingerprint,
        _flatten_gdb_layer_jobs,
        _kml_label_gdb_job,
        _write_geodataframe_kml,
    )

    eligible_gpkg = eligible_gpkg.expanduser().resolve()
    if not eligible_gpkg.is_file():
        return

    clips_root = Path(clips_root).expanduser().resolve()
    data_dir_res = Path(data_dir).expanduser().resolve()
    layers_root = eligible_gpkg.parent / ELIGIBLE_SLICE_LAYERS_SUBDIR
    gdb_fp = _file_tree_mtime_size_fingerprint

    try:
        g_el = gpd.read_file(eligible_gpkg, layer=ELIGIBLE_LAYER)
    except Exception:
        if layers_root.is_dir():
            shutil.rmtree(layers_root)
        return

    if g_el.empty or g_el.geometry.is_empty.iloc[0]:
        if layers_root.is_dir():
            shutil.rmtree(layers_root)
        return

    geom_ll = make_valid(g_el.geometry.iloc[0])
    if geom_ll.is_empty:
        if layers_root.is_dir():
            shutil.rmtree(layers_root)
        return

    manifest_shas = set(read_composite_include_manifest(clips_root))

    wanted_stems: set[str] = set()
    layers_root.mkdir(parents=True, exist_ok=True)

    for preset_path, resolved, layer_name, where in _flatten_gdb_layer_jobs(preset_include_groups(preset), data_dir_res):
        body = clip_layer_fingerprint_body(
            role="include",
            preset_path=preset_path,
            resolved=resolved,
            layer=layer_name,
            where=where,
            mask_body=mask_body,
            gdb_tree=gdb_fp(resolved),
        )
        sha = clip_layer_sha(body)
        if sha not in manifest_shas:
            if verbose_log:
                verbose_log(f"slice {preset_path}::{layer_name}: not in include manifest (skip)")
            continue
        stem = _clip_stem(preset_path, layer_name, where)
        wanted_stems.add(stem)
        clip_disk = layer_job_gpkg_path(clips_root, "include", stem)
        kml_out = layers_root / f"{stem}.kml"
        png_out = kml_out.with_suffix(".png")

        if not clip_disk.is_file():
            if verbose_log:
                verbose_log(f"slice {stem}: include clip missing (skip)")
            kml_out.unlink(missing_ok=True)
            png_out.unlink(missing_ok=True)
            continue

        try:
            clip_disk.stat()
        except OSError:
            kml_out.unlink(missing_ok=True)
            png_out.unlink(missing_ok=True)
            continue

        layer_label = _kml_label_gdb_job(preset_path, layer_name)
        slice_df = eligible_land_slice_from_include_clip_gpkg(geom_ll, clip_disk)

        if slice_df.empty or slice_df.geometry.is_empty.all():
            if verbose_log:
                verbose_log(f"slice {stem}: empty after eligible ∩ include (skip)")
            kml_out.unlink(missing_ok=True)
            png_out.unlink(missing_ok=True)
            continue

        _write_geodataframe_kml(slice_df, kml_out, layer_label=layer_label, kml_overlay=kml_overlay)
        if verbose_log:
            verbose_log(f"slice {stem}: wrote {kml_out.name}")

    for stray in layers_root.glob("*.kml"):
        if stray.stem not in wanted_stems:
            if verbose_log:
                verbose_log(f"slice {stray.stem}: removed stale KML")
            stray.unlink(missing_ok=True)
            stray.with_suffix(".png").unlink(missing_ok=True)


def list_eligible_layer_kmz_entries(
    bundle_dir: Path,
    *,
    preset: Preset,
    data_dir: Path,
) -> list[tuple[str, Path, str]]:
    """Eligible slice KMZ triples mapped to arcnames ``eligible/layers/<stem>.kml``."""
    from peaky_finders.bundle_build import (
        _clip_stem,
        _file_tree_mtime_size_fingerprint,
        _flatten_gdb_layer_jobs,
        _kml_label_gdb_job,
        aoi_inputs_fingerprint_body,
    )

    plc = preset.bundle
    if plc is None:
        return []

    bundle_dir = Path(bundle_dir).expanduser().resolve()
    data_dir = Path(data_dir).expanduser().resolve()

    rows: list[tuple[str, Path, str]] = []

    resolve_path = bundle_resolve_path(bundle_dir)
    if not resolve_path.is_file():
        raise FileNotFoundError(
            f"bundle resolve manifest missing: {resolve_path}. "
            "Bundle resolve manifest missing — ensure bundle GDB paths in preset are valid."
        )

    resolve = read_bundle_resolve(bundle_dir)
    clips_root = Path(str(resolve["clips_root"])).resolve()

    gdb_fp = _file_tree_mtime_size_fingerprint

    eligible_gpkg = eligible_gpkg_path(clips_root)
    layers_root = eligible_gpkg.parent / ELIGIBLE_SLICE_LAYERS_SUBDIR
    mask_body = aoi_inputs_fingerprint_body(preset, data_dir)
    manifest_shas = set(read_composite_include_manifest(clips_root))
    if not manifest_shas:
        return []

    for preset_path, resolved, layer_name, where in _flatten_gdb_layer_jobs(preset_include_groups(preset), data_dir):
        body = clip_layer_fingerprint_body(
            role="include",
            preset_path=preset_path,
            resolved=resolved,
            layer=layer_name,
            where=where,
            mask_body=mask_body,
            gdb_tree=gdb_fp(resolved),
        )
        sha = clip_layer_sha(body)
        if sha not in manifest_shas:
            continue
        stem = _clip_stem(preset_path, layer_name, where)
        kml_disk = layers_root / f"{stem}.kml"
        if not kml_disk.is_file():
            continue
        base = _kml_label_gdb_job(preset_path, layer_name)
        label = f"Eligible: {base}"
        arcname = f"eligible/layers/{stem}.kml"
        rows.append((label, kml_disk.resolve(), arcname))

    rows.sort(key=lambda t: t[2])
    return rows


def _write_manifest(path: Path, *, fmt: str, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {"format": fmt, **payload}
    path.write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _read_manifest(path: Path, expected_format: str) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, dict) or raw.get("format") != expected_format:
        return None
    return raw


@dataclass
class ClipBuildResult:
    aoi_sha: str
    include_sha: str
    exclude_sha: str
    eligible_sha: str
    eligible_gpkg: Path


def _union_wgs84_from_job_paths(paths: list[Path]) -> gpd.GeoDataFrame:
    from peaky_finders.bundle_build import _sanitize_collection

    pieces_ll: list[BaseGeometry] = []
    for pth in sorted(paths, key=lambda p: str(p)):
        gdf = gpd.read_file(pth)
        if gdf.empty:
            continue
        pieces_ll.extend(gdf.geometry.tolist())
    u_ll = _sanitize_collection(pieces_ll if pieces_ll else [])
    return gpd.GeoDataFrame(geometry=[u_ll], crs="EPSG:4326")


def _union_include_from_job_paths(paths: list[Path]) -> gpd.GeoDataFrame:
    from peaky_finders.bundle_build import _sanitize_collection

    pieces: list[BaseGeometry] = []
    for pth in sorted(paths, key=lambda p: str(p)):
        gdf = gpd.read_file(pth)
        if gdf.empty:
            continue
        g3857 = gdf.to_crs("EPSG:3857")
        pieces.extend(g3857.geometry.tolist())
    u = _sanitize_collection(pieces)
    g_union = gpd.GeoDataFrame(geometry=[u], crs="EPSG:3857")
    return g_union.to_crs("EPSG:4326")


def _write_composite_union(
    gdf: gpd.GeoDataFrame,
    out_gpkg: Path,
    *,
    gpkg_layer: str,
    layer_label: str,
    kml_overlay: BundleKmlOverlayStyles | None,
) -> None:
    from peaky_finders.bundle_build import _write_geodataframe_gpkg_and_kml

    _write_geodataframe_gpkg_and_kml(
        gdf,
        out_gpkg,
        gpkg_layer=gpkg_layer,
        layer_label=layer_label,
        kml_overlay=kml_overlay,
    )


def build_clip_layer(
    clips_root: Path,
    *,
    role: ClipRole,
    preset_path: str,
    resolved: Path,
    layer: str,
    where: str | None,
    mask_body: str,
    gdb_tree: str,
    aoi_poly_4326: BaseGeometry,
    kml_label: str,
    kml_overlay: BundleKmlOverlayStyles | None,
    store_crs_3857: bool,
    verbose_log: Callable[[str], None] | None,
    progress_log: Callable[[str], None] | None,
) -> str | None:
    """Build one clip layer job; return clip_sha, or ``None`` if clip is empty (exclude only)."""

    from peaky_finders.bundle_build import (
        _read_and_clip_gdb_layer_to_aoi,
        _clip_stem,
        _write_geodataframe_gpkg_and_kml,
    )

    clips_root = Path(clips_root).expanduser().resolve()
    body = clip_layer_fingerprint_body(
        role=role,
        preset_path=preset_path,
        resolved=resolved,
        layer=layer,
        where=where,
        mask_body=mask_body,
        gdb_tree=gdb_tree,
    )
    sha = clip_layer_sha(body)
    stem = _clip_stem(preset_path, layer, where)
    job_dir = clip_job_dir(clips_root, role, stem)
    out = job_dir / CLIP_GPKG_BASENAME
    ws = job_dir / CLIP_JOB_WORKSPACE_NAME

    with _clips_cache_exclusive_lock(clips_root):
        if job_dir.is_dir():
            shutil.rmtree(job_dir)

        if progress_log:
            progress_log(f"clip {role} read+clip {preset_path}::{layer} …")
        gdf, clipped = _read_and_clip_gdb_layer_to_aoi(
            preset_path, resolved, layer, aoi_poly_4326, kind=role, where=where
        )
        if clipped.empty:
            if verbose_log:
                verbose_log(f"clip {role} {preset_path}::{layer}: empty after clip (skip)")
            return None

        if store_crs_3857:
            out_gdf = clipped.to_crs("EPSG:3857")
        else:
            out_gdf = clipped.to_crs("EPSG:4326")

        job_dir.mkdir(parents=True, exist_ok=True)
        if progress_log:
            progress_log(f"clip/{role}/{stem}: write ({len(out_gdf):,} features) …")
        _write_geodataframe_gpkg_and_kml(
            out_gdf,
            out,
            gpkg_layer="features",
            layer_label=kml_label,
            kml_overlay=kml_overlay,
        )
        _write_manifest(ws, fmt=CLIP_JOB_WORKSPACE_FMT, payload={"clip_sha": sha})
        if verbose_log:
            verbose_log(
                f"clip {role} {preset_path}::{layer}: {len(gdf):,} native → {len(clipped):,} clipped → {stem}"
            )
        return sha


def _clip_shas_for_composite(
    *,
    preset: Preset,
    data_dir: Path,
    clips_root: Path,
    mask_body: str,
    role: ClipRole,
) -> list[str]:
    from peaky_finders.bundle_build import _clip_stem, _file_tree_mtime_size_fingerprint, _flatten_gdb_layer_jobs

    gdb_fp = _file_tree_mtime_size_fingerprint
    groups = preset_aoi_groups(preset) if role == "aoi" else preset_include_groups(preset) if role == "include" else preset_exclude_groups(preset)
    shas: list[str] = []
    for preset_path, resolved, layer_name, where in _flatten_gdb_layer_jobs(groups, data_dir):
        if not resolved.exists():
            raise FileNotFoundError(f"{role} GDB not found: {resolved} (preset path {preset_path!r})")
        body = clip_layer_fingerprint_body(
            role=role,
            preset_path=preset_path,
            resolved=resolved,
            layer=layer_name,
            where=where,
            mask_body=mask_body,
            gdb_tree=gdb_fp(resolved),
        )
        sha = clip_layer_sha(body)
        stem = _clip_stem(preset_path, layer_name, where)
        gpkg = layer_job_gpkg_path(clips_root, role, stem)
        if role == "exclude":
            if gpkg.is_file():
                shas.append(sha)
        elif gpkg.is_file():
            shas.append(sha)
        else:
            raise FileNotFoundError(
                f"{role} clip missing: {gpkg} (bundle clip for {role}/{layer_name} not materialized)"
            )
    return shas


def build_composite_aoi(
    *,
    preset: Preset,
    data_dir: Path,
    clips_root: Path,
    mask_body: str,
    kml_overlay: BundleKmlOverlayStyles | None,
    verbose_log: Callable[[str], None] | None = None,
    progress_log: Callable[[str], None] | None = None,
) -> str:
    _ = verbose_log, progress_log
    clips_root = Path(clips_root).expanduser().resolve()
    data_dir = Path(data_dir).expanduser().resolve()

    aoi_clip_shas = _clip_shas_for_composite(
        preset=preset, data_dir=data_dir, clips_root=clips_root, mask_body=mask_body, role="aoi"
    )
    if not aoi_clip_shas:
        raise ValueError("AOI clip set is empty")

    aoi_sha = _sha16(composite_aoi_fingerprint_body(aoi_clip_shas))
    aoi_workspace = composite_workspace_dir(clips_root, "aoi")
    aoi_gpkg = aoi_workspace / UNION_GPKG_BASENAME
    aoi_ws_manifest = aoi_workspace / COMPOSITE_WORKSPACE_JSON_NAME

    with _clips_cache_exclusive_lock(clips_root):
        if aoi_workspace.is_dir():
            shutil.rmtree(aoi_workspace)
        aoi_workspace.mkdir(parents=True, exist_ok=True)
        aoi_job_paths = _sorted_layer_job_gpkgs_for_clip_shas(
            preset=preset,
            role="aoi",
            clips_root=clips_root,
            data_dir=data_dir,
            mask_body=mask_body,
            clip_shas=aoi_clip_shas,
        )
        g_aoi = _union_wgs84_from_job_paths(aoi_job_paths)
        if g_aoi.empty or g_aoi.geometry.is_empty.iloc[0]:
            raise ValueError("AOI composite union is empty")
        _write_composite_union(
            g_aoi,
            aoi_gpkg,
            gpkg_layer="aoi",
            layer_label="aoi",
            kml_overlay=kml_overlay,
        )
        _write_manifest(
            aoi_workspace / MANIFEST_BASENAME,
            fmt=COMPOSITE_AOI_FORMAT,
            payload={"clips": sorted(aoi_clip_shas)},
        )
        _write_manifest(
            aoi_ws_manifest,
            fmt=COMPOSITE_WORKSPACE_FMT,
            payload={"composite_sha": aoi_sha, "kind": "aoi"},
        )
    return aoi_sha


def build_composite_include(
    *,
    preset: Preset,
    data_dir: Path,
    clips_root: Path,
    mask_body: str,
    kml_overlay: BundleKmlOverlayStyles | None,
    verbose_log: Callable[[str], None] | None = None,
    progress_log: Callable[[str], None] | None = None,
) -> str:
    _ = verbose_log, progress_log
    from peaky_finders.bundle_build import _file_tree_mtime_size_fingerprint

    clips_root = Path(clips_root).expanduser().resolve()
    data_dir = Path(data_dir).expanduser().resolve()
    mask_sha = aoi_mask_sha_from_body(mask_body)
    gdb_fp = _file_tree_mtime_size_fingerprint

    include_clip_shas = _clip_shas_for_composite(
        preset=preset, data_dir=data_dir, clips_root=clips_root, mask_body=mask_body, role="include"
    )
    include_sha = _sha16(
        composite_include_fingerprint_body(preset, data_dir, aoi_mask_sha=mask_sha, gdb_fingerprint_fn=gdb_fp)
    )
    include_workspace = composite_workspace_dir(clips_root, "include")
    include_gpkg = include_workspace / UNION_GPKG_BASENAME
    include_ws_manifest = include_workspace / COMPOSITE_WORKSPACE_JSON_NAME

    with _clips_cache_exclusive_lock(clips_root):
        if include_workspace.is_dir():
            shutil.rmtree(include_workspace)
        include_workspace.mkdir(parents=True, exist_ok=True)
        inc_job_paths = _sorted_layer_job_gpkgs_for_clip_shas(
            preset=preset,
            role="include",
            clips_root=clips_root,
            data_dir=data_dir,
            mask_body=mask_body,
            clip_shas=include_clip_shas,
        )
        g_inc = _union_include_from_job_paths(inc_job_paths)
        _write_composite_union(
            g_inc,
            include_gpkg,
            gpkg_layer="include",
            layer_label="include",
            kml_overlay=kml_overlay,
        )
        _write_manifest(
            include_workspace / MANIFEST_BASENAME,
            fmt=COMPOSITE_INCLUDE_FORMAT,
            payload={"clips": sorted(include_clip_shas), "aoi_mask": mask_sha},
        )
        _write_manifest(
            include_ws_manifest,
            fmt=COMPOSITE_WORKSPACE_FMT,
            payload={"composite_sha": include_sha, "kind": "include"},
        )
    return include_sha


def build_composite_exclude(
    *,
    preset: Preset,
    data_dir: Path,
    clips_root: Path,
    mask_body: str,
    kml_overlay: BundleKmlOverlayStyles | None,
    verbose_log: Callable[[str], None] | None = None,
    progress_log: Callable[[str], None] | None = None,
) -> str:
    _ = verbose_log, progress_log
    from peaky_finders.bundle_build import _file_tree_mtime_size_fingerprint

    clips_root = Path(clips_root).expanduser().resolve()
    data_dir = Path(data_dir).expanduser().resolve()
    mask_sha = aoi_mask_sha_from_body(mask_body)
    gdb_fp = _file_tree_mtime_size_fingerprint

    exclude_clip_shas = _clip_shas_for_composite(
        preset=preset, data_dir=data_dir, clips_root=clips_root, mask_body=mask_body, role="exclude"
    )
    exclude_sha = _sha16(
        composite_exclude_fingerprint_body(preset, data_dir, aoi_mask_sha=mask_sha, gdb_fingerprint_fn=gdb_fp)
    )
    exclude_workspace = composite_workspace_dir(clips_root, "exclude")
    exclude_gpkg = exclude_workspace / UNION_GPKG_BASENAME
    exclude_ws_manifest = exclude_workspace / COMPOSITE_WORKSPACE_JSON_NAME

    with _clips_cache_exclusive_lock(clips_root):
        if exclude_workspace.is_dir():
            shutil.rmtree(exclude_workspace)
        exclude_workspace.mkdir(parents=True, exist_ok=True)
        if exclude_clip_shas:
            exc_job_paths = _sorted_layer_job_gpkgs_for_clip_shas(
                preset=preset,
                role="exclude",
                clips_root=clips_root,
                data_dir=data_dir,
                mask_body=mask_body,
                clip_shas=exclude_clip_shas,
            )
            g_exc = _union_wgs84_from_job_paths(exc_job_paths)
        else:
            g_exc = gpd.GeoDataFrame(geometry=[Polygon()], crs="EPSG:4326")
        _write_composite_union(
            g_exc,
            exclude_gpkg,
            gpkg_layer="exclude",
            layer_label="exclude",
            kml_overlay=kml_overlay,
        )
        _write_manifest(
            exclude_workspace / MANIFEST_BASENAME,
            fmt=COMPOSITE_EXCLUDE_FORMAT,
            payload={"clips": sorted(exclude_clip_shas), "aoi_mask": mask_sha},
        )
        _write_manifest(
            exclude_ws_manifest,
            fmt=COMPOSITE_WORKSPACE_FMT,
            payload={"composite_sha": exclude_sha, "kind": "exclude"},
        )
    return exclude_sha


def build_eligible_workspace(
    *,
    preset: Preset,
    data_dir: Path,
    clips_root: Path,
    mask_body: str,
    kml_overlay: BundleKmlOverlayStyles | None,
    verbose_log: Callable[[str], None] | None = None,
    progress_log: Callable[[str], None] | None = None,
) -> ClipBuildResult:
    _ = progress_log
    from peaky_finders.bundle_build import (
        _file_tree_mtime_size_fingerprint,
        build_eligible_land_use_gdf,
        land_use_inputs_fingerprint_body,
    )

    def _vlog(msg: str) -> None:
        if verbose_log is not None:
            verbose_log(msg)

    clips_root = Path(clips_root).expanduser().resolve()
    data_dir = Path(data_dir).expanduser().resolve()
    mask_sha = aoi_mask_sha_from_body(mask_body)
    gdb_fp = _file_tree_mtime_size_fingerprint

    include_sha = _sha16(
        composite_include_fingerprint_body(preset, data_dir, aoi_mask_sha=mask_sha, gdb_fingerprint_fn=gdb_fp)
    )
    exclude_sha = _sha16(
        composite_exclude_fingerprint_body(preset, data_dir, aoi_mask_sha=mask_sha, gdb_fingerprint_fn=gdb_fp)
    )
    bundle_land_digest = _sha16(land_use_inputs_fingerprint_body(preset, data_dir))
    eligible_sha = _sha16(
        eligible_fingerprint_body(
            include_sha=include_sha,
            exclude_sha=exclude_sha,
            bundle_land_digest=bundle_land_digest,
        )
    )
    _vlog(
        f"fingerprints include_sha={include_sha} exclude_sha={exclude_sha} "
        f"eligible_sha={eligible_sha} bundle_land={bundle_land_digest}"
    )

    include_gpkg = composite_union_gpkg(clips_root, "include")
    exclude_gpkg = composite_union_gpkg(clips_root, "exclude")
    if not include_gpkg.is_file():
        raise FileNotFoundError(f"Include composite missing: {include_gpkg}")
    if not exclude_gpkg.is_file():
        raise FileNotFoundError(f"Exclude composite missing: {exclude_gpkg}")

    ew_dir = eligible_land_use_workspace_dir(clips_root)
    eligible_gpkg = ew_dir / ELIGIBLE_GPKG_BASENAME
    manifest_path = ew_dir / ELIGIBLE_WORKSPACE_MANIFEST_NAME

    with _clips_cache_exclusive_lock(clips_root):
        if ew_dir.is_dir():
            _vlog(f"replacing eligible workspace {ew_dir}")
            shutil.rmtree(ew_dir)
        ew_dir.mkdir(parents=True, exist_ok=True)

        _vlog(f"reading include composite {include_gpkg}")
        include_union = gpd.read_file(include_gpkg, layer="include")
        _vlog(f"reading exclude composite {exclude_gpkg}")
        exclude_union = gpd.read_file(exclude_gpkg, layer="exclude")
        _vlog(
            f"include union {len(include_union)} feature(s); "
            f"exclude union {len(exclude_union)} feature(s)"
        )
        eligible_gdf = build_eligible_land_use_gdf(include_union, exclude_union)
        if eligible_gdf.empty or eligible_gdf.geometry.is_empty.all():
            _vlog("eligible geometry empty after include − exclude")
        else:
            minx, miny, maxx, maxy = map(float, eligible_gdf.total_bounds)
            _vlog(f"eligible geometry bounds (4326)=({minx},{miny},{maxx},{maxy})")
        _write_composite_union(
            eligible_gdf,
            eligible_gpkg,
            gpkg_layer=ELIGIBLE_LAYER,
            layer_label=ELIGIBLE_LAYER,
            kml_overlay=kml_overlay,
        )
        _vlog(f"wrote {eligible_gpkg}")
        _write_manifest(
            manifest_path,
            fmt=ELIGIBLE_WORKSPACE_MANIFEST_FMT,
            payload={
                "eligible_sha": eligible_sha,
                "include_sha": include_sha,
                "exclude_sha": exclude_sha,
                "bundle_land_digest": bundle_land_digest,
            },
        )
        _vlog(f"wrote manifest {manifest_path.name}")
        _vlog("syncing eligible include layer slices")
        sync_eligible_include_layer_slices(
            preset=preset,
            data_dir=data_dir,
            clips_root=clips_root,
            mask_body=mask_body,
            eligible_gpkg=eligible_gpkg,
            kml_overlay=kml_overlay,
            verbose_log=verbose_log,
        )

    aoi_sha = _sha16(
        composite_aoi_fingerprint_body(
            _clip_shas_for_composite(
                preset=preset, data_dir=data_dir, clips_root=clips_root, mask_body=mask_body, role="aoi"
            )
        )
    )
    return ClipBuildResult(
        aoi_sha=aoi_sha,
        include_sha=include_sha,
        exclude_sha=exclude_sha,
        eligible_sha=eligible_sha,
        eligible_gpkg=eligible_gpkg,
    )


def build_reference_entry(
    *,
    entry_id: str,
    preset: Preset,
    data_dir: Path,
    clips_root: Path,
    aoi_sha: str,
    kml_overlay: BundleKmlOverlayStyles | None,
    verbose_log: Callable[[str], None] | None = None,
    progress_log: Callable[[str], None] | None = None,
) -> str:
    """Build ``clips/reference/<entry_id>/`` for one ``bundle.reference`` entry."""

    import pandas as pd

    from peaky_finders.bundle_build import (
        _flatten_gdb_layer_jobs,
        _overlay_for_reference_entry,
        _read_and_clip_gdb_layer_to_aoi,
        _write_geodataframe_gpkg_and_kml,
    )

    if not preset_general_overlays(preset):
        raise ValueError("preset has no general_overlay maps")

    want = entry_id.strip()
    ent = next((e for e in preset_general_overlays(preset) if e.id == want), None)
    if ent is None:
        known = [e.id for e in preset_general_overlays(preset)]
        raise KeyError(f"unknown reference id {want!r} (configured: {', '.join(repr(k) for k in known)})")

    clips_root = Path(clips_root).expanduser().resolve()
    data_dir = Path(data_dir).expanduser().resolve()
    entry_sha = plan_reference_entries(preset=preset, data_dir=data_dir, clips_root=clips_root)[want]

    aoi_gpkg = composite_union_gpkg(clips_root, "aoi", aoi_sha)
    if not aoi_gpkg.is_file():
        raise FileNotFoundError(f"AOI composite missing: {aoi_gpkg}")
    aoi_poly = make_valid(gpd.read_file(aoi_gpkg, layer="aoi").geometry.iloc[0])

    ref_dir = reference_entry_dir(clips_root, ent.id)
    ws_ref = ref_dir / REFERENCE_WORKSPACE_JSON_NAME
    out_gpkg = reference_gpkg_path(clips_root, ent.id)
    empty_marker = ref_dir / REFERENCE_EMPTY_MARKER

    with _clips_cache_exclusive_lock(clips_root):
        if ref_dir.is_dir():
            shutil.rmtree(ref_dir)
        ref_dir.mkdir(parents=True, exist_ok=True)

        jobs = _flatten_gdb_layer_jobs(
            [GdbLayerGroup(path=ent.path, layers=ent.layers)],
            data_dir,
        )
        pieces_ll: list[gpd.GeoDataFrame] = []
        n_j = len(jobs)
        for jidx, (preset_path, resolved, layer_name, where) in enumerate(jobs, start=1):
            if not resolved.exists():
                raise FileNotFoundError(f"Reference GDB not found: {resolved} (preset path {preset_path!r})")
            if progress_log:
                progress_log(f"reference [{ent.id}] [{jidx}/{n_j}] read+clip {preset_path}::{layer_name} …")
            _gdf, clipped = _read_and_clip_gdb_layer_to_aoi(
                preset_path, resolved, layer_name, aoi_poly, kind="reference", where=where
            )
            clipped_ll = clipped.to_crs("EPSG:4326")
            if not clipped_ll.empty:
                pieces_ll.append(clipped_ll)
            if verbose_log:
                verbose_log(
                    f"reference {ent.id} {preset_path}::{layer_name}: "
                    f"{len(_gdf):,} native → {len(clipped_ll):,} clipped"
                )

        if not pieces_ll:
            if verbose_log:
                verbose_log(f"reference [{ent.id}]: empty after clip → {ref_dir}/.empty")
            empty_marker.write_text("1\n", encoding="utf-8")
            _write_manifest(ws_ref, fmt=REFERENCE_WORKSPACE_FMT, payload={"reference_sha": entry_sha, "id": ent.id})
            return entry_sha

        merged = gpd.GeoDataFrame(pd.concat(pieces_ll, ignore_index=True), crs="EPSG:4326")
        overlay = _overlay_for_reference_entry(ent, preset.bundle or BundleConfig())
        if progress_log:
            progress_log(f"{ref_dir.name}: write reference.gpkg + kml …")
        _write_geodataframe_gpkg_and_kml(
            merged,
            out_gpkg,
            gpkg_layer=REFERENCE_GPKG_LAYER,
            layer_label=ent.id,
            kml_overlay=overlay,
        )
        if verbose_log:
            verbose_log(f"reference [{ent.id}]: wrote {out_gpkg}")
        _write_manifest(ws_ref, fmt=REFERENCE_WORKSPACE_FMT, payload={"reference_sha": entry_sha, "id": ent.id})
    return entry_sha

