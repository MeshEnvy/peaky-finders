"""Project ``maps`` catalog, upload, GeoJSON, and clip rebuild for the web UI."""

from __future__ import annotations

import json
import shutil
import uuid
import zipfile
from pathlib import Path
from typing import Any, Callable

import geopandas as gpd
import pandas as pd
import pyogrio
from shapely.geometry import mapping

from peaky_finders.bundle_build import (
    _file_tree_mtime_size_fingerprint,
    _flatten_gdb_layer_jobs,
    _kml_label_gdb_job,
    aoi_inputs_fingerprint_body,
    list_polygon_layer_names,
    load_composite_aoi_polygon,
    openfilegdb_dataset_path,
    require_bundle_config,
    resolve_land_use_gdb_path,
)
from peaky_finders.bundle_clips import (
    build_clip_layer,
    build_composite_aoi,
    build_composite_exclude,
    build_composite_include,
    build_eligible_workspace,
    build_reference_entry,
    bundle_resolve_path,
    composite_union_gpkg,
    eligible_gpkg_path,
    layer_job_gpkg_path,
    plan_clip_layer_jobs,
    plan_reference_entries,
    read_bundle_resolve,
    reference_gpkg_path,
    write_bundle_resolve,
)
from peaky_finders.sites_job import (
    MapType,
    Preset,
    ProjectMapEntry,
    dump_preset_yaml_document,
    load_preset,
    maps_by_type,
    parse_preset_dict,
    preset_general_overlays,
    read_preset_yaml_tree,
    resolved_bundle_dir,
    resolved_preset_clips_dir,
    resolved_preset_data_dir,
)

MAP_TYPES: tuple[MapType, ...] = ("aoi", "include", "exclude", "general_overlay")


def _preset_path(slug: str) -> Path:
    from peaky_finders.sites_job import peaky_projects_dir

    cfg = peaky_projects_dir() / slug / "config.yaml"
    if not cfg.is_file():
        raise FileNotFoundError(f"project not found: {slug!r}")
    return cfg


def _validate_data_path(data_dir: Path, rel_path: str) -> Path:
    root = data_dir.resolve()
    target = (root / rel_path).resolve()
    if not str(target).startswith(str(root)):
        raise ValueError("path must resolve under project data/")
    return target


def _resolve_exists(bundle_dir: Path) -> bool:
    return bundle_resolve_path(bundle_dir).is_file()


def _find_layer_job_clip_gpkg(
    clips_root: Path,
    *,
    role: str,
    preset_path_s: str,
    layer_name: str,
    where: str | None,
) -> Path | None:
    """Resolve per-layer clip GPKG; tolerates legacy ``{role}_`` stem prefixes."""
    from peaky_finders.bundle_build import _clip_stem

    stem = _clip_stem(preset_path_s, layer_name, where)
    direct = layer_job_gpkg_path(clips_root, role, stem)
    if direct.is_file():
        return direct

    jobs_dir = clips_root / "layer_jobs" / role
    if not jobs_dir.is_dir():
        return None

    legacy = layer_job_gpkg_path(clips_root, role, f"{role}_{stem}")
    if legacy.is_file():
        return legacy

    path_stem = Path(preset_path_s).stem.lower()
    suffix = f"__{layer_name}"
    for d in sorted(jobs_dir.iterdir()):
        if not d.is_dir():
            continue
        name = d.name
        if not name.endswith(suffix):
            continue
        if path_stem in name.lower() or Path(preset_path_s).name.lower().replace(".", "_") in name.lower():
            gpkg = d / "clip.gpkg"
            if gpkg.is_file():
                return gpkg
    return None


def _map_entry_clip_gpkgs(preset: Preset, preset_path: Path, entry: ProjectMapEntry) -> list[Path]:
    from peaky_finders.sites_job import map_entry_to_gdb_layer_group

    clips_root = resolved_preset_clips_dir(preset_path)
    if entry.type == "general_overlay":
        gpkg = reference_gpkg_path(clips_root, entry.id)
        return [gpkg] if gpkg.is_file() else []

    g = map_entry_to_gdb_layer_group(entry)
    data_dir = resolved_preset_data_dir(preset_path)
    found: list[Path] = []
    for preset_path_s, _resolved, layer_name, where in _flatten_gdb_layer_jobs([g], data_dir):
        clip = _find_layer_job_clip_gpkg(
            clips_root,
            role=entry.type,
            preset_path_s=preset_path_s,
            layer_name=layer_name,
            where=where,
        )
        if clip is not None:
            found.append(clip)

    if found:
        return found

    # Single-entry role: fall back to composite union (legacy builds / stale stems).
    role_maps = [m for m in preset.maps if m.type == entry.type]
    if len(role_maps) == 1 and entry.type in ("aoi", "include", "exclude"):
        union = composite_union_gpkg(clips_root, entry.type)
        if union.is_file():
            return [union]
    return []


