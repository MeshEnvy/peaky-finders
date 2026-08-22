//! Land source listing and GeoJSON layer serve (v4 cache compatible).

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use peaky_geo::{
    apply_aoi_clip_to_geojson, aoi_land_digest, layer_field_values, layer_fields,
    layer_skips_aoi_clip, list_land_data_gdbs, list_preview_layers, load_preset_aoi_union,
    preview_layers_json, read_layer_geojson_value, resolve_land_data_path,
    resolve_land_layer_geojson_path, transform_geojson_for_layer,
};
use peaky_preset::{
    delete_land_source, import_land_source, load_preset, patch_land_sidebar, patch_land_source,
    slugify_files_segment, LandLayerEntry, LandLayerRole, LandLayerStyleValue, LandSidebar,
    LandSourceEntry,
};
use serde::Deserialize;
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
    aoi_digest: &str,
) -> Value {
    let layers: Vec<Value> = entry
        .layers
        .iter()
        .map(|layer| {
            let digest = effective_layer_digest(manifest, source_id, layer, None, aoi_digest);
            serialize_land_layer(layer, Some(&digest))
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
    let aoi_digest = peaky_geo::aoi_land_digest(preset_path).unwrap_or_else(|_| "none".to_string());
    let mut sources: Vec<Value> = preset
        .land
        .sources
        .iter()
        .map(|(id, entry)| serialize_land_source(id, entry, &manifest, &aoi_digest))
        .collect();
    sources.sort_by(|a, b| {
        a.get("id")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .cmp(b.get("id").and_then(|v| v.as_str()).unwrap_or(""))
    });
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

fn filter_digest_suffix(layer: &LandLayerEntry) -> String {
    use std::hash::{Hash, Hasher};
    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    for filt in &layer.include {
        filt.field.hash(&mut hasher);
        for v in &filt.values {
            v.hash(&mut hasher);
        }
    }
    for filt in &layer.exclude {
        filt.field.hash(&mut hasher);
        for v in &filt.values {
            v.hash(&mut hasher);
        }
    }
    if let Some(label) = &layer.label_field {
        label.hash(&mut hasher);
    }
    if let Some(style) = &layer.style_field {
        style.hash(&mut hasher);
    }
    format!("{:08x}", hasher.finish())
}

fn layer_needs_geojson_transform(layer: &LandLayerEntry) -> bool {
    !layer.include.is_empty()
        || !layer.exclude.is_empty()
        || layer
            .label_field
            .as_deref()
            .is_some_and(|field| !field.is_empty())
        || layer
            .style_field
            .as_deref()
            .is_some_and(|field| !field.is_empty())
}

fn layer_needs_aoi_clip(layer: &LandLayerEntry, aoi_digest: &str) -> bool {
    !layer_skips_aoi_clip(layer) && aoi_digest != "none"
}

fn layer_needs_geojson_pipeline(layer: &LandLayerEntry, aoi_digest: &str) -> bool {
    layer_needs_geojson_transform(layer) || layer_needs_aoi_clip(layer, aoi_digest)
}

fn pipeline_digest_suffix(layer: &LandLayerEntry, aoi_digest: &str) -> String {
    let mut parts = Vec::new();
    if layer_needs_aoi_clip(layer, aoi_digest) {
        parts.push(format!("aoi:{aoi_digest}"));
    }
    if layer_needs_geojson_transform(layer) {
        parts.push(format!("filt:{}", filter_digest_suffix(layer)));
    }
    parts.join("|")
}

fn effective_layer_digest(
    manifest: &HashMap<String, Value>,
    source_id: &str,
    layer: &LandLayerEntry,
    filtered_bytes: Option<&[u8]>,
    aoi_digest: &str,
) -> String {
    if let Some(base) = manifest_digest(manifest, source_id, &layer.layer_key()) {
        if !layer_needs_geojson_pipeline(layer, aoi_digest) {
            return base;
        }
        let suffix = pipeline_digest_suffix(layer, aoi_digest);
        if suffix.is_empty() {
            return base;
        }
        return format!("{base}:{suffix}");
    }
    filtered_bytes
        .map(digest_for_bytes)
        .unwrap_or_else(|| pipeline_digest_suffix(layer, aoi_digest))
}

fn apply_land_layer_geojson_pipeline(
    raw_bytes: &[u8],
    preset_path: &Path,
    layer: &LandLayerEntry,
) -> Result<Vec<u8>> {
    let aoi = load_preset_aoi_union(preset_path)?;
    if aoi.is_none() && !layer_needs_geojson_transform(layer) {
        return Ok(raw_bytes.to_vec());
    }

    let geojson: Value = serde_json::from_slice(raw_bytes).context("parse layer GeoJSON")?;
    let geojson = process_land_geojson_value(geojson, layer, aoi.as_ref());
    Ok(serde_json::to_vec(&geojson)?)
}

fn process_land_geojson_value(
    geojson: Value,
    layer: &LandLayerEntry,
    aoi: Option<&geo::Geometry<f64>>,
) -> Value {
    let geojson = apply_aoi_clip_to_geojson(geojson, layer, aoi);
    if !layer_needs_geojson_transform(layer) {
        return geojson;
    }
    transform_geojson_for_layer(
        geojson,
        &layer.include,
        &layer.exclude,
        layer.label_field.as_deref(),
        layer.style_field.as_deref(),
    )
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
    let aoi_digest = aoi_land_digest(preset_path).unwrap_or_else(|_| "none".to_string());
    let cached = layer_cache_path(&cache_root, source_id, layer_key);

    let raw_bytes = if cached.is_file() {
        std::fs::read(&cached)?
    } else {
        let geo_path = resolve_land_layer_geojson_path(
            project_dir,
            source_id,
            &layer.name,
            &source.path,
        )?;
        std::fs::read(&geo_path)?
    };

    let bytes = apply_land_layer_geojson_pipeline(&raw_bytes, preset_path, layer)?;
    let digest = effective_layer_digest(&manifest, source_id, layer, Some(&bytes), &aoi_digest);
    Ok((bytes, digest))
}

fn project_dir_for(preset_path: &Path) -> Result<&Path> {
    preset_path
        .parent()
        .context("preset path must have parent directory")
}

#[derive(Deserialize)]
pub struct LandImportBody {
    pub path: String,
    pub layers: Vec<LandLayerEntry>,
    #[serde(default)]
    pub label: Option<String>,
}

#[derive(Deserialize)]
pub struct LandPatchSourceBody {
    #[serde(default)]
    pub layers: Option<Vec<LandLayerEntry>>,
    #[serde(default)]
    pub label: Option<String>,
}

#[derive(Deserialize)]
pub struct LandPreviewGeoJsonBody {
    pub path: String,
    pub layer: String,
    #[serde(default)]
    pub include: Vec<peaky_preset::LandAttributeFilter>,
    #[serde(default)]
    pub exclude: Vec<peaky_preset::LandAttributeFilter>,
    #[serde(default, rename = "labelField")]
    pub label_field: Option<String>,
    #[serde(default, rename = "styleField")]
    pub style_field: Option<String>,
    #[serde(default)]
    pub role: Option<LandLayerRole>,
}

fn preview_layer_entry(body: &LandPreviewGeoJsonBody) -> LandLayerEntry {
    LandLayerEntry {
        name: body.layer.clone(),
        id: None,
        role: body.role,
        include: body.include.clone(),
        exclude: body.exclude.clone(),
        label_field: body.label_field.clone(),
        style_field: body.style_field.clone(),
        style: None,
    }
}

pub fn land_data_gdbs_payload(preset_path: &Path) -> Result<Value> {
    let project_dir = project_dir_for(preset_path)?;
    let paths = list_land_data_gdbs(project_dir)?;
    Ok(json!({ "paths": paths }))
}

pub fn land_import_preview_payload(preset_path: &Path, rel_path: &str) -> Result<Value> {
    let project_dir = project_dir_for(preset_path)?;
    let abs = resolve_land_data_path(project_dir, rel_path)?;
    let layers = list_preview_layers(&abs)?;
    if layers.is_empty() {
        anyhow::bail!("no layers found in data source");
    }
    Ok(preview_layers_json(&layers))
}

pub fn land_import_source(
    preset_path: &Path,
    body: LandImportBody,
) -> Result<(String, Value)> {
    if body.layers.is_empty() {
        anyhow::bail!("select at least one layer");
    }
    let project_dir = project_dir_for(preset_path)?;
    resolve_land_data_path(project_dir, &body.path)?;
    let (source_id, entry) = import_land_source(
        preset_path,
        body.path,
        body.label,
        body.layers,
    )?;
    let cache_root = resolved_land_cache_dir(preset_path);
    let manifest = read_manifest(&cache_root);
    let aoi_digest = aoi_land_digest(preset_path).unwrap_or_else(|_| "none".to_string());
    Ok((
        source_id.clone(),
        serialize_land_source(&source_id, &entry, &manifest, &aoi_digest),
    ))
}

pub fn land_patch_source(
    preset_path: &Path,
    source_id: &str,
    body: LandPatchSourceBody,
) -> Result<Value> {
    if body.layers.is_none() && body.label.is_none() {
        anyhow::bail!("layers or label required");
    }
    let preset = load_preset(preset_path)?;
    let layers = match body.layers {
        Some(layers) => {
            if layers.is_empty() {
                anyhow::bail!("select at least one layer");
            }
            layers
        }
        None => preset
            .land
            .sources
            .get(source_id)
            .map(|source| source.layers.clone())
            .with_context(|| format!("unknown land source: {source_id}"))?,
    };
    let entry = patch_land_source(preset_path, source_id, body.label, layers)?;
    let cache_root = resolved_land_cache_dir(preset_path);
    let manifest = read_manifest(&cache_root);
    let aoi_digest = aoi_land_digest(preset_path).unwrap_or_else(|_| "none".to_string());
    Ok(serialize_land_source(source_id, &entry, &manifest, &aoi_digest))
}

pub fn land_delete_source(preset_path: &Path, source_id: &str) -> Result<()> {
    delete_land_source(preset_path, source_id)
}

pub fn land_patch_sidebar(preset_path: &Path, sidebar: LandSidebar) -> Result<Value> {
    let updated = patch_land_sidebar(preset_path, sidebar)?;
    Ok(json!({ "sidebar": updated }))
}

pub fn land_preview_geojson_payload(
    preset_path: &Path,
    rel_path: &str,
    layer: &str,
) -> Result<Value> {
    let project_dir = project_dir_for(preset_path)?;
    let abs = resolve_land_data_path(project_dir, rel_path)?;
    let geojson = read_layer_geojson_value(&abs, layer)?;
    let aoi = load_preset_aoi_union(preset_path)?;
    let preview_layer = LandLayerEntry {
        name: layer.to_string(),
        id: None,
        role: None,
        include: Vec::new(),
        exclude: Vec::new(),
        label_field: None,
        style_field: None,
        style: None,
    };
    let geojson = process_land_geojson_value(geojson, &preview_layer, aoi.as_ref());
    Ok(json!({
        "path": rel_path,
        "layer": layer,
        "geojson": geojson,
    }))
}

pub fn land_preview_geojson_filtered_payload(
    preset_path: &Path,
    body: LandPreviewGeoJsonBody,
) -> Result<Value> {
    let project_dir = project_dir_for(preset_path)?;
    let abs = resolve_land_data_path(project_dir, &body.path)?;
    let geojson = read_layer_geojson_value(&abs, &body.layer)?;
    let aoi = load_preset_aoi_union(preset_path)?;
    let layer = preview_layer_entry(&body);
    let geojson = process_land_geojson_value(geojson, &layer, aoi.as_ref());
    Ok(json!({
        "path": body.path,
        "layer": body.layer,
        "geojson": geojson,
    }))
}

pub fn land_preview_fields_payload(
    preset_path: &Path,
    rel_path: &str,
    layer: &str,
) -> Result<Value> {
    let project_dir = project_dir_for(preset_path)?;
    let abs = resolve_land_data_path(project_dir, rel_path)?;
    let fields = layer_fields(&abs, layer)?;
    Ok(json!({ "fields": fields }))
}

pub fn land_preview_values_payload(
    preset_path: &Path,
    rel_path: &str,
    layer: &str,
    field: &str,
) -> Result<Value> {
    let project_dir = project_dir_for(preset_path)?;
    let abs = resolve_land_data_path(project_dir, rel_path)?;
    let values = layer_field_values(&abs, layer, field)?;
    Ok(json!({
        "values": values.values,
        "truncated": values.truncated,
    }))
}
