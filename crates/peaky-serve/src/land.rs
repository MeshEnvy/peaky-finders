//! Land source listing and GeoJSON layer serve (v4 cache compatible).

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use peaky_geo::resolve_land_layer_geojson_path;
use peaky_preset::{
    load_preset, slugify_files_segment, LandLayerEntry, LandLayerRole, LandLayerStyleValue,
    LandSourceEntry,
};
use serde_json::{json, Value};

pub fn resolved_land_cache_dir(preset_path: &Path) -> PathBuf {
    preset_path
        .parent()
        .unwrap_or_else(|| Path::new("."))
        .join(".peaky/cache/land")
}

fn layer_cache_path(cache_root: &Path, source_id: &str, layer_key: &str) -> PathBuf {
    let safe = slugify_files_segment(layer_key);
    let stem = if safe.is_empty() { "layer" } else { safe.as_str() };
    cache_root.join(source_id).join(format!("{stem}.geojson"))
}

fn read_manifest(cache_root: &Path) -> HashMap<String, Value> {
    let path = cache_root.join("manifest.json");
    if !path.is_file() {
        return HashMap::new();
    }
    std::fs::read_to_string(&path)
        .ok()
        .and_then(|text| serde_json::from_str(&text).ok())
        .unwrap_or_default()
}

fn manifest_digest(manifest: &HashMap<String, Value>, source_id: &str, layer_key: &str) -> Option<String> {
    let safe = slugify_files_segment(layer_key);
    let stem = if safe.is_empty() { "layer" } else { safe.as_str() };
    let key = format!("{source_id}/{stem}");
    manifest
        .get(&key)
        .and_then(|v| v.get("digest"))
        .and_then(|d| d.as_str())
        .map(str::to_string)
}

fn layer_by_key<'a>(source: &'a LandSourceEntry, layer_key: &str) -> Option<&'a LandLayerEntry> {
    source
        .layers
        .iter()
        .find(|layer| layer.layer_key() == layer_key)
}

fn serialize_layer_style(style: &Option<LandLayerStyleValue>) -> Option<Value> {
    match style {
        Some(LandLayerStyleValue::Single(s)) => Some(json!({
            "color": s.color,
            "opacity": s.opacity,
        })),
        Some(LandLayerStyleValue::Map(m)) => {
            let mut out = serde_json::Map::new();
            for (k, v) in m {
                out.insert(
                    k.clone(),
                    json!({ "color": v.color, "opacity": v.opacity }),
                );
            }
            Some(Value::Object(out))
        }
        None => None,
    }
}

fn serialize_land_layer(entry: &LandLayerEntry, digest: Option<&str>) -> Value {
    let mut row = json!({
        "name": entry.name,
        "key": entry.layer_key(),
    });
    if let Some(id) = &entry.id {
        row["id"] = json!(id);
    }
    if let Some(role) = &entry.role {
        row["role"] = json!(match role {
            LandLayerRole::Aoi => "aoi",
            LandLayerRole::Include => "include",
            LandLayerRole::Exclude => "exclude",
        });
    }
    if !entry.include.is_empty() {
        row["include"] = json!(entry.include.iter().map(|f| json!({
            "field": f.field,
            "values": f.values,
        })).collect::<Vec<_>>());
    }
    if !entry.exclude.is_empty() {
        row["exclude"] = json!(entry.exclude.iter().map(|f| json!({
            "field": f.field,
            "values": f.values,
        })).collect::<Vec<_>>());
    }
    if let Some(label_field) = &entry.label_field {
        row["labelField"] = json!(label_field);
    }
    if let Some(style_field) = &entry.style_field {
        row["styleField"] = json!(style_field);
    }
    if let Some(style) = serialize_layer_style(&entry.style) {
        row["style"] = style;
    }
    if let Some(d) = digest {
        row["digest"] = json!(d);
    }
    row
}

pub fn serialize_land_source(
    source_id: &str,
    entry: &LandSourceEntry,
    manifest: &HashMap<String, Value>,
) -> Value {
    let layers: Vec<Value> = entry
        .layers
        .iter()
        .map(|layer| {
            let digest = manifest_digest(manifest, source_id, &layer.layer_key());
            serialize_land_layer(layer, digest.as_deref())
        })
        .collect();
    json!({
        "id": source_id,
        "path": entry.path,
        "label": entry.label.as_deref().unwrap_or(source_id),
        "layers": layers,
    })
}

pub fn list_land_payload(preset_path: &Path) -> Result<Value> {
    let preset = load_preset(preset_path)?;
    let cache_root = resolved_land_cache_dir(preset_path);
    let manifest = read_manifest(&cache_root);
    let mut sources: Vec<Value> = preset
        .land
        .sources
        .iter()
        .map(|(id, entry)| serialize_land_source(id, entry, &manifest))
        .collect();
    sources.sort_by(|a, b| {
        a.get("id")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .cmp(b.get("id").and_then(|v| v.as_str()).unwrap_or(""))
    });
    let aoi_digest = peaky_geo::eligible_land_digest(preset_path)
        .unwrap_or_else(|_| "none".to_string());
    Ok(json!({
        "sources": sources,
        "sidebar": preset.land.sidebar,
        "aoiDigest": aoi_digest,
    }))
}

fn digest_for_bytes(bytes: &[u8]) -> String {
    use std::hash::{Hash, Hasher};
    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    bytes.hash(&mut hasher);
    format!("{:016x}", hasher.finish())
}

pub fn read_layer_geojson_bytes(
    preset_path: &Path,
    source_id: &str,
    layer_key: &str,
) -> Result<(Vec<u8>, String)> {
    let preset = load_preset(preset_path)?;
    let source = preset
        .land
        .sources
        .get(source_id)
        .with_context(|| format!("unknown land source: {source_id}"))?;
    let layer = layer_by_key(source, layer_key)
        .with_context(|| format!("unknown layer key: {layer_key}"))?;

    let project_dir = preset_path
        .parent()
        .context("preset path must have parent")?;
    let cache_root = resolved_land_cache_dir(preset_path);
    let manifest = read_manifest(&cache_root);
    let cached = layer_cache_path(&cache_root, source_id, layer_key);

    if cached.is_file() {
        let bytes = std::fs::read(&cached)?;
        let digest = manifest_digest(&manifest, source_id, layer_key)
            .unwrap_or_else(|| digest_for_bytes(&bytes));
        return Ok((bytes, digest));
    }

    let geo_path = resolve_land_layer_geojson_path(
        project_dir,
        source_id,
        &layer.name,
        &source.path,
    )?;
    let bytes = std::fs::read(&geo_path)?;
    let digest = manifest_digest(&manifest, source_id, layer_key)
        .unwrap_or_else(|| digest_for_bytes(&bytes));
    Ok((bytes, digest))
}