def _map_entry_build_status(preset: Preset, preset_path: Path, entry: ProjectMapEntry) -> str:
    if entry.type == "general_overlay":
        gpkg = reference_gpkg_path(resolved_preset_clips_dir(preset_path), entry.id)
        return "built" if gpkg.is_file() else "missing"
    return "built" if _map_entry_clip_gpkgs(preset, preset_path, entry) else "missing"


def _map_entry_map_style(preset: Preset, entry: ProjectMapEntry) -> dict[str, str] | None:
    if entry.style is not None:
        from peaky_finders.web.mesh_layers import _kml_style_colors

        return _kml_style_colors(entry.style)
    bundle = preset.bundle
    if bundle is None or bundle.kml_overlay is None:
        return None
    overlay = bundle.kml_overlay
    style = getattr(overlay, entry.type, None) or overlay.default
    from peaky_finders.web.mesh_layers import _kml_style_colors

    return _kml_style_colors(style)


def _map_entry_dict(
    m: ProjectMapEntry,
    preset: Preset,
    preset_path: Path,
    *,
    slug: str,
    clips_row_phase: str,
) -> dict[str, Any]:
    return {
        "id": m.id,
        "name": m.name,
        "description": m.description,
        "type": m.type,
        "path": m.path,
        "layers": list(m.layers),
        "display_mode": "all" if m.visible else "off",
        "visible": m.visible,
        "build_phase": clips_row_phase,
        "map_style": _map_entry_map_style(preset, m),
    }


def _eligible_catalog_entry(preset: Preset, preset_path: Path, *, slug: str, clips_row_phase: str) -> dict[str, Any]:
    default_visible = False
    if preset.bundle is not None and preset.bundle.kmz is not None:
        default_visible = bool(preset.bundle.kmz.layers.eligible)
    return {
        "type": "eligible",
        "id": "eligible",
        "name": "Eligible land",
        "description": "Derived deployable land (∪include \\ ∪exclude) ∩ AOI",
        "display_mode": "all" if default_visible else "off",
        "build_phase": clips_row_phase,
    }


def project_maps_catalog(slug: str) -> dict[str, Any]:
    from peaky_finders.sites_job import read_preset_yaml_tree
    from peaky_finders.web.maps_build_scheduler import catalog_clips_row_phase, maps_build_status_for_preset
    from peaky_finders.web.mesh_layers import mesh_layers_catalog_for_preset

    preset_path = _preset_path(slug)
    preset = load_preset(preset_path)
    _, yaml_root = read_preset_yaml_tree(preset_path)
    clips_row_phase = catalog_clips_row_phase(slug)

    entries: list[dict[str, Any]] = []
    for m in preset.maps:
        entries.append(_map_entry_dict(m, preset, preset_path, slug=slug, clips_row_phase=clips_row_phase))

    mesh = mesh_layers_catalog_for_preset(slug, preset, preset_path, yaml_root)

    by_type: dict[str, list[dict[str, Any]]] = {t: [] for t in MAP_TYPES}
    for e in entries:
        by_type.setdefault(str(e["type"]), []).append(e)

    return {
        "maps": entries,
        "by_type": by_type,
        "eligible": _eligible_catalog_entry(preset, preset_path, slug=slug, clips_row_phase=clips_row_phase),
        "mesh": mesh,
        "build": maps_build_status_for_preset(slug, preset),
    }


