"""Land GDB overlay registry, cache, and GeoJSON serve helpers."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import geopandas as gpd
from shapely import make_valid
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from peaky_finders.core.preset import (
    LandLayerEntry,
    LandLayerRole,
    LandLayerStyle,
    LandSidebar,
    LandSidebarFolder,
    LandSourceEntry,
    Preset,
    load_preset,
    resolved_preset_cache_dir,
    slugify_files_segment,
    update_preset_yaml_tree,
)
from peaky_finders.serve.land_import import (
    gdf_to_feature_collection_geojson,
    land_layer_entry_digest,
    read_land_layer_gdf,
    resolve_land_source_path,
)

_GEOJSON_SIMPLIFY_TOLERANCE_DEG = 0.0001
_AOI_DIGEST_NONE = "none"


def resolved_land_cache_dir(preset_path: Path) -> Path:
    return resolved_preset_cache_dir(preset_path) / "land"


def _aoi_memo_dir(cache_root: Path) -> Path:
    return cache_root / "aoi"


def unique_folder_id(existing: set[str], stem: str) -> str:
    slug = slugify_files_segment(stem)
    if not slug:
        slug = "folder"
    if slug not in existing:
        return slug
    n = 2
    while f"{slug}-{n}" in existing:
        n += 1
    return f"{slug}-{n}"


def unique_source_id(existing: set[str], stem: str) -> str:
    slug = slugify_files_segment(stem)
    if slug.endswith(".gdb"):
        slug = slugify_files_segment(slug[:-4])
    if not slug:
        slug = "land-source"
    if slug not in existing:
        return slug
    n = 2
    while f"{slug}-{n}" in existing:
        n += 1
    return f"{slug}-{n}"


def _all_land_source_ids(preset: Preset) -> set[str]:
    return set(preset.land.sources.keys())


def normalize_land_sidebar(preset: Preset) -> LandSidebar:
    """Prune stale source ids and append new sources to unfiled."""
    valid = _all_land_source_ids(preset)
    raw = preset.land.sidebar
    if raw is None:
        return LandSidebar(unfiled_sources=sorted(valid))

    folders: list[LandSidebarFolder] = []
    assigned: set[str] = set()
    for folder in raw.folders:
        kept = [sid for sid in folder.sources if sid in valid]
        assigned.update(kept)
        folders.append(
            LandSidebarFolder(id=folder.id, label=folder.label, sources=kept),
        )

    unfiled: list[str] = [sid for sid in raw.unfiled_sources if sid in valid and sid not in assigned]
    assigned.update(unfiled)
    for sid in sorted(valid):
        if sid not in assigned:
            unfiled.append(sid)
    return LandSidebar(folders=folders, unfiled_sources=unfiled)


def serialize_land_sidebar(preset: Preset) -> dict[str, Any]:
    sidebar = normalize_land_sidebar(preset)
    return {
        "folders": [
            {"id": f.id, "label": f.label, "sources": list(f.sources)}
            for f in sidebar.folders
        ],
        "unfiledSources": list(sidebar.unfiled_sources),
    }


def _sidebar_to_yaml(sidebar: LandSidebar) -> dict[str, Any]:
    return {
        "folders": [
            {"id": f.id, "label": f.label, "sources": list(f.sources)}
            for f in sidebar.folders
        ],
        "unfiled_sources": list(sidebar.unfiled_sources),
    }


def _ensure_land_sidebar_raw(land_raw: dict[str, Any]) -> dict[str, Any]:
    sidebar_raw = land_raw.get("sidebar")
    if not isinstance(sidebar_raw, dict):
        sidebar_raw = {}
        land_raw["sidebar"] = sidebar_raw
    if "folders" not in sidebar_raw:
        sidebar_raw["folders"] = []
    if "unfiled_sources" not in sidebar_raw:
        sidebar_raw["unfiled_sources"] = []
    return sidebar_raw


def _remove_source_from_sidebar_raw(sidebar_raw: dict[str, Any], source_id: str) -> None:
    folders = sidebar_raw.get("folders")
    if isinstance(folders, list):
        for folder in folders:
            if not isinstance(folder, dict):
                continue
            sources = folder.get("sources")
            if isinstance(sources, list):
                folder["sources"] = [s for s in sources if str(s) != source_id]
    unfiled = sidebar_raw.get("unfiled_sources")
    if isinstance(unfiled, list):
        sidebar_raw["unfiled_sources"] = [s for s in unfiled if str(s) != source_id]


def patch_land_sidebar(preset_path: Path, sidebar: LandSidebar) -> dict[str, Any]:
    job = load_preset(preset_path)
    valid = _all_land_source_ids(job)
    for folder in sidebar.folders:
        for sid in folder.sources:
            if sid not in valid:
                raise ValueError(f"unknown land source: {sid}")
    for sid in sidebar.unfiled_sources:
        if sid not in valid:
            raise ValueError(f"unknown land source: {sid}")

    validated = LandSidebar.model_validate(sidebar.model_dump(mode="python"))

    def mutator(_yaml_rt: Any, root: dict[str, Any]) -> dict[str, Any]:
        land_raw = root.get("land")
        if not isinstance(land_raw, dict):
            land_raw = {}
            root["land"] = land_raw
        sidebar_raw = _ensure_land_sidebar_raw(land_raw)
        sidebar_raw.clear()
        sidebar_raw.update(_sidebar_to_yaml(validated))
        return {
            "folders": [
                {"id": f.id, "label": f.label, "sources": list(f.sources)}
                for f in validated.folders
            ],
            "unfiledSources": list(validated.unfiled_sources),
        }

    return update_preset_yaml_tree(preset_path, mutator, validate=True)


    slug = slugify_files_segment(stem)
    if slug.endswith(".gdb"):
        slug = slugify_files_segment(slug[:-4])
    if not slug:
        slug = "land-source"
    if slug not in existing:
        return slug
    n = 2
    while f"{slug}-{n}" in existing:
        n += 1
    return f"{slug}-{n}"


def _layer_cache_path(cache_root: Path, source_id: str, layer_key: str) -> Path:
    safe_layer = slugify_files_segment(layer_key) or "layer"
    return cache_root / source_id / f"{safe_layer}.geojson"


def _manifest_path(cache_root: Path) -> Path:
    return cache_root / "manifest.json"


def _read_manifest(cache_root: Path) -> dict[str, Any]:
    path = _manifest_path(cache_root)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write_manifest(cache_root: Path, manifest: dict[str, Any]) -> None:
    cache_root.mkdir(parents=True, exist_ok=True)
    _manifest_path(cache_root).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _serialize_layer_style(style: LandLayerStyle | dict[str, LandLayerStyle] | None) -> Any:
    if style is None:
        return None
    if isinstance(style, LandLayerStyle):
        return {"color": style.color, "opacity": style.opacity}
    return {
        key: {"color": val.color, "opacity": val.opacity}
        for key, val in sorted(style.items())
    }


def iter_aoi_layer_entries(preset: Preset) -> list[tuple[str, LandLayerEntry]]:
    rows: list[tuple[str, LandLayerEntry]] = []
    for source_id, source in sorted(preset.land.sources.items()):
        for layer in source.layers:
            if layer.role == LandLayerRole.AOI:
                rows.append((source_id, layer))
    return rows


def aoi_digest(preset_path: Path) -> str:
    preset = load_preset(preset_path)
    aoi_rows = iter_aoi_layer_entries(preset)
    if not aoi_rows:
        return _AOI_DIGEST_NONE
    project_dir = preset_path.parent
    parts: list[str] = []
    for source_id, layer in aoi_rows:
        source = preset.land.sources[source_id]
        data_path = resolve_land_source_path(project_dir, source.path)
        stat = data_path.stat()
        parts.append(
            "|".join(
                (
                    source_id,
                    layer.layer_key(),
                    land_layer_entry_digest(layer),
                    str(stat.st_mtime_ns),
                    str(stat.st_size),
                )
            )
        )
    payload = "\n".join(parts)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _aoi_memo_path(cache_root: Path, digest: str) -> Path:
    return _aoi_memo_dir(cache_root) / f"{digest}.wkb"


def build_uber_aoi_geometry(preset_path: Path) -> BaseGeometry | None:
    digest = aoi_digest(preset_path)
    if digest == _AOI_DIGEST_NONE:
        return None
    cache_root = resolved_land_cache_dir(preset_path)
    memo_path = _aoi_memo_path(cache_root, digest)
    if memo_path.is_file():
        try:
            from shapely import wkb

            return wkb.loads(memo_path.read_bytes())
        except (OSError, ValueError):
            pass

    preset = load_preset(preset_path)
    project_dir = preset_path.parent
    pieces: list[BaseGeometry] = []
    for source_id, layer in iter_aoi_layer_entries(preset):
        source = preset.land.sources[source_id]
        data_path = resolve_land_source_path(project_dir, source.path)
        gdf = read_land_layer_gdf(data_path, layer)
        if gdf.empty:
            continue
        for geom in gdf.geometry:
            if geom is None or geom.is_empty:
                continue
            pieces.append(geom)
    if not pieces:
        return None

    union = make_valid(unary_union(pieces))
    memo_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from shapely import wkb

        memo_path.write_bytes(wkb.dumps(union))
    except OSError:
        pass
    return union


def _clip_gdf_to_aoi(gdf: gpd.GeoDataFrame, aoi: BaseGeometry) -> gpd.GeoDataFrame:
    if gdf.empty:
        return gdf
    aoi_gdf = gpd.GeoDataFrame(geometry=[aoi], crs="EPSG:4326")
    clipped = gpd.clip(gdf, aoi_gdf)
    return clipped if clipped is not None else gdf.iloc[0:0].copy()


def purge_clipped_serve_caches(preset_path: Path) -> None:
    """Delete serve GeoJSON for all non-AOI layers and clear AOI memo cache."""
    preset = load_preset(preset_path)
    cache_root = resolved_land_cache_dir(preset_path)
    manifest = _read_manifest(cache_root)
    keys_to_drop: list[str] = []
    for source_id, source in preset.land.sources.items():
        for layer in source.layers:
            if layer.role == LandLayerRole.AOI:
                continue
            layer_key = layer.layer_key()
            safe_layer = slugify_files_segment(layer_key) or "layer"
            out_path = _layer_cache_path(cache_root, source_id, layer_key)
            out_path.unlink(missing_ok=True)
            keys_to_drop.append(f"{source_id}/{safe_layer}")
    for key in keys_to_drop:
        manifest.pop(key, None)
    _write_manifest(cache_root, manifest)
    aoi_dir = _aoi_memo_dir(cache_root)
    if aoi_dir.is_dir():
        shutil.rmtree(aoi_dir, ignore_errors=True)


def _maybe_purge_on_aoi_change(preset_path: Path, prev_digest: str) -> None:
    if aoi_digest(preset_path) != prev_digest:
        purge_clipped_serve_caches(preset_path)


def serialize_land_layer(
    entry: LandLayerEntry,
    *,
    digest: str | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "name": entry.name,
        "key": entry.layer_key(),
    }
    if entry.id:
        row["id"] = entry.id
    if entry.role is not None:
        row["role"] = entry.role.value
    if entry.include:
        row["include"] = [{"field": f.field, "values": list(f.values)} for f in entry.include]
    if entry.exclude:
        row["exclude"] = [{"field": f.field, "values": list(f.values)} for f in entry.exclude]
    if entry.label_field:
        row["labelField"] = entry.label_field
    if entry.style_field:
        row["styleField"] = entry.style_field
    style_payload = _serialize_layer_style(entry.style)
    if style_payload is not None:
        row["style"] = style_payload
    if digest:
        row["digest"] = digest
    return row


def serialize_land_source(
    source_id: str,
    entry: LandSourceEntry,
    *,
    manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    layers: list[dict[str, Any]] = []
    for layer in entry.layers:
        digest: str | None = None
        if manifest is not None:
            manifest_key = f"{source_id}/{slugify_files_segment(layer.layer_key()) or 'layer'}"
            cached = manifest.get(manifest_key)
            if isinstance(cached, dict):
                raw_digest = cached.get("digest")
                if isinstance(raw_digest, str) and raw_digest:
                    digest = raw_digest
        layers.append(serialize_land_layer(layer, digest=digest))
    return {
        "id": source_id,
        "path": entry.path,
        "label": entry.label or source_id,
        "layers": layers,
    }


def serialize_land_sources(preset_path: Path) -> list[dict[str, Any]]:
    job = load_preset(preset_path)
    manifest = _read_manifest(resolved_land_cache_dir(preset_path))
    rows: list[dict[str, Any]] = []
    for source_id, entry in sorted(job.land.sources.items()):
        rows.append(serialize_land_source(source_id, entry, manifest=manifest))
    return rows


def list_land_payload(preset_path: Path) -> dict[str, Any]:
    job = load_preset(preset_path)
    return {
        "sources": serialize_land_sources(preset_path),
        "sidebar": serialize_land_sidebar(job),
        "aoiDigest": aoi_digest(preset_path),
    }


def _layer_entry_to_yaml(layer: LandLayerEntry) -> dict[str, Any]:
    row: dict[str, Any] = {"name": layer.name}
    if layer.id:
        row["id"] = layer.id
    if layer.role is not None:
        row["role"] = layer.role.value
    if layer.include:
        row["include"] = [{"field": f.field, "values": list(f.values)} for f in layer.include]
    if layer.exclude:
        row["exclude"] = [{"field": f.field, "values": list(f.values)} for f in layer.exclude]
    if layer.label_field:
        row["label_field"] = layer.label_field
    if layer.style_field:
        row["style_field"] = layer.style_field
    if layer.style is not None:
        if isinstance(layer.style, LandLayerStyle):
            row["style"] = layer.style.model_dump()
        else:
            row["style"] = {k: v.model_dump() for k, v in sorted(layer.style.items())}
    return row


def _normalize_layer_entries(layers: list[LandLayerEntry | str | dict[str, Any]]) -> list[LandLayerEntry]:
    if not isinstance(layers, list) or not layers:
        raise ValueError("layers must be a non-empty list")
    out: list[LandLayerEntry] = []
    seen: set[str] = set()
    for item in layers:
        if isinstance(item, str):
            entry = LandLayerEntry(name=item.strip())
        elif isinstance(item, LandLayerEntry):
            entry = item
        elif isinstance(item, dict):
            entry = LandLayerEntry.model_validate(item)
        else:
            raise ValueError("each layer must be a string or layer object")
        key = entry.layer_key()
        if key in seen:
            raise ValueError(f"duplicate land layer key: {key}")
        seen.add(key)
        out.append(entry)
    return out


def parse_land_layer_entries(raw_layers: Any) -> list[LandLayerEntry]:
    return _normalize_layer_entries(raw_layers)


def add_land_source(
    preset_path: Path,
    *,
    path: str,
    layers: list[LandLayerEntry],
    label: str | None = None,
    source_id: str | None = None,
) -> dict[str, Any]:
    project_dir = preset_path.parent
    resolve_land_source_path(project_dir, path)
    layer_entries = _normalize_layer_entries(layers)
    gdb_stem = Path(path).stem
    prev_aoi_digest = aoi_digest(preset_path)

    def mutator(_yaml_rt: Any, root: dict[str, Any]) -> dict[str, Any]:
        land_raw = root.get("land")
        if not isinstance(land_raw, dict):
            land_raw = {}
            root["land"] = land_raw
        sources_raw = land_raw.get("sources")
        if not isinstance(sources_raw, dict):
            sources_raw = {}
            land_raw["sources"] = sources_raw

        existing = {str(k) for k in sources_raw.keys()}
        sid = str(source_id).strip() if source_id else unique_source_id(existing, gdb_stem)
        if not sid:
            raise ValueError("source id is required")
        if sid in existing:
            raise ValueError(f"land source id already exists: {sid}")

        entry: dict[str, Any] = {
            "path": path.strip().replace("\\", "/"),
            "layers": [_layer_entry_to_yaml(layer) for layer in layer_entries],
        }
        if label and str(label).strip():
            entry["label"] = str(label).strip()
        sources_raw[sid] = entry
        sidebar_raw = _ensure_land_sidebar_raw(land_raw)
        unfiled = sidebar_raw.get("unfiled_sources")
        if isinstance(unfiled, list):
            unfiled.append(sid)
        return serialize_land_source(sid, LandSourceEntry.model_validate(entry))

    result = update_preset_yaml_tree(preset_path, mutator, validate=True)
    _maybe_purge_on_aoi_change(preset_path, prev_aoi_digest)
    return result


def patch_land_source(
    preset_path: Path,
    source_id: str,
    *,
    layers: list[LandLayerEntry] | None = None,
    label: str | None = None,
) -> dict[str, Any]:
    sid = str(source_id).strip()
    if not sid:
        raise ValueError("source id is required")
    prev_aoi_digest = aoi_digest(preset_path)

    def mutator(_yaml_rt: Any, root: dict[str, Any]) -> dict[str, Any]:
        land_raw = root.get("land")
        if not isinstance(land_raw, dict):
            raise ValueError(f"unknown land source: {sid}")
        sources_raw = land_raw.get("sources")
        if not isinstance(sources_raw, dict) or sid not in sources_raw:
            raise ValueError(f"unknown land source: {sid}")
        entry_raw = sources_raw[sid]
        if not isinstance(entry_raw, dict):
            raise ValueError(f"invalid land source entry: {sid}")

        project_dir = preset_path.parent
        path = str(entry_raw.get("path", "")).strip()
        resolve_land_source_path(project_dir, path)

        if layers is not None:
            layer_entries = _normalize_layer_entries(layers)
            entry_raw["layers"] = [_layer_entry_to_yaml(layer) for layer in layer_entries]
        if label is not None:
            trimmed = str(label).strip()
            if trimmed:
                entry_raw["label"] = trimmed
            elif "label" in entry_raw:
                del entry_raw["label"]

        validated = LandSourceEntry.model_validate(entry_raw)
        _invalidate_removed_layer_caches(preset_path, sid, validated.layers)
        return serialize_land_source(sid, validated)

    result = update_preset_yaml_tree(preset_path, mutator, validate=True)
    _maybe_purge_on_aoi_change(preset_path, prev_aoi_digest)
    return result


def delete_land_source(preset_path: Path, source_id: str) -> None:
    sid = str(source_id).strip()
    if not sid:
        raise ValueError("source id is required")
    prev_aoi_digest = aoi_digest(preset_path)

    def mutator(_yaml_rt: Any, root: dict[str, Any]) -> None:
        land_raw = root.get("land")
        if not isinstance(land_raw, dict):
            raise ValueError(f"unknown land source: {sid}")
        sources_raw = land_raw.get("sources")
        if not isinstance(sources_raw, dict) or sid not in sources_raw:
            raise ValueError(f"unknown land source: {sid}")
        del sources_raw[sid]
        sidebar_raw = land_raw.get("sidebar")
        if isinstance(sidebar_raw, dict):
            _remove_source_from_sidebar_raw(sidebar_raw, sid)
        if not sources_raw:
            if "sources" in land_raw:
                del land_raw["sources"]
            if not land_raw:
                del root["land"]

    update_preset_yaml_tree(preset_path, mutator, validate=True)
    cache_dir = resolved_land_cache_dir(preset_path) / sid
    if cache_dir.is_dir():
        shutil.rmtree(cache_dir, ignore_errors=True)
    manifest = _read_manifest(resolved_land_cache_dir(preset_path))
    prefix = f"{sid}/"
    for key in [k for k in manifest if str(k).startswith(prefix)]:
        del manifest[key]
    _write_manifest(resolved_land_cache_dir(preset_path), manifest)
    _maybe_purge_on_aoi_change(preset_path, prev_aoi_digest)


def _invalidate_removed_layer_caches(
    preset_path: Path,
    source_id: str,
    layers: list[LandLayerEntry],
) -> None:
    cache_root = resolved_land_cache_dir(preset_path) / source_id
    if not cache_root.is_dir():
        return
    keep = {layer.layer_key() for layer in layers}
    for path in cache_root.glob("*.geojson"):
        if path.stem not in keep:
            path.unlink(missing_ok=True)


def _layer_digest(
    data_path: Path,
    layer: LandLayerEntry,
    *,
    aoi_digest_value: str,
) -> str:
    stat = data_path.stat()
    spec = land_layer_entry_digest(layer)
    payload = f"{data_path}|{layer.name}|{spec}|{stat.st_mtime_ns}|{stat.st_size}"
    if layer.role != LandLayerRole.AOI:
        payload += f"|aoi:{aoi_digest_value}"
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def ensure_layer_geojson(
    preset_path: Path,
    source_id: str,
    layer_key: str,
) -> tuple[Path, list[float], str]:
    """Build or reuse cached GeoJSON; return path, WGS84 bbox, and cache digest."""
    sid = str(source_id).strip()
    key = str(layer_key).strip()
    if not sid or not key:
        raise ValueError("source id and layer key are required")

    job = load_preset(preset_path)
    source = job.land.sources.get(sid)
    if source is None:
        raise ValueError(f"unknown land source: {sid}")
    layer_entry = source.layer_by_key(key)
    if layer_entry is None:
        raise ValueError(f"layer key {key!r} not registered on source {sid!r}")

    project_dir = preset_path.parent
    data_path = resolve_land_source_path(project_dir, source.path)
    cache_root = resolved_land_cache_dir(preset_path)
    out_path = _layer_cache_path(cache_root, sid, key)
    aoi_digest_value = aoi_digest(preset_path)
    digest = _layer_digest(data_path, layer_entry, aoi_digest_value=aoi_digest_value)
    manifest_key = f"{sid}/{slugify_files_segment(key) or 'layer'}"
    manifest = _read_manifest(cache_root)
    cached = manifest.get(manifest_key)
    if (
        out_path.is_file()
        and isinstance(cached, dict)
        and cached.get("digest") == digest
    ):
        bbox = cached.get("bbox")
        if isinstance(bbox, list) and len(bbox) == 4:
            return out_path, [float(x) for x in bbox], digest

    gdf = read_land_layer_gdf(data_path, layer_entry)
    if layer_entry.role != LandLayerRole.AOI:
        aoi_geom = build_uber_aoi_geometry(preset_path)
        if aoi_geom is not None:
            gdf = _clip_gdf_to_aoi(gdf, aoi_geom)

    if gdf.empty:
        geojson = {"type": "FeatureCollection", "features": []}
        bbox = [0.0, 0.0, 0.0, 0.0]
    else:
        minx, miny, maxx, maxy = gdf.total_bounds
        bbox = [float(minx), float(miny), float(maxx), float(maxy)]
        geojson = gdf_to_feature_collection_geojson(
            gdf,
            simplify_tolerance_deg=_GEOJSON_SIMPLIFY_TOLERANCE_DEG,
            label_field=layer_entry.label_field,
            style_field=layer_entry.style_field,
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(geojson) + "\n", encoding="utf-8")
    manifest[manifest_key] = {
        "digest": digest,
        "bbox": bbox,
        "source_id": sid,
        "layer_key": key,
        "layer": layer_entry.name,
        "path": source.path,
    }
    _write_manifest(cache_root, manifest)
    return out_path, bbox, digest


def read_layer_geojson_bytes(preset_path: Path, source_id: str, layer_key: str) -> tuple[bytes, str]:
    path, _bbox, digest = ensure_layer_geojson(preset_path, source_id, layer_key)
    return path.read_bytes(), digest


def resolve_layer_entry_from_source(source: LandSourceEntry, layer_key: str) -> LandLayerEntry:
    entry = source.layer_by_key(layer_key)
    if entry is None:
        raise ValueError(f"unknown layer key: {layer_key}")
    return entry
