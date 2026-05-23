"""Content-addressed clip cache under ``<preset-dir>/build/clips/``.

Layout::

    clips/clip/<clip_sha>/clip.{gpkg,kml,png}
    clips/aoi/<aoi_sha>/union.{gpkg,kml,png} + manifest.json
    clips/include|<exclude>/<sha>/union.{gpkg,kml,png} + manifest.json
    clips/eligible/<eligible_sha>/eligible_land_use.{gpkg,kml,png}
    clips/eligible/<eligible_sha>/layers/<stem>.{kml,png}  (per-include eligible ∩ global eligible)
    clips/reference/<entry_sha>/reference.{gpkg,kml,png} or .empty

Job workspaces live at ``<preset-dir>/build/bundles/<job_sha>/`` with ``resolve.json`` pointing into ``clips/``.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

import geopandas as gpd
from shapely import make_valid
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry

from peaky_finders.sites_job import (
    BundleConfig,
    BundleKmlOverlayStyles,
    BundleReferenceLayerEntry,
    GdbLayerGroup,
    Preset,
    _gdb_layer_group_payload,
    _reference_entry_payload,
)

CLIP_LAYER_FORMAT = "clip_layer/v1"
COMPOSITE_AOI_FORMAT = "composite_aoi/v1"
COMPOSITE_INCLUDE_FORMAT = "composite_include/v1"
COMPOSITE_EXCLUDE_FORMAT = "composite_exclude/v1"
COMPOSITE_ELIGIBLE_FORMAT = "composite_eligible/v1"
REFERENCE_ENTRY_FORMAT = "reference_entry/v1"
BUNDLE_RESOLVE_FORMAT = "bundle_resolve/v2"

MANIFEST_BASENAME = "manifest.json"
CLIP_GPKG_BASENAME = "clip.gpkg"
UNION_GPKG_BASENAME = "union.gpkg"
ELIGIBLE_GPKG_BASENAME = "eligible_land_use.gpkg"
ELIGIBLE_LAYER = "eligible_land_use"
# Slice sidecars in KMZ: subsets of aggregated eligible attributable to each include clip.
ELIGIBLE_SLICE_LAYERS_SUBDIR = "layers"
REFERENCE_GPKG_BASENAME = "reference.gpkg"
REFERENCE_GPKG_LAYER = "reference"
REFERENCE_EMPTY_MARKER = ".empty"

ClipRole = Literal["aoi", "include", "exclude"]


def resolved_clips_cache_root(cache_base: Path) -> Path:
    return Path(cache_base).expanduser().resolve() / "clips"


def _sha16(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def _clip_dir(clips_root: Path, clip_sha: str) -> Path:
    return clips_root / "clip" / clip_sha


def _composite_dir(clips_root: Path, role: str, composite_sha: str) -> Path:
    return clips_root / role / composite_sha


def _reference_dir(clips_root: Path, entry_sha: str) -> Path:
    return clips_root / "reference" / entry_sha


def reference_gpkg_path(clips_root: Path, entry_sha: str) -> Path:
    return _reference_dir(clips_root, entry_sha) / REFERENCE_GPKG_BASENAME


def reference_kml_path(clips_root: Path, entry_sha: str) -> Path:
    return _reference_dir(clips_root, entry_sha) / REFERENCE_GPKG_BASENAME.replace(".gpkg", ".kml")


def clip_gpkg_path(clips_root: Path, clip_sha: str) -> Path:
    return _clip_dir(clips_root, clip_sha) / CLIP_GPKG_BASENAME


def clip_kml_path(clips_root: Path, clip_sha: str) -> Path:
    """Sidecar KML beside ``clip.gpkg`` for one cached GDB clip."""
    return _clip_dir(clips_root, clip_sha) / CLIP_GPKG_BASENAME.replace(".gpkg", ".kml")


def composite_union_gpkg(clips_root: Path, role: str, composite_sha: str) -> Path:
    return _composite_dir(clips_root, role, composite_sha) / UNION_GPKG_BASENAME


def eligible_gpkg_path(clips_root: Path, eligible_sha: str) -> Path:
    return _composite_dir(clips_root, "eligible", eligible_sha) / ELIGIBLE_GPKG_BASENAME


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


def _include_config_json(pre: BundleConfig) -> str:
    sorted_groups = sorted(
        pre.include,
        key=lambda g: (g.path, tuple(s.name for s in sorted(g.layers, key=lambda x: x.name))),
    )
    payload = {"include": [_gdb_layer_group_payload(g) for g in sorted_groups]}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _exclude_config_json(pre: BundleConfig) -> str:
    sorted_groups = sorted(
        pre.exclude,
        key=lambda g: (g.path, tuple(s.name for s in sorted(g.layers, key=lambda x: x.name))),
    )
    payload = {"exclude": [_gdb_layer_group_payload(g) for g in sorted_groups]}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def composite_include_fingerprint_body(
    pre: BundleConfig, data_dir: Path, *, aoi_mask_sha: str, gdb_fingerprint_fn: Any
) -> str:
    from peaky_finders.bundle_build import _unique_sorted_include_gdb_roots

    data_dir = Path(data_dir).expanduser().resolve()
    parts = [
        f"format={COMPOSITE_INCLUDE_FORMAT}",
        f"aoi_mask={aoi_mask_sha}",
        "config",
        _include_config_json(pre),
        "gdb_roots",
    ]
    for root in _unique_sorted_include_gdb_roots(pre, data_dir):
        if not root.exists():
            raise FileNotFoundError(f"GDB path not found for include composite fingerprint: {root}")
        parts.append(str(root.resolve()))
        parts.append(gdb_fingerprint_fn(root).rstrip("\n"))
    return "\n".join(parts) + "\n"


def composite_exclude_fingerprint_body(
    pre: BundleConfig, data_dir: Path, *, aoi_mask_sha: str, gdb_fingerprint_fn: Any
) -> str:
    from peaky_finders.bundle_build import _unique_sorted_exclude_gdb_roots

    data_dir = Path(data_dir).expanduser().resolve()
    parts = [
        f"format={COMPOSITE_EXCLUDE_FORMAT}",
        f"aoi_mask={aoi_mask_sha}",
        "config",
        _exclude_config_json(pre),
        "gdb_roots",
    ]
    for root in _unique_sorted_exclude_gdb_roots(pre, data_dir):
        if not root.exists():
            raise FileNotFoundError(f"GDB path not found for exclude composite fingerprint: {root}")
        parts.append(str(root.resolve()))
        parts.append(gdb_fingerprint_fn(root).rstrip("\n"))
    return "\n".join(parts) + "\n"


def eligible_fingerprint_body(*, include_sha: str, exclude_sha: str) -> str:
    return (
        f"format={COMPOSITE_ELIGIBLE_FORMAT}\n"
        f"include={include_sha}\n"
        f"exclude={exclude_sha}\n"
    )


def plan_clip_build_result(
    *,
    plc: BundleConfig,
    data_dir: Path,
    clips_root: Path,
) -> ClipBuildResult:
    """Resolve clip/composite SHAs and eligible GPKG path without building."""
    from peaky_finders.bundle_build import (
        _file_tree_mtime_size_fingerprint,
        _flatten_gdb_layer_jobs,
        aoi_inputs_fingerprint_body,
    )

    data_dir = Path(data_dir).expanduser().resolve()
    clips_root = Path(clips_root).expanduser().resolve()
    mask_body = aoi_inputs_fingerprint_body(plc, data_dir)
    mask_sha = aoi_mask_sha_from_body(mask_body)
    gdb_fp = _file_tree_mtime_size_fingerprint

    aoi_clip_shas: list[str] = []
    for preset_path, resolved, layer_name, where in _flatten_gdb_layer_jobs(plc.aoi, data_dir):
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
        composite_include_fingerprint_body(plc, data_dir, aoi_mask_sha=mask_sha, gdb_fingerprint_fn=gdb_fp)
    )
    exclude_sha = _sha16(
        composite_exclude_fingerprint_body(plc, data_dir, aoi_mask_sha=mask_sha, gdb_fingerprint_fn=gdb_fp)
    )
    eligible_sha = _sha16(eligible_fingerprint_body(include_sha=include_sha, exclude_sha=exclude_sha))
    return ClipBuildResult(
        aoi_sha=aoi_sha,
        include_sha=include_sha,
        exclude_sha=exclude_sha,
        eligible_sha=eligible_sha,
        eligible_gpkg=eligible_gpkg_path(clips_root, eligible_sha),
    )


def aoi_mask_sha_from_body(mask_body: str) -> str:
    return _sha16(mask_body)


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
    bundle_resolve_path(bundle_dir).write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def reference_entry_fingerprint_body(
    ent: BundleReferenceLayerEntry,
    data_dir: Path,
    *,
    aoi_mask_sha: str,
    gdb_tree: str,
) -> str:
    from peaky_finders.bundle_build import resolve_land_use_gdb_path

    resolved = resolve_land_use_gdb_path(data_dir, ent.path)
    entry_json = json.dumps(_reference_entry_payload(ent), ensure_ascii=False, sort_keys=True)
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
    plc: BundleConfig,
    data_dir: Path,
    clips_root: Path,
) -> dict[str, str]:
    """Map ``bundle.reference`` entry id → content-addressed sha (no I/O beyond GDB trees)."""
    from peaky_finders.bundle_build import (
        _file_tree_mtime_size_fingerprint,
        aoi_inputs_fingerprint_body,
        resolve_land_use_gdb_path,
    )

    if not plc.reference:
        return {}
    data_dir = Path(data_dir).expanduser().resolve()
    mask_sha = aoi_mask_sha_from_body(aoi_inputs_fingerprint_body(plc, data_dir))
    gdb_fp = _file_tree_mtime_size_fingerprint
    out: dict[str, str] = {}
    for ent in plc.reference:
        resolved = resolve_land_use_gdb_path(data_dir, ent.path)
        if not resolved.exists():
            raise FileNotFoundError(f"GDB path not found for reference fingerprint: {resolved}")
        body = reference_entry_fingerprint_body(
            ent, data_dir, aoi_mask_sha=mask_sha, gdb_tree=gdb_fp(resolved)
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
    return reference_kml_path(root, sha)


def eligible_gpkg_from_bundle_dir(bundle_dir: Path) -> Path:
    r = read_bundle_resolve(bundle_dir)
    root = Path(str(r["clips_root"])).resolve()
    return eligible_gpkg_path(root, str(r["eligible"]))


def composite_kml_from_bundle_dir(bundle_dir: Path, role: Literal["aoi", "include", "exclude"]) -> Path:
    r = read_bundle_resolve(bundle_dir)
    root = Path(str(r["clips_root"])).resolve()
    sha = str(r[role])
    return _composite_dir(root, role, sha) / UNION_GPKG_BASENAME.replace(".gpkg", ".kml")


def read_composite_exclude_manifest(clips_root: Path, exclude_sha: str) -> list[str]:
    """Clip SHAs referenced by the composite exclude union manifest (sorted order)."""
    path = _composite_dir(clips_root, "exclude", exclude_sha) / MANIFEST_BASENAME
    if not path.is_file():
        return []
    raw_obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw_obj, dict) or raw_obj.get("format") != COMPOSITE_EXCLUDE_FORMAT:
        return []
    clips = raw_obj.get("clips")
    if not isinstance(clips, list):
        return []
    return sorted(str(x) for x in clips if isinstance(x, str))


def read_composite_include_manifest(clips_root: Path, include_sha: str) -> list[str]:
    """Clip SHAs referenced by the composite include union manifest (sorted order)."""
    path = _composite_dir(clips_root, "include", include_sha) / MANIFEST_BASENAME
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

    Uses clip-cache manifests when ``resolve.json`` exists; otherwise legacy ``exclude/clip_manifest.json``.
    """
    from peaky_finders.bundle_build import (
        CLIP_EXCLUDE,
        CLIP_MANIFEST_BASENAME,
        SUBDIR_EXCLUDE,
        _clip_stem,
        _file_tree_mtime_size_fingerprint,
        _flatten_gdb_layer_jobs,
        _kml_label_gdb_job,
        aoi_inputs_fingerprint_body,
        resolve_land_use_gdb_path,
    )

    plc = preset.bundle
    if plc is None:
        return []

    data_dir = Path(data_dir).expanduser().resolve()
    bundle_dir = Path(bundle_dir).expanduser().resolve()

    rows: list[tuple[str, Path, str]] = []

    if bundle_resolve_path(bundle_dir).is_file():
        resolve = read_bundle_resolve(bundle_dir)
        clips_root = Path(str(resolve["clips_root"])).resolve()
        exclude_sha = str(resolve["exclude"])
        manifest_shas = set(read_composite_exclude_manifest(clips_root, exclude_sha))
        if not manifest_shas:
            return []

        mask_body = aoi_inputs_fingerprint_body(plc, data_dir)
        gdb_fp = _file_tree_mtime_size_fingerprint

        for preset_path, resolved, layer_name, where in _flatten_gdb_layer_jobs(plc.exclude, data_dir):
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
            kml_disk = clip_kml_path(clips_root, sha)
            if not kml_disk.is_file():
                continue
            label = _kml_label_gdb_job(preset_path, layer_name)
            stem = _clip_stem(preset_path, resolved, layer_name, where)
            arcname = f"exclude/layers/{stem}.kml"
            rows.append((label, kml_disk.resolve(), arcname))
        rows.sort(key=lambda t: t[2])
        return rows

    exc_dir = bundle_dir / SUBDIR_EXCLUDE
    manifest_path = exc_dir / CLIP_MANIFEST_BASENAME
    if not manifest_path.is_file():
        return []
    try:
        raw_obj = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(raw_obj, dict) or raw_obj.get("format") != CLIP_EXCLUDE.manifest_format:
        return []
    items = raw_obj.get("items")
    if not isinstance(items, list):
        return []

    for it in items:
        if not isinstance(it, dict):
            continue
        fn = it.get("file")
        path_str = it.get("path")
        layer_str = it.get("layer")
        if not isinstance(fn, str) or not isinstance(path_str, str) or not isinstance(layer_str, str):
            continue
        kml_disk = exc_dir / fn.replace(".gpkg", ".kml")
        if not kml_disk.is_file():
            continue
        where_raw = it.get("where")
        where_s = where_raw if isinstance(where_raw, str) else None
        resolved = resolve_land_use_gdb_path(data_dir, path_str)
        stem = _clip_stem(path_str, resolved, layer_str, where_s)
        label = _kml_label_gdb_job(path_str, layer_str)
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

    Uses clip-cache manifests when ``resolve.json`` exists; otherwise legacy ``include/clip_manifest.json``.
    """
    from peaky_finders.bundle_build import (
        CLIP_INCLUDE,
        CLIP_MANIFEST_BASENAME,
        SUBDIR_INCLUDE,
        _clip_stem,
        _file_tree_mtime_size_fingerprint,
        _flatten_gdb_layer_jobs,
        _kml_label_gdb_job,
        aoi_inputs_fingerprint_body,
        resolve_land_use_gdb_path,
    )

    plc = preset.bundle
    if plc is None:
        return []

    data_dir = Path(data_dir).expanduser().resolve()
    bundle_dir = Path(bundle_dir).expanduser().resolve()

    rows: list[tuple[str, Path, str]] = []

    if bundle_resolve_path(bundle_dir).is_file():
        resolve = read_bundle_resolve(bundle_dir)
        clips_root = Path(str(resolve["clips_root"])).resolve()
        include_sha = str(resolve["include"])
        manifest_shas = set(read_composite_include_manifest(clips_root, include_sha))
        if not manifest_shas:
            return []

        mask_body = aoi_inputs_fingerprint_body(plc, data_dir)
        gdb_fp = _file_tree_mtime_size_fingerprint

        for preset_path, resolved, layer_name, where in _flatten_gdb_layer_jobs(plc.include, data_dir):
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
            kml_disk = clip_kml_path(clips_root, sha)
            if not kml_disk.is_file():
                continue
            label = _kml_label_gdb_job(preset_path, layer_name)
            stem = _clip_stem(preset_path, resolved, layer_name, where)
            arcname = f"include/layers/{stem}.kml"
            rows.append((label, kml_disk.resolve(), arcname))
        rows.sort(key=lambda t: t[2])
        return rows

    inc_dir = bundle_dir / SUBDIR_INCLUDE
    manifest_path = inc_dir / CLIP_MANIFEST_BASENAME
    if not manifest_path.is_file():
        return []
    try:
        raw_obj = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(raw_obj, dict) or raw_obj.get("format") != CLIP_INCLUDE.manifest_format:
        return []
    items = raw_obj.get("items")
    if not isinstance(items, list):
        return []

    for it in items:
        if not isinstance(it, dict):
            continue
        fn = it.get("file")
        path_str = it.get("path")
        layer_str = it.get("layer")
        if not isinstance(fn, str) or not isinstance(path_str, str) or not isinstance(layer_str, str):
            continue
        kml_disk = inc_dir / fn.replace(".gpkg", ".kml")
        if not kml_disk.is_file():
            continue
        where_raw = it.get("where")
        where_s = where_raw if isinstance(where_raw, str) else None
        resolved = resolve_land_use_gdb_path(data_dir, path_str)
        stem = _clip_stem(path_str, resolved, layer_str, where_s)
        label = _kml_label_gdb_job(path_str, layer_str)
        arcname = f"include/layers/{stem}.kml"
        rows.append((label, kml_disk.resolve(), arcname))

    rows.sort(key=lambda t: t[2])
    return rows


def sync_eligible_include_layer_slices(
    *,
    plc: BundleConfig,
    data_dir: Path,
    clips_root: Path,
    mask_body: str,
    eligible_gpkg: Path,
    kml_overlay: BundleKmlOverlayStyles | None,
) -> None:
    """Write ``clips/eligible/<sha>/layers/<stem>.kml``: global eligible ∩ each include clip (post-exclusions)."""

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

    mask_sha = aoi_mask_sha_from_body(mask_body)
    include_sha = _sha16(
        composite_include_fingerprint_body(plc, data_dir, aoi_mask_sha=mask_sha, gdb_fingerprint_fn=gdb_fp)
    )
    manifest_shas = set(read_composite_include_manifest(clips_root, include_sha))

    wanted_stems: set[str] = set()
    layers_root.mkdir(parents=True, exist_ok=True)

    for preset_path, resolved, layer_name, where in _flatten_gdb_layer_jobs(plc.include, data_dir_res):
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
        stem = _clip_stem(preset_path, resolved, layer_name, where)
        wanted_stems.add(stem)
        clip_disk = clip_gpkg_path(clips_root, sha)
        kml_out = layers_root / f"{stem}.kml"

        png_out = kml_out.with_suffix(".png")
        if not clip_disk.is_file():
            kml_out.unlink(missing_ok=True)
            png_out.unlink(missing_ok=True)
            continue

        layer_label = _kml_label_gdb_job(preset_path, layer_name)
        slice_df = eligible_land_slice_from_include_clip_gpkg(geom_ll, clip_disk)

        kml_out.unlink(missing_ok=True)
        png_out.unlink(missing_ok=True)
        if slice_df.empty or slice_df.geometry.is_empty.all():
            continue
        _write_geodataframe_kml(slice_df, kml_out, layer_label=layer_label, kml_overlay=kml_overlay)

    for stray in layers_root.glob("*.kml"):
        if stray.stem not in wanted_stems:
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

    if not bundle_resolve_path(bundle_dir).is_file():
        return rows

    resolve = read_bundle_resolve(bundle_dir)
    clips_root = Path(str(resolve["clips_root"])).resolve()

    gdb_fp = _file_tree_mtime_size_fingerprint

    eligible_gpkg = eligible_gpkg_path(clips_root, str(resolve["eligible"]))
    layers_root = eligible_gpkg.parent / ELIGIBLE_SLICE_LAYERS_SUBDIR
    mask_body = aoi_inputs_fingerprint_body(plc, data_dir)
    mask_sha = aoi_mask_sha_from_body(mask_body)
    include_sha = _sha16(
        composite_include_fingerprint_body(plc, data_dir, aoi_mask_sha=mask_sha, gdb_fingerprint_fn=gdb_fp)
    )
    manifest_shas = set(read_composite_include_manifest(clips_root, include_sha))
    if not manifest_shas:
        return []

    for preset_path, resolved, layer_name, where in _flatten_gdb_layer_jobs(plc.include, data_dir):
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
        stem = _clip_stem(preset_path, resolved, layer_name, where)
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


def _union_wgs84_from_clip_shas(clips_root: Path, clip_shas: list[str]) -> gpd.GeoDataFrame:
    from peaky_finders.bundle_build import _sanitize_collection

    pieces_ll: list[BaseGeometry] = []
    for sha in sorted(clip_shas):
        gdf = gpd.read_file(clip_gpkg_path(clips_root, sha))
        if gdf.empty:
            continue
        pieces_ll.extend(gdf.geometry.tolist())
    u_ll = _sanitize_collection(pieces_ll if pieces_ll else [])
    return gpd.GeoDataFrame(geometry=[u_ll], crs="EPSG:4326")


def _union_include_from_clip_shas(clips_root: Path, clip_shas: list[str]) -> gpd.GeoDataFrame:
    from peaky_finders.bundle_build import _sanitize_collection

    pieces: list[BaseGeometry] = []
    for sha in sorted(clip_shas):
        gdf = gpd.read_file(clip_gpkg_path(clips_root, sha))
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


def _ensure_clip_layer(
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
    """Build or reuse one clip; return clip_sha, or ``None`` if clip is empty (exclude only)."""
    from peaky_finders.bundle_build import (
        _gdf_coordinate_vertex_count,
        _read_and_clip_gdb_layer_to_aoi,
        _write_geodataframe_gpkg_and_kml,
    )

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
    out = clip_gpkg_path(clips_root, sha)
    if out.is_file():
        if verbose_log:
            verbose_log(f"clip reuse {role} {preset_path}::{layer} → clip/{sha}")
        return sha

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

    out.parent.mkdir(parents=True, exist_ok=True)
    if progress_log:
        progress_log(f"clip/{sha}: write ({len(out_gdf):,} features) …")
    _write_geodataframe_gpkg_and_kml(
        out_gdf,
        out,
        gpkg_layer="features",
        layer_label=kml_label,
        kml_overlay=kml_overlay,
    )
    if verbose_log:
        verbose_log(
            f"clip {role} {preset_path}::{layer}: {len(gdf):,} native → {len(clipped):,} clipped → clip/{sha}"
        )
    return sha


def ensure_bundle_clip_cache(
    *,
    plc: BundleConfig,
    data_dir: Path,
    clips_root: Path,
    mask_body: str,
    kml_overlay: BundleKmlOverlayStyles | None,
    force: bool,
    verbose_log: Callable[[str], None] | None = None,
    progress_log: Callable[[str], None] | None = None,
) -> ClipBuildResult:
    """Build or reuse global clip cache entries; return composite SHAs and eligible GPKG path."""
    from peaky_finders.bundle_build import (
        _file_tree_mtime_size_fingerprint,
        _flatten_gdb_layer_jobs,
        _kml_label_aoi_clip,
        _kml_label_gdb_job,
        build_eligible_land_use_gdf,
        load_composite_aoi_polygon,
        resolve_land_use_gdb_path,
    )

    clips_root = Path(clips_root).expanduser().resolve()
    clips_root.mkdir(parents=True, exist_ok=True)
    data_dir = Path(data_dir).expanduser().resolve()
    mask_sha = aoi_mask_sha_from_body(mask_body)
    gdb_fp = _file_tree_mtime_size_fingerprint

    # --- AOI layer clips + composite ---
    aoi_poly_full = load_composite_aoi_polygon(plc, data_dir)
    aoi_poly_full = make_valid(aoi_poly_full)
    if aoi_poly_full.is_empty:
        raise ValueError("AOI union from bundle.aoi is empty")

    aoi_clip_shas: list[str] = []
    for preset_path, resolved, layer_name, where in _flatten_gdb_layer_jobs(plc.aoi, data_dir):
        if not resolved.exists():
            raise FileNotFoundError(f"AOI GDB not found: {resolved} (preset path {preset_path!r})")
        tree = gdb_fp(resolved)
        sha = _ensure_clip_layer(
            clips_root,
            role="aoi",
            preset_path=preset_path,
            resolved=resolved,
            layer=layer_name,
            where=where,
            mask_body=mask_body,
            gdb_tree=tree,
            aoi_poly_4326=aoi_poly_full,
            kml_label=_kml_label_aoi_clip(preset_path, layer_name),
            kml_overlay=kml_overlay,
            store_crs_3857=False,
            verbose_log=verbose_log,
            progress_log=progress_log,
        )
        if sha is not None:
            aoi_clip_shas.append(sha)

    if not aoi_clip_shas:
        raise ValueError("AOI clip set is empty")

    aoi_body = composite_aoi_fingerprint_body(aoi_clip_shas)
    aoi_sha = _sha16(aoi_body)
    aoi_dir = _composite_dir(clips_root, "aoi", aoi_sha)
    aoi_gpkg = aoi_dir / UNION_GPKG_BASENAME
    if force and aoi_gpkg.is_file():
        for suf in (".gpkg", ".kml", ".png"):
            (aoi_dir / UNION_GPKG_BASENAME.replace(".gpkg", suf)).unlink(missing_ok=True)
    if not aoi_gpkg.is_file():
        g_aoi = _union_wgs84_from_clip_shas(clips_root, aoi_clip_shas)
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
            aoi_dir / MANIFEST_BASENAME,
            fmt=COMPOSITE_AOI_FORMAT,
            payload={"clips": sorted(aoi_clip_shas)},
        )

    aoi_poly = make_valid(gpd.read_file(aoi_gpkg, layer="aoi").geometry.iloc[0])

    # --- Include ---
    inc_body = composite_include_fingerprint_body(plc, data_dir, aoi_mask_sha=mask_sha, gdb_fingerprint_fn=gdb_fp)
    include_sha = _sha16(inc_body)
    include_dir = _composite_dir(clips_root, "include", include_sha)
    include_gpkg = include_dir / UNION_GPKG_BASENAME
    if force and include_gpkg.is_file():
        for suf in (".gpkg", ".kml", ".png"):
            (include_dir / UNION_GPKG_BASENAME.replace(".gpkg", suf)).unlink(missing_ok=True)

    include_clip_shas: list[str] = []
    for preset_path, resolved, layer, where in _flatten_gdb_layer_jobs(plc.include, data_dir):
        if not resolved.exists():
            raise FileNotFoundError(f"Include GDB not found: {resolved} (preset path {preset_path!r})")
        sha = _ensure_clip_layer(
            clips_root,
            role="include",
            preset_path=preset_path,
            resolved=resolved,
            layer=layer,
            where=where,
            mask_body=mask_body,
            gdb_tree=gdb_fp(resolved),
            aoi_poly_4326=aoi_poly,
            kml_label=_kml_label_gdb_job(preset_path, layer),
            kml_overlay=kml_overlay,
            store_crs_3857=True,
            verbose_log=verbose_log,
            progress_log=progress_log,
        )
        if sha is not None:
            include_clip_shas.append(sha)

    if not include_gpkg.is_file():
        g_inc = _union_include_from_clip_shas(clips_root, include_clip_shas)
        _write_composite_union(
            g_inc,
            include_gpkg,
            gpkg_layer="include",
            layer_label="include",
            kml_overlay=kml_overlay,
        )
        _write_manifest(
            include_dir / MANIFEST_BASENAME,
            fmt=COMPOSITE_INCLUDE_FORMAT,
            payload={"clips": sorted(include_clip_shas), "aoi_mask": mask_sha},
        )

    # --- Exclude ---
    exc_body = composite_exclude_fingerprint_body(plc, data_dir, aoi_mask_sha=mask_sha, gdb_fingerprint_fn=gdb_fp)
    exclude_sha = _sha16(exc_body)
    exclude_dir = _composite_dir(clips_root, "exclude", exclude_sha)
    exclude_gpkg = exclude_dir / UNION_GPKG_BASENAME
    if force and exclude_gpkg.is_file():
        for suf in (".gpkg", ".kml", ".png"):
            (exclude_dir / UNION_GPKG_BASENAME.replace(".gpkg", suf)).unlink(missing_ok=True)

    exclude_clip_shas: list[str] = []
    if plc.exclude:
        for preset_path, resolved, layer_name, where in _flatten_gdb_layer_jobs(plc.exclude, data_dir):
            if not resolved.exists():
                raise FileNotFoundError(f"Exclude GDB not found: {resolved} (preset path {preset_path!r})")
            sha = _ensure_clip_layer(
                clips_root,
                role="exclude",
                preset_path=preset_path,
                resolved=resolved,
                layer=layer_name,
                where=where,
                mask_body=mask_body,
                gdb_tree=gdb_fp(resolved),
                aoi_poly_4326=aoi_poly,
                kml_label=_kml_label_gdb_job(preset_path, layer_name),
                kml_overlay=kml_overlay,
                store_crs_3857=False,
                verbose_log=verbose_log,
                progress_log=progress_log,
            )
            if sha is not None:
                exclude_clip_shas.append(sha)

    if not exclude_gpkg.is_file():
        if exclude_clip_shas:
            g_exc = _union_wgs84_from_clip_shas(clips_root, exclude_clip_shas)
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
            exclude_dir / MANIFEST_BASENAME,
            fmt=COMPOSITE_EXCLUDE_FORMAT,
            payload={"clips": sorted(exclude_clip_shas), "aoi_mask": mask_sha},
        )

    # --- Eligible ---
    elig_body = eligible_fingerprint_body(include_sha=include_sha, exclude_sha=exclude_sha)
    eligible_sha = _sha16(elig_body)
    eligible_dir = _composite_dir(clips_root, "eligible", eligible_sha)
    eligible_gpkg = eligible_dir / ELIGIBLE_GPKG_BASENAME
    if force and eligible_gpkg.is_file():
        lyr = eligible_dir / ELIGIBLE_SLICE_LAYERS_SUBDIR
        if lyr.is_dir():
            shutil.rmtree(lyr)
        for suf in (".gpkg", ".kml", ".png"):
            (eligible_dir / ELIGIBLE_GPKG_BASENAME.replace(".gpkg", suf)).unlink(missing_ok=True)

    if not eligible_gpkg.is_file():
        include_union = gpd.read_file(include_gpkg, layer="include")
        exclude_union = gpd.read_file(exclude_gpkg, layer="exclude")
        eligible_gdf = build_eligible_land_use_gdf(include_union, exclude_union)
        _write_composite_union(
            eligible_gdf,
            eligible_gpkg,
            gpkg_layer=ELIGIBLE_LAYER,
            layer_label=ELIGIBLE_LAYER,
            kml_overlay=kml_overlay,
        )

    sync_eligible_include_layer_slices(
        plc=plc,
        data_dir=data_dir,
        clips_root=clips_root,
        mask_body=mask_body,
        eligible_gpkg=eligible_gpkg,
        kml_overlay=kml_overlay,
    )

    return ClipBuildResult(
        aoi_sha=aoi_sha,
        include_sha=include_sha,
        exclude_sha=exclude_sha,
        eligible_sha=eligible_sha,
        eligible_gpkg=eligible_gpkg,
    )


def refresh_clip_kml_sidecars(
    *,
    plc: BundleConfig,
    data_dir: Path,
    clips_root: Path,
    mask_body: str,
    kml_overlay: BundleKmlOverlayStyles | None,
    result: ClipBuildResult,
    verbose_log: Callable[[str], None] | None = None,
) -> None:
    """Regenerate KML/PNG for clips and composites after ``bundle.kml_overlay`` change."""
    from peaky_finders.bundle_build import (
        _file_tree_mtime_size_fingerprint,
        _flatten_gdb_layer_jobs,
        _kml_label_aoi_clip,
        _kml_label_gdb_job,
        _write_geodataframe_kml,
        load_composite_aoi_polygon,
        resolve_land_use_gdb_path,
    )

    clips_root = Path(clips_root).expanduser().resolve()
    data_dir = Path(data_dir).expanduser().resolve()
    gdb_fp = _file_tree_mtime_size_fingerprint
    aoi_poly_full = make_valid(load_composite_aoi_polygon(plc, data_dir))

    for preset_path, resolved, layer_name, where in _flatten_gdb_layer_jobs(plc.aoi, data_dir):
        body = clip_layer_fingerprint_body(
            role="aoi",
            preset_path=preset_path,
            resolved=resolved,
            layer=layer_name,
            where=where,
            mask_body=mask_body,
            gdb_tree=gdb_fp(resolved),
        )
        sha = clip_layer_sha(body)
        p = clip_gpkg_path(clips_root, sha)
        if p.is_file():
            gdf = gpd.read_file(p)
            _write_geodataframe_kml(
                gdf, p.with_suffix(".kml"), layer_label=_kml_label_aoi_clip(preset_path, layer_name), kml_overlay=kml_overlay
            )

    aoi_gpkg = composite_union_gpkg(clips_root, "aoi", result.aoi_sha)
    if aoi_gpkg.is_file():
        gdf = gpd.read_file(aoi_gpkg, layer="aoi")
        _write_geodataframe_kml(gdf, aoi_gpkg.with_suffix(".kml"), layer_label="aoi", kml_overlay=kml_overlay)

    aoi_poly = make_valid(gpd.read_file(aoi_gpkg, layer="aoi").geometry.iloc[0]) if aoi_gpkg.is_file() else aoi_poly_full

    for preset_path, resolved, layer, where in _flatten_gdb_layer_jobs(plc.include, data_dir):
        body = clip_layer_fingerprint_body(
            role="include",
            preset_path=preset_path,
            resolved=resolved,
            layer=layer,
            where=where,
            mask_body=mask_body,
            gdb_tree=gdb_fp(resolved),
        )
        sha = clip_layer_sha(body)
        p = clip_gpkg_path(clips_root, sha)
        if p.is_file():
            gdf = gpd.read_file(p)
            _write_geodataframe_kml(
                gdf, p.with_suffix(".kml"), layer_label=_kml_label_gdb_job(preset_path, layer), kml_overlay=kml_overlay
            )

    inc_gpkg = composite_union_gpkg(clips_root, "include", result.include_sha)
    if inc_gpkg.is_file():
        gdf = gpd.read_file(inc_gpkg, layer="include")
        _write_geodataframe_kml(gdf, inc_gpkg.with_suffix(".kml"), layer_label="include", kml_overlay=kml_overlay)

    for preset_path, resolved, layer_name, where in _flatten_gdb_layer_jobs(plc.exclude, data_dir):
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
        p = clip_gpkg_path(clips_root, sha)
        if p.is_file():
            gdf = gpd.read_file(p)
            _write_geodataframe_kml(
                gdf,
                p.with_suffix(".kml"),
                layer_label=_kml_label_gdb_job(preset_path, layer_name),
                kml_overlay=kml_overlay,
            )

    exc_gpkg = composite_union_gpkg(clips_root, "exclude", result.exclude_sha)
    if exc_gpkg.is_file():
        gdf = gpd.read_file(exc_gpkg, layer="exclude")
        _write_geodataframe_kml(gdf, exc_gpkg.with_suffix(".kml"), layer_label="exclude", kml_overlay=kml_overlay)

    if result.eligible_gpkg.is_file():
        gdf = gpd.read_file(result.eligible_gpkg, layer=ELIGIBLE_LAYER)
        _write_geodataframe_kml(
            gdf,
            result.eligible_gpkg.with_suffix(".kml"),
            layer_label=ELIGIBLE_LAYER,
            kml_overlay=kml_overlay,
        )
        sync_eligible_include_layer_slices(
            plc=plc,
            data_dir=data_dir,
            clips_root=clips_root,
            mask_body=mask_body,
            eligible_gpkg=result.eligible_gpkg,
            kml_overlay=kml_overlay,
        )

    if verbose_log:
        verbose_log("clips: refreshed KML/PNG sidecars for kml_overlay change")


def ensure_reference_clip_cache(
    *,
    plc: BundleConfig,
    data_dir: Path,
    clips_root: Path,
    aoi_sha: str,
    kml_overlay: BundleKmlOverlayStyles | None,
    force: bool,
    verbose_log: Callable[[str], None] | None = None,
    progress_log: Callable[[str], None] | None = None,
) -> dict[str, str]:
    """Build or reuse ``clips/reference/<sha>/`` per ``bundle.reference`` entry."""
    import pandas as pd

    from peaky_finders.bundle_build import (
        _flatten_gdb_layer_jobs,
        _overlay_for_reference_entry,
        _read_and_clip_gdb_layer_to_aoi,
        _write_geodataframe_gpkg_and_kml,
    )

    if not plc.reference:
        return {}

    clips_root = Path(clips_root).expanduser().resolve()
    data_dir = Path(data_dir).expanduser().resolve()
    entry_shas = plan_reference_entries(plc=plc, data_dir=data_dir, clips_root=clips_root)

    aoi_gpkg = composite_union_gpkg(clips_root, "aoi", aoi_sha)
    aoi_poly = make_valid(gpd.read_file(aoi_gpkg, layer="aoi").geometry.iloc[0])

    for ent in plc.reference:
        entry_sha = entry_shas[ent.id]
        ref_dir = _reference_dir(clips_root, entry_sha)
        out_gpkg = reference_gpkg_path(clips_root, entry_sha)
        out_kml = reference_kml_path(clips_root, entry_sha)
        empty_marker = ref_dir / REFERENCE_EMPTY_MARKER

        if force and ref_dir.is_dir():
            for p in ref_dir.iterdir():
                if p.is_file():
                    p.unlink(missing_ok=True)

        if not force and empty_marker.is_file():
            if verbose_log:
                verbose_log(f"reference reuse {ent.id} → reference/{entry_sha} (empty)")
            continue
        if not force and out_gpkg.is_file():
            if verbose_log:
                verbose_log(f"reference reuse {ent.id} → reference/{entry_sha}")
            continue

        ref_dir.mkdir(parents=True, exist_ok=True)
        empty_marker.unlink(missing_ok=True)
        out_gpkg.unlink(missing_ok=True)
        out_kml.unlink(missing_ok=True)
        out_kml.with_suffix(".png").unlink(missing_ok=True)

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
                verbose_log(f"reference [{ent.id}]: empty after clip → reference/{entry_sha}/.empty")
            empty_marker.write_text("1\n", encoding="utf-8")
            continue

        merged = gpd.GeoDataFrame(pd.concat(pieces_ll, ignore_index=True), crs="EPSG:4326")
        overlay = _overlay_for_reference_entry(ent, plc)
        if progress_log:
            progress_log(f"reference/{entry_sha}: write reference.gpkg + kml …")
        _write_geodataframe_gpkg_and_kml(
            merged,
            out_gpkg,
            gpkg_layer=REFERENCE_GPKG_LAYER,
            layer_label=ent.id,
            kml_overlay=overlay,
        )
        if verbose_log:
            verbose_log(f"reference [{ent.id}]: wrote reference/{entry_sha}/reference.gpkg + kml")

    return entry_shas


def refresh_reference_clip_kml_sidecars(
    *,
    plc: BundleConfig,
    clips_root: Path,
    reference_shas: dict[str, str],
    verbose_log: Callable[[str], None] | None = None,
) -> None:
    """Re-style reference KML/PNG from cached GeoPackages (``bundle.kml_overlay`` change)."""
    from peaky_finders.bundle_build import _overlay_for_reference_entry, _write_geodataframe_kml

    if not plc.reference or not reference_shas:
        return
    clips_root = Path(clips_root).expanduser().resolve()
    for ent in plc.reference:
        sha = reference_shas.get(ent.id)
        if not sha:
            continue
        gpkg = reference_gpkg_path(clips_root, sha)
        kml = reference_kml_path(clips_root, sha)
        if not gpkg.is_file():
            continue
        gdf = gpd.read_file(gpkg, layer=REFERENCE_GPKG_LAYER)
        overlay = _overlay_for_reference_entry(ent, plc)
        _write_geodataframe_kml(gdf, kml, layer_label=ent.id, kml_overlay=overlay)
    if verbose_log:
        verbose_log("clips: refreshed reference KML/PNG sidecars")