def inspect_data_path(slug: str, rel_path: str) -> dict[str, Any]:
    preset_path = _preset_path(slug)
    data_dir = resolved_preset_data_dir(preset_path)
    resolved = _validate_data_path(data_dir, rel_path)
    if not resolved.exists():
        raise FileNotFoundError(f"dataset not found: {rel_path!r}")

    layers: list[dict[str, Any]] = []
    with openfilegdb_dataset_path(resolved) as ds:
        info = pyogrio.list_layers(ds)
        if info.ndim == 1:
            rows = [(str(info[0]), str(info[1]))]
        else:
            rows = [(str(r[0]), str(r[1])) for r in info]
        for name, geom_type in rows:
            try:
                count = int(pyogrio.read_info(ds, layer=name)["features_count"])
            except Exception:
                count = None
            sample_fields: list[str] = []
            try:
                df = pyogrio.read_dataframe(ds, layer=name, max_features=1)
                sample_fields = [str(c) for c in df.columns if c != "geometry"][:12]
            except Exception:
                pass
            layers.append(
                {
                    "name": name,
                    "geometry_type": geom_type,
                    "feature_count": count,
                    "fields": sample_fields,
                }
            )
    return {"path": rel_path, "layers": layers}


def _gdf_to_geojson(gdf: gpd.GeoDataFrame, *, tolerance: float = 0.0001) -> dict[str, Any]:
    if gdf.empty:
        return {"type": "FeatureCollection", "features": []}
    g = gdf.to_crs("EPSG:4326")
    if tolerance > 0:
        g = g.copy()
        g["geometry"] = g.geometry.simplify(tolerance, preserve_topology=True)
    features = []
    for _, row in g.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        props = {k: v for k, v in row.items() if k != "geometry"}
        features.append({"type": "Feature", "geometry": mapping(geom), "properties": props})
    return {"type": "FeatureCollection", "features": features}


def map_entry_geojson(slug: str, map_id: str) -> dict[str, Any]:
    preset_path = _preset_path(slug)
    preset = load_preset(preset_path)
    entry = next((m for m in preset.maps if m.id == map_id), None)
    if entry is None:
        raise KeyError(f"unknown map id: {map_id!r}")

    clips_root = resolved_preset_clips_dir(preset_path)

    if entry.type == "general_overlay":
        gpkg = reference_gpkg_path(clips_root, entry.id)
        if not gpkg.is_file():
            raise FileNotFoundError(f"general_overlay clip not built for {map_id!r}")
        gdf = gpd.read_file(gpkg, layer="reference")
        return _gdf_to_geojson(gdf)

    clip_gpkgs = _map_entry_clip_gpkgs(preset, preset_path, entry)
    if not clip_gpkgs:
        raise FileNotFoundError(f"clip not built for map {map_id!r}")

    pieces: list[gpd.GeoDataFrame] = []
    for clip_gpkg in clip_gpkgs:
        if clip_gpkg.parent.parent.name == "layer_jobs":
            pieces.append(gpd.read_file(clip_gpkg, layer="features"))
        else:
            # composite union.gpkg
            pieces.append(gpd.read_file(clip_gpkg, layer=entry.type))
    if not pieces:
        return {"type": "FeatureCollection", "features": []}
    merged = gpd.GeoDataFrame(pd.concat(pieces, ignore_index=True), crs=pieces[0].crs)
    return _gdf_to_geojson(merged)


def eligible_geojson(slug: str) -> dict[str, Any]:
    preset_path = _preset_path(slug)
    gpkg = eligible_gpkg_path(resolved_preset_clips_dir(preset_path))
    if not gpkg.is_file():
        raise FileNotFoundError("eligible land not built")
    gdf = gpd.read_file(gpkg, layer="eligible_land_use")
    return _gdf_to_geojson(gdf, tolerance=0.0005)


