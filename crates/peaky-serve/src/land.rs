//! Land source listing and GeoJSON layer serve (v4 cache compatible).

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use peaky_geo::{
    aoi_land_digest, apply_filters_to_aoi_base, effective_layer_digest,
    ensure_aoi_clipped_preview_geojson, ensure_preview_field_values, ensure_preview_fields,
    land_cache_dir, list_land_data_gdbs, list_preview_layers, overlay_land_digest,
    preview_layers_json, read_land_manifest,
    include_role_source_ids, read_layer_geojson_bytes as read_layer_geojson_bytes_inner,
    read_or_build_overlay_geojson, read_or_build_overlay_part_geojson, resolve_land_data_path,
    LandOverlayKind,
};
use peaky_preset::{
    delete_land_source, import_land_source, load_preset, patch_land_sidebar, patch_land_source,
    LandLayerEntry, LandLayerRole, LandLayerStyleValue, LandSidebar, LandSourceEntry,
};
use serde::Deserialize;
use serde_json::{json, Value};

pub fn resolved_land_cache_dir(preset_path: &Path) -> PathBuf {
    land_cache_dir(preset_path)
}

fn read_manifest(cache_root: &Path) -> HashMap<String, Value> {
    read_land_manifest(cache_root)
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
        .filter(|(_, entry)| entry.is_enabled())
        .map(|(id, entry)| serialize_land_source(id, entry, &manifest, &aoi_digest))
        .collect();
    sources.sort_by(|a, b| {
        a.get("id")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .cmp(b.get("id").and_then(|v| v.as_str()).unwrap_or(""))
    });
    let overlay_digest = overlay_land_digest(preset_path).unwrap_or_else(|_| "none".to_string());
    let overlay_parts = include_role_source_ids(preset_path).unwrap_or_default();
    Ok(json!({
        "sources": sources,
        "sidebar": preset.land.sidebar,
        "aoiDigest": aoi_digest,
        "overlayDigest": overlay_digest,
        "overlays": [
            { "id": "eligible", "label": "Eligible", "parts": overlay_parts },
        ],
    }))
}

pub fn read_overlay_geojson_bytes(
    preset_path: &Path,
    kind: &str,
) -> Result<(Vec<u8>, String)> {
    let kind = LandOverlayKind::parse(kind)?;
    read_or_build_overlay_geojson(preset_path, kind)
}

pub fn read_overlay_part_geojson_bytes(
    preset_path: &Path,
    kind: &str,
    source_id: &str,
) -> Result<(Vec<u8>, String)> {
    let kind = LandOverlayKind::parse(kind)?;
    read_or_build_overlay_part_geojson(preset_path, kind, source_id)
}

pub fn read_layer_geojson_bytes(
    preset_path: &Path,
    source_id: &str,
    layer_key: &str,
) -> Result<(Vec<u8>, String)> {
    read_layer_geojson_bytes_inner(preset_path, source_id, layer_key)
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
    let geojson = ensure_aoi_clipped_preview_geojson(preset_path, rel_path, layer)?;
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
    let base = ensure_aoi_clipped_preview_geojson(preset_path, &body.path, &body.layer)?;
    let layer = preview_layer_entry(&body);
    let geojson = apply_filters_to_aoi_base(base, &layer);
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
    let fields = ensure_preview_fields(preset_path, rel_path, layer)?;
    Ok(json!({ "fields": fields }))
}

pub fn land_preview_values_payload(
    preset_path: &Path,
    rel_path: &str,
    layer: &str,
    field: &str,
) -> Result<Value> {
    let (values, scoped_to_aoi) = ensure_preview_field_values(preset_path, rel_path, layer, field)?;
    Ok(json!({
        "values": values.values,
        "truncated": values.truncated,
        "scopedToAoi": scoped_to_aoi,
    }))
}
