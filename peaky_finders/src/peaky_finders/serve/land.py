"""Land GDB overlay registry, cache, and GeoJSON serve helpers."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Mapping

import pyogrio

from peaky_finders.core.preset import (
    LandLayerStyle,
    LandSourceEntry,
    load_preset,
    resolved_preset_cache_dir,
    slugify_files_segment,
    update_preset_yaml_tree,
)
from peaky_finders.serve.land_import import gdf_to_feature_collection_geojson, resolve_land_gdb_path

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


def _source_digest(gdb_path: Path, layer: str) -> str:
    stat = gdb_path.stat()
    payload = f"{gdb_path}|{layer}|{stat.st_mtime_ns}|{stat.st_size}"
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _layer_cache_path(cache_root: Path, source_id: str, layer: str) -> Path:
    safe_layer = slugify_files_segment(layer) or "layer"
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


def serialize_land_sources(preset_path: Path) -> list[dict[str, Any]]:
    job = load_preset(preset_path)
    rows: list[dict[str, Any]] = []
    for source_id, entry in sorted(job.land.sources.items()):
        rows.append(serialize_land_source(source_id, entry))
    return rows


def serialize_land_source(source_id: str, entry: LandSourceEntry) -> dict[str, Any]:
    layer_styles: dict[str, dict[str, Any]] = {}
    for layer_name, style in sorted(entry.layer_styles.items()):
        layer_styles[layer_name] = {
            "color": style.color,
            "opacity": style.opacity,
        }
    return {
        "id": source_id,
        "path": entry.path,
        "label": entry.label or source_id,
        "layers": list(entry.layers),
        "layerStyles": layer_styles,
    }


def list_land_payload(preset_path: Path) -> dict[str, Any]:
    return {"sources": serialize_land_sources(preset_path)}


def add_land_source(
    preset_path: Path,
    *,
    path: str,
    layers: list[str],
    label: str | None = None,
    source_id: str | None = None,
    layer_styles: dict[str, LandLayerStyle] | None = None,
) -> dict[str, Any]:
    project_dir = preset_path.parent
    resolve_land_gdb_path(project_dir, path)
    layer_names = _normalize_layer_names(layers)
    styles = _normalize_layer_styles(layer_names, layer_styles)
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
            "layers": layer_names,
        }
        if label and str(label).strip():
            entry["label"] = str(label).strip()
        if styles:
            entry["layer_styles"] = {
                layer: style.model_dump() for layer, style in sorted(styles.items())
            }
        sources_raw[sid] = entry
        return serialize_land_source(sid, LandSourceEntry.model_validate(entry))

    return update_preset_yaml_tree(preset_path, mutator, validate=True)


def patch_land_source(
    preset_path: Path,
    source_id: str,
    *,
    layers: list[str] | None = None,
    label: str | None = None,
    layer_styles: dict[str, LandLayerStyle] | None = None,
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
            entry_raw["layers"] = _normalize_layer_names(layers)
            styles_raw = entry_raw.get("layer_styles")
            if isinstance(styles_raw, dict):
                allowed = set(entry_raw["layers"])
                pruned = {str(k): v for k, v in styles_raw.items() if str(k) in allowed}
                if pruned:
                    entry_raw["layer_styles"] = pruned
                elif "layer_styles" in entry_raw:
                    del entry_raw["layer_styles"]
        if label is not None:
            trimmed = str(label).strip()
            if trimmed:
                entry_raw["label"] = trimmed
            elif "label" in entry_raw:
                del entry_raw["label"]
        if layer_styles is not None:
            layer_names = _normalize_layer_names(entry_raw.get("layers") or [])
            styles = _normalize_layer_styles(layer_names, layer_styles)
            if styles:
                entry_raw["layer_styles"] = {
                    layer: style.model_dump() for layer, style in sorted(styles.items())
                }
            elif "layer_styles" in entry_raw:
                del entry_raw["layer_styles"]

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


def _normalize_layer_names(layers: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in layers:
        name = str(item).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    if not out:
        raise ValueError("layers must contain at least one layer name")
    return out


def _normalize_layer_styles(
    layer_names: list[str],
    layer_styles: dict[str, LandLayerStyle] | Mapping[str, Any] | None,
) -> dict[str, LandLayerStyle]:
    if not layer_styles:
        return {}
    allowed = set(layer_names)
    out: dict[str, LandLayerStyle] = {}
    for key, raw in layer_styles.items():
        name = str(key).strip()
        if not name or name not in allowed:
            continue
        if isinstance(raw, LandLayerStyle):
            out[name] = raw
        else:
            out[name] = LandLayerStyle.model_validate(raw)
    return out


def _invalidate_removed_layer_caches(preset_path: Path, source_id: str, layers: list[str]) -> None:
    cache_root = resolved_land_cache_dir(preset_path) / source_id
    if not cache_root.is_dir():
        return
    keep = {slugify_files_segment(name) or "layer" for name in layers}
    for path in cache_root.glob("*.geojson"):
        if path.stem not in keep:
            path.unlink(missing_ok=True)


def ensure_layer_geojson(
    preset_path: Path,
    source_id: str,
    layer: str,
) -> tuple[Path, list[float]]:
    """Build or reuse cached GeoJSON; return path and WGS84 bbox [minx, miny, maxx, maxy]."""
    sid = str(source_id).strip()
    layer_name = str(layer).strip()
    if not sid or not layer_name:
        raise ValueError("source id and layer are required")

    job = load_preset(preset_path)
    entry = job.land.sources.get(sid)
    if entry is None:
        raise ValueError(f"unknown land source: {sid}")
    if layer_name not in entry.layers:
        raise ValueError(f"layer {layer_name!r} not registered on source {sid!r}")

    project_dir = preset_path.parent
    gdb_path = resolve_land_gdb_path(project_dir, entry.path)
    cache_root = resolved_land_cache_dir(preset_path)
    out_path = _layer_cache_path(cache_root, sid, layer_name)
    digest = _source_digest(gdb_path, layer_name)
    manifest_key = f"{sid}/{slugify_files_segment(layer_name) or 'layer'}"
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

    gdf = pyogrio.read_dataframe(gdb_path, layer=layer_name, read_geometry=True)
    if gdf.empty:
        geojson = {"type": "FeatureCollection", "features": []}
        bbox = [0.0, 0.0, 0.0, 0.0]
    else:
        if gdf.crs is None:
            gdf = gdf.set_crs("EPSG:4326")
        else:
            gdf = gdf.to_crs("EPSG:4326")
        minx, miny, maxx, maxy = gdf.total_bounds
        bbox = [float(minx), float(miny), float(maxx), float(maxy)]
        geojson = gdf_to_feature_collection_geojson(
            gdf,
            simplify_tolerance_deg=_GEOJSON_SIMPLIFY_TOLERANCE_DEG,
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(geojson) + "\n", encoding="utf-8")
    manifest[manifest_key] = {
        "digest": digest,
        "bbox": bbox,
        "source_id": sid,
        "layer": layer_name,
        "path": entry.path,
    }
    _write_manifest(cache_root, manifest)
    return out_path, bbox


def read_layer_geojson_bytes(preset_path: Path, source_id: str, layer: str) -> bytes:
    path, _bbox = ensure_layer_geojson(preset_path, source_id, layer)
    return path.read_bytes()