def save_uploaded_dataset(slug: str, filename: str, content: bytes) -> dict[str, Any]:
    preset_path = _preset_path(slug)
    data_dir = resolved_preset_data_dir(preset_path)
    data_dir.mkdir(parents=True, exist_ok=True)

    safe_name = Path(filename).name
    if not safe_name:
        raise ValueError("filename required")

    dest = data_dir / safe_name
    if safe_name.lower().endswith(".kmz"):
        tmp = data_dir / f".upload-{uuid.uuid4().hex}"
        tmp.mkdir(parents=True)
        kmz_path = tmp / safe_name
        kmz_path.write_bytes(content)
        with zipfile.ZipFile(kmz_path) as zf:
            kml_names = [n for n in zf.namelist() if n.lower().endswith(".kml")]
            if not kml_names:
                shutil.rmtree(tmp, ignore_errors=True)
                raise ValueError("KMZ contains no KML document")
            extracted = tmp / Path(kml_names[0]).name
            extracted.write_bytes(zf.read(kml_names[0]))
        final = data_dir / extracted.name
        shutil.move(str(extracted), str(final))
        shutil.rmtree(tmp, ignore_errors=True)
        rel = final.name
    elif safe_name.lower().endswith(".zip"):
        tmp = data_dir / f".upload-{uuid.uuid4().hex}"
        tmp.mkdir(parents=True)
        zip_path = tmp / safe_name
        zip_path.write_bytes(content)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(tmp)
        gdb_dirs = [p for p in tmp.rglob("*.gdb") if p.is_dir()]
        if not gdb_dirs:
            shutil.rmtree(tmp, ignore_errors=True)
            raise ValueError("ZIP contains no .gdb directory")
        src = gdb_dirs[0]
        final = data_dir / src.name
        if final.exists():
            shutil.rmtree(final)
        shutil.move(str(src), str(final))
        shutil.rmtree(tmp, ignore_errors=True)
        rel = final.name
    else:
        dest.write_bytes(content)
        rel = safe_name

    return inspect_data_path(slug, rel)


def _maps_list(root: dict[str, Any]) -> list[dict[str, Any]]:
    raw = root.get("maps")
    if raw is None:
        raw = []
        root["maps"] = raw
    if not isinstance(raw, list):
        raise ValueError("maps must be a list")
    return raw


def append_map_entry(slug: str, payload: dict[str, Any]) -> dict[str, Any]:
    preset_path = _preset_path(slug)
    entry = ProjectMapEntry.model_validate(payload)
    y, root = read_preset_yaml_tree(preset_path)
    maps_raw = _maps_list(root)
    if any(isinstance(m, dict) and str(m.get("id")) == entry.id for m in maps_raw):
        raise ValueError(f"map id already exists: {entry.id!r}")
    maps_raw.append(entry.model_dump(mode="json", exclude_none=True))
    parse_preset_dict(root)
    dump_preset_yaml_document(y, root, preset_path)
    preset = load_preset(preset_path)
    return _map_entry_dict(entry, preset, preset_path, slug=slug)


