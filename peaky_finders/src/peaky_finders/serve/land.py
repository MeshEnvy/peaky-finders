"""Land GDB overlay registry, cache, and GeoJSON serve helpers."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Mapping

from peaky_finders.core.preset import (
    LandLayerEntry,
    LandLayerStyle,
    LandSourceEntry,
    land_layer_key,
    load_preset,
    resolved_preset_cache_dir,
    slugify_files_segment,
    update_preset_yaml_tree,
)
from peaky_finders.serve.land_import import (
    land_layer_entry_digest,
    layer_entry_geojson,
    read_land_layer_gdf,
    resolve_land_gdb_path,
)

_GEOJSON_SIMPLIFY_TOLERANCE_DEG = 0.0001


def resolved_land_cache_dir(preset_path: Path) -> Path:
    return resolved_preset_cache_dir(preset_path) / "land"


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


def serialize_land_layer(entry: LandLayerEntry) -> dict[str, Any]:
    row: dict[str, Any] = {
        "name": entry.name,
        "key": entry.layer_key(),
    }
    if entry.id:
        row["id"] = entry.id
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
    return row


def serialize_land_sources(preset_path: Path) -> list[dict[str, Any]]:
    job = load_preset(preset_path)
    rows: list[dict[str, Any]] = []
    for source_id, entry in sorted(job.land.sources.items()):
        rows.append(serialize_land_source(source_id, entry))
    return rows


def serialize_land_source(source_id: str, entry: LandSourceEntry) -> dict[str, Any]:
    return {
        "id": source_id,
        "path": entry.path,
        "label": entry.label or source_id,
        "layers": [serialize_land_layer(layer) for layer in entry.layers],
    }


def list_land_payload(preset_path: Path) -> dict[str, Any]:
    return {"sources": serialize_land_sources(preset_path)}


def _layer_entry_to_yaml(layer: LandLayerEntry) -> dict[str, Any]:
    row: dict[str, Any] = {"name": layer.name}
    if layer.id:
        row["id"] = layer.id
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
    resolve_land_gdb_path(project_dir, path)
    layer_entries = _normalize_layer_entries(layers)
    gdb_stem = Path(path).stem

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
        return serialize_land_source(sid, LandSourceEntry.model_validate(entry))

    return update_preset_yaml_tree(preset_path, mutator, validate=True)


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
        resolve_land_gdb_path(project_dir, path)

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

    return update_preset_yaml_tree(preset_path, mutator, validate=True)


def delete_land_source(preset_path: Path, source_id: str) -> None:
    sid = str(source_id).strip()
    if not sid:
        raise ValueError("source id is required")

    def mutator(_yaml_rt: Any, root: dict[str, Any]) -> None:
        land_raw = root.get("land")
        if not isinstance(land_raw, dict):
            raise ValueError(f"unknown land source: {sid}")
        sources_raw = land_raw.get("sources")
        if not isinstance(sources_raw, dict) or sid not in sources_raw:
            raise ValueError(f"unknown land source: {sid}")
        del sources_raw[sid]
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


def _layer_digest(gdb_path: Path, layer: LandLayerEntry) -> str:
    stat = gdb_path.stat()
    spec = land_layer_entry_digest(layer)
    payload = f"{gdb_path}|{layer.name}|{spec}|{stat.st_mtime_ns}|{stat.st_size}"
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def ensure_layer_geojson(
    preset_path: Path,
    source_id: str,
    layer_key: str,
) -> tuple[Path, list[float]]:
    """Build or reuse cached GeoJSON; return path and WGS84 bbox."""
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
    gdb_path = resolve_land_gdb_path(project_dir, source.path)
    cache_root = resolved_land_cache_dir(preset_path)
    out_path = _layer_cache_path(cache_root, sid, key)
    digest = _layer_digest(gdb_path, layer_entry)
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
            return out_path, [float(x) for x in bbox]

    gdf = read_land_layer_gdf(gdb_path, layer_entry)
    if gdf.empty:
        geojson = {"type": "FeatureCollection", "features": []}
        bbox = [0.0, 0.0, 0.0, 0.0]
    else:
        minx, miny, maxx, maxy = gdf.total_bounds
        bbox = [float(minx), float(miny), float(maxx), float(maxy)]
        geojson = layer_entry_geojson(
            gdb_path,
            layer_entry,
            simplify_tolerance_deg=_GEOJSON_SIMPLIFY_TOLERANCE_DEG,
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
    return out_path, bbox


def read_layer_geojson_bytes(preset_path: Path, source_id: str, layer_key: str) -> bytes:
    path, _bbox = ensure_layer_geojson(preset_path, source_id, layer_key)
    return path.read_bytes()


def parse_land_layer_entries(raw_layers: Any) -> list[LandLayerEntry]:
    return _normalize_layer_entries(raw_layers)


def resolve_layer_entry_from_source(source: LandSourceEntry, layer_key: str) -> LandLayerEntry:
    entry = source.layer_by_key(layer_key)
    if entry is None:
        raise ValueError(f"unknown layer key: {layer_key}")
    return entry