def patch_map_entry(slug: str, map_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    preset_path = _preset_path(slug)
    y, root = read_preset_yaml_tree(preset_path)
    maps_raw = _maps_list(root)
    idx = next((i for i, m in enumerate(maps_raw) if isinstance(m, dict) and str(m.get("id")) == map_id), None)
    if idx is None:
        raise KeyError(f"unknown map id: {map_id!r}")
    current = dict(maps_raw[idx]) if isinstance(maps_raw[idx], dict) else {}
    current.update({k: v for k, v in payload.items() if v is not None})
    current["id"] = map_id
    entry = ProjectMapEntry.model_validate(current)
    maps_raw[idx] = entry.model_dump(mode="json", exclude_none=True)
    parse_preset_dict(root)
    dump_preset_yaml_document(y, root, preset_path)
    preset = load_preset(preset_path)
    return _map_entry_dict(entry, preset, preset_path, slug=slug)


def patch_map_display_mode(slug: str, map_id: str, mode: str) -> dict[str, Any]:
    visible = mode == "all"
    return patch_map_entry(slug, map_id, {"visible": visible})


def patch_eligible_display_mode(slug: str, mode: str) -> dict[str, Any]:
    visible = mode == "all"
    return patch_eligible_visibility(slug, visible)


def patch_eligible_visibility(slug: str, visible: bool) -> dict[str, Any]:
    preset_path = _preset_path(slug)
    y, root = read_preset_yaml_tree(preset_path)
    bundle = root.get("bundle")
    if not isinstance(bundle, dict):
        bundle = {}
        root["bundle"] = bundle
    kmz = bundle.get("kmz")
    if not isinstance(kmz, dict):
        kmz = {}
        bundle["kmz"] = kmz
    layers = kmz.get("layers")
    if not isinstance(layers, dict):
        layers = {}
        kmz["layers"] = layers
    layers["eligible"] = bool(visible)
    parse_preset_dict(root)
    dump_preset_yaml_document(y, root, preset_path)
    preset = load_preset(preset_path)
    return _eligible_catalog_entry(preset, preset_path, slug=slug)


def delete_map_entry(slug: str, map_id: str) -> None:
    preset_path = _preset_path(slug)
    y, root = read_preset_yaml_tree(preset_path)
    maps_raw = _maps_list(root)
    kept = [m for m in maps_raw if not (isinstance(m, dict) and str(m.get("id")) == map_id)]
    if len(kept) == len(maps_raw):
        raise KeyError(f"unknown map id: {map_id!r}")
    root["maps"] = kept
    parse_preset_dict(root)
    dump_preset_yaml_document(y, root, preset_path)


def run_maps_rebuild(
    slug: str,
    *,
    verbose_log: Callable[[str], None] | None = None,
    progress_log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    def vlog(msg: str) -> None:
        if verbose_log:
            verbose_log(msg)

    def plog(msg: str) -> None:
        if progress_log:
            progress_log(msg)

    preset_path = _preset_path(slug)
    preset = load_preset(preset_path)
    require_bundle_config(preset)
    data_dir = resolved_preset_data_dir(preset_path)
    clips_root = resolved_preset_clips_dir(preset_path)
    bundle_dir = resolved_bundle_dir(preset_path=preset_path)
    kml_overlay = preset.bundle.kml_overlay if preset.bundle else None

    mask_body = aoi_inputs_fingerprint_body(preset, data_dir)
    aoi_poly = load_composite_aoi_polygon(preset, data_dir)
    gdb_fp = _file_tree_mtime_size_fingerprint

    plog(f"maps rebuild: {len(preset.maps)} map(s), clips={clips_root}")
    layers, _planned = plan_clip_layer_jobs(preset=preset, data_dir=data_dir, clips_root=clips_root)

    for job in layers:
        resolved = resolve_land_use_gdb_path(data_dir, job.preset_path)
        plog(f"clip [{job.role}] {job.preset_path}::{job.layer}")
        build_clip_layer(
            clips_root,
            role=job.role,
            preset_path=job.preset_path,
            resolved=resolved,
            layer=job.layer,
            where=job.where,
            mask_body=mask_body,
            gdb_tree=gdb_fp(resolved),
            aoi_poly_4326=aoi_poly,
            kml_label=_kml_label_gdb_job(job.preset_path, job.layer),
            kml_overlay=kml_overlay,
            store_crs_3857=job.role in ("include", "exclude"),
            verbose_log=verbose_log,
            progress_log=progress_log,
        )

    aoi_sha = build_composite_aoi(
        preset=preset,
        data_dir=data_dir,
        clips_root=clips_root,
        mask_body=mask_body,
        kml_overlay=kml_overlay,
        verbose_log=verbose_log,
        progress_log=progress_log,
    )
    include_sha = build_composite_include(
        preset=preset,
        data_dir=data_dir,
        clips_root=clips_root,
        mask_body=mask_body,
        kml_overlay=kml_overlay,
        verbose_log=verbose_log,
        progress_log=progress_log,
    )
    exclude_sha = build_composite_exclude(
        preset=preset,
        data_dir=data_dir,
        clips_root=clips_root,
        mask_body=mask_body,
        kml_overlay=kml_overlay,
        verbose_log=verbose_log,
        progress_log=progress_log,
    )
    result = build_eligible_workspace(
        preset=preset,
        data_dir=data_dir,
        clips_root=clips_root,
        mask_body=mask_body,
        kml_overlay=kml_overlay,
        verbose_log=verbose_log,
        progress_log=progress_log,
    )

    ref_map = plan_reference_entries(preset=preset, data_dir=data_dir, clips_root=clips_root)
    for ent in preset_general_overlays(preset):
        plog(f"general_overlay {ent.id}")
        build_reference_entry(
            entry_id=ent.id,
            preset=preset,
            data_dir=data_dir,
            clips_root=clips_root,
            aoi_sha=aoi_sha,
            kml_overlay=kml_overlay,
            verbose_log=verbose_log,
            progress_log=progress_log,
        )

    write_bundle_resolve(
        bundle_dir,
        clips_root=clips_root,
        aoi_sha=aoi_sha,
        include_sha=include_sha,
        exclude_sha=exclude_sha,
        eligible_sha=result.eligible_sha,
        reference=ref_map or None,
    )
    vlog("maps rebuild done")
    return {
        "aoi_sha": aoi_sha,
        "include_sha": include_sha,
        "exclude_sha": exclude_sha,
        "eligible_sha": result.eligible_sha,
    }
