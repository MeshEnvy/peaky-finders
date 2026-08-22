//! Import-editor preview caches: AOI-clipped GeoJSON, field lists, field value indexes.

use std::collections::{HashMap, HashSet};
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

use anyhow::{Context, Result};
use peaky_preset::{load_preset, slugify_files_segment, LandLayerEntry, LandSourceEntry};
use rayon::prelude::*;
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::eligible_land::{aoi_land_digest, load_preset_aoi_union};
use crate::land_boot::{land_boot_pool, land_boot_workers, LandBootProgress};
use crate::land_gdb::{
    field_values_from_geojson_value, layer_fields, layer_field_values, read_layer_geojson_cached,
    resolve_land_data_path, LandFieldValues, LandPreviewField,
};
use crate::land_pipeline::{land_cache_dir, layer_needs_geojson_transform, process_land_geojson_value};
use crate::land_validate_cache::{land_source_layers_key, source_fingerprint};

pub fn preview_aoi_geojson_path(
    cache_root: &Path,
    aoi_digest: &str,
    rel_path: &str,
    layer: &str,
) -> PathBuf {
    cache_root
        .join("_preview")
        .join(aoi_digest)
        .join(slugify_files_segment(rel_path))
        .join(format!("{}.geojson", slugify_files_segment(layer)))
}

pub fn preview_fields_cache_path(
    cache_root: &Path,
    source_fingerprint: (u64, u64),
    rel_path: &str,
    layer: &str,
) -> PathBuf {
    let key = format!("{:016x}{:016x}", source_fingerprint.0, source_fingerprint.1);
    cache_root
        .join("_preview")
        .join("_fields")
        .join(key)
        .join(slugify_files_segment(rel_path))
        .join(format!("{}.fields.json", slugify_files_segment(layer)))
}

pub fn preview_field_values_cache_path(
    cache_root: &Path,
    aoi_digest: &str,
    rel_path: &str,
    layer: &str,
    field: &str,
) -> PathBuf {
    cache_root
        .join("_preview")
        .join(aoi_digest)
        .join(slugify_files_segment(rel_path))
        .join(slugify_files_segment(layer))
        .join(format!("{}.values.json", slugify_files_segment(field)))
}

pub fn empty_preview_layer(name: &str) -> LandLayerEntry {
    LandLayerEntry {
        name: name.to_string(),
        id: None,
        role: None,
        include: Vec::new(),
        exclude: Vec::new(),
        label_field: None,
        style_field: None,
        style: None,
    }
}

/// AOI-clipped GeoJSON for import preview and as the shared base for pipeline filter passes.
pub fn ensure_aoi_clipped_preview_geojson(
    preset_path: &Path,
    rel_path: &str,
    layer: &str,
) -> Result<Value> {
    let aoi = load_preset_aoi_union(preset_path)?;
    let project_dir = preset_path
        .parent()
        .context("preset path must have parent")?;
    let geojson = read_layer_geojson_cached(project_dir, rel_path, layer)?;
    if aoi.is_none() {
        return Ok(geojson);
    }
    let cache_root = land_cache_dir(preset_path);
    let aoi_digest = aoi_land_digest(preset_path).unwrap_or_else(|_| "none".to_string());
    let cache_path = preview_aoi_geojson_path(&cache_root, &aoi_digest, rel_path, layer);
    if cache_path.is_file() {
        let text = fs::read_to_string(&cache_path).context("read cached preview GeoJSON")?;
        return serde_json::from_str(&text).context("parse cached preview GeoJSON");
    }
    let clipped = process_land_geojson_value(geojson, &empty_preview_layer(layer), aoi.as_ref());
    if let Some(parent) = cache_path.parent() {
        fs::create_dir_all(parent)?;
    }
    let tmp = cache_path.with_extension("geojson.tmp");
    fs::write(&tmp, serde_json::to_vec(&clipped)?)?;
    fs::rename(&tmp, &cache_path).with_context(|| format!("rename {}", cache_path.display()))?;
    Ok(clipped)
}

pub fn ensure_preview_fields(
    preset_path: &Path,
    rel_path: &str,
    layer: &str,
) -> Result<Vec<LandPreviewField>> {
    let project_dir = preset_path
        .parent()
        .context("preset path must have parent")?;
    let abs = resolve_land_data_path(project_dir, rel_path)?;
    let is_gdb = rel_path.replace('\\', "/").to_ascii_lowercase().ends_with(".gdb");
    let fingerprint = source_fingerprint(&abs, is_gdb)?;
    let cache_root = land_cache_dir(preset_path);
    let cache_path = preview_fields_cache_path(&cache_root, fingerprint, rel_path, layer);
    if cache_path.is_file() {
        let text = fs::read_to_string(&cache_path).context("read cached preview fields")?;
        return serde_json::from_str(&text).context("parse cached preview fields");
    }
    let fields = layer_fields(&abs, layer)?;
    if let Some(parent) = cache_path.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::write(&cache_path, serde_json::to_vec(&fields)?)?;
    Ok(fields)
}

pub fn ensure_preview_field_values(
    preset_path: &Path,
    rel_path: &str,
    layer: &str,
    field: &str,
) -> Result<(LandFieldValues, bool)> {
    let aoi = load_preset_aoi_union(preset_path)?;
    if aoi.is_none() {
        let project_dir = preset_path
            .parent()
            .context("preset path must have parent")?;
        let abs = resolve_land_data_path(project_dir, rel_path)?;
        return Ok((layer_field_values(&abs, layer, field)?, false));
    }
    let cache_root = land_cache_dir(preset_path);
    let aoi_digest = aoi_land_digest(preset_path).unwrap_or_else(|_| "none".to_string());
    let cache_path = preview_field_values_cache_path(&cache_root, &aoi_digest, rel_path, layer, field);
    if cache_path.is_file() {
        let text = fs::read_to_string(&cache_path).context("read cached field values")?;
        let cached: LandFieldValues =
            serde_json::from_str(&text).context("parse cached field values")?;
        return Ok((cached, true));
    }
    let geojson = ensure_aoi_clipped_preview_geojson(preset_path, rel_path, layer)?;
    let computed = field_values_from_geojson_value(&geojson, field)?;
    if let Some(parent) = cache_path.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::write(&cache_path, serde_json::to_vec(&computed)?)?;
    Ok((computed, true))
}

/// Apply attribute filters to an AOI-clipped base (no second clip pass).
pub fn apply_filters_to_aoi_base(base: Value, layer: &LandLayerEntry) -> Value {
    if !layer_needs_geojson_transform(layer) {
        return base;
    }
    crate::land_filter::transform_geojson_for_layer(
        base,
        &layer.include,
        &layer.exclude,
        layer.label_field.as_deref(),
        layer.style_field.as_deref(),
    )
}

fn collect_filter_fields_from_source(entry: &LandSourceEntry) -> HashSet<(String, String)> {
    let mut out = HashSet::new();
    for layer in &entry.layers {
        for filt in layer.include.iter().chain(layer.exclude.iter()) {
            if !filt.field.is_empty() {
                out.insert((layer.name.clone(), filt.field.clone()));
            }
        }
        if let Some(label) = &layer.label_field {
            if !label.is_empty() {
                out.insert((layer.name.clone(), label.clone()));
            }
        }
        if let Some(style) = &layer.style_field {
            if !style.is_empty() {
                out.insert((layer.name.clone(), style.clone()));
            }
        }
    }
    out
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
struct PreviewWarmEntry {
    source_size: u64,
    source_mtime_secs: u64,
    layers_key: String,
    artifact: String,
}

#[derive(Debug, Default, Serialize, Deserialize)]
struct PreviewWarmFile {
    #[serde(default)]
    aoi_digest: String,
    #[serde(default)]
    entries: HashMap<String, PreviewWarmEntry>,
}

pub struct LandPreviewWarmCache {
    path: PathBuf,
    aoi_digest: String,
    entries: Mutex<HashMap<String, PreviewWarmEntry>>,
    dirty: std::sync::atomic::AtomicBool,
}

impl LandPreviewWarmCache {
    pub fn open(project_dir: &Path, aoi_digest: &str) -> Result<Self> {
        let cache_root = project_dir.join(".peaky/cache/land");
        fs::create_dir_all(&cache_root)?;
        let path = cache_root.join("preview-warm.json");
        let (file_aoi, entries) = if path.is_file() {
            fs::read_to_string(&path)
                .ok()
                .and_then(|text| serde_json::from_str::<PreviewWarmFile>(&text).ok())
                .map(|file| (file.aoi_digest, file.entries))
                .unwrap_or_default()
        } else {
            (String::new(), HashMap::new())
        };
        let mut entries = entries;
        if file_aoi != aoi_digest {
            entries.clear();
        }
        Ok(Self {
            path,
            aoi_digest: aoi_digest.to_string(),
            entries: Mutex::new(entries),
            dirty: std::sync::atomic::AtomicBool::new(file_aoi != aoi_digest),
        })
    }

    pub fn persist(&self) -> Result<()> {
        use std::sync::atomic::Ordering;
        if !self.dirty.load(Ordering::Relaxed) {
            return Ok(());
        }
        let entries = self.entries.lock().expect("preview warm cache").clone();
        let mut keys: Vec<_> = entries.keys().cloned().collect();
        keys.sort();
        let ordered: HashMap<String, PreviewWarmEntry> = keys
            .into_iter()
            .filter_map(|k| entries.get(&k).map(|v| (k, v.clone())))
            .collect();
        let payload = PreviewWarmFile {
            aoi_digest: self.aoi_digest.clone(),
            entries: ordered,
        };
        fs::write(
            &self.path,
            serde_json::to_string_pretty(&payload)? + "\n",
        )
        .with_context(|| format!("write {}", self.path.display()))?;
        self.dirty.store(false, Ordering::Relaxed);
        Ok(())
    }

    fn is_warm(
        &self,
        key: &str,
        fingerprint: (u64, u64),
        layers_key: &str,
        artifact: &str,
        path: &Path,
    ) -> bool {
        if !path.is_file() {
            return false;
        }
        let cache = self.entries.lock().expect("preview warm cache");
        cache.get(key).is_some_and(|row| {
            row.source_size == fingerprint.0
                && row.source_mtime_secs == fingerprint.1
                && row.layers_key == layers_key
                && row.artifact == artifact
        })
    }

    fn remember(&self, key: &str, fingerprint: (u64, u64), layers_key: &str, artifact: &str) {
        use std::sync::atomic::Ordering;
        let row = PreviewWarmEntry {
            source_size: fingerprint.0,
            source_mtime_secs: fingerprint.1,
            layers_key: layers_key.to_string(),
            artifact: artifact.to_string(),
        };
        let mut cache = self.entries.lock().expect("preview warm cache");
        if cache.get(key) == Some(&row) {
            return;
        }
        cache.insert(key.to_string(), row);
        self.dirty.store(true, Ordering::Relaxed);
    }
}

#[derive(Debug, Clone, Default, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LandPreviewWarmStats {
    pub aoi_bases: usize,
    pub fields: usize,
    pub field_values: usize,
    pub skipped: usize,
    pub failed: usize,
}

struct PreviewSourceJob {
    source_id: String,
    rel_path: String,
    layer_name: String,
    filter_fields: Vec<String>,
}

fn source_is_gdb(path: &str) -> bool {
    path.replace('\\', "/")
        .to_ascii_lowercase()
        .ends_with(".gdb")
}

/// Pre-build AOI-clipped preview GeoJSON, field lists, and filter field value indexes.
pub fn warm_land_preview_caches_for_preset(
    preset_path: &Path,
    preview_warm_cache: &LandPreviewWarmCache,
) -> Result<LandPreviewWarmStats> {
    let preset = load_preset(preset_path)?;
    let project_dir = preset_path
        .parent()
        .context("preset path must have parent")?;
    let cache_root = land_cache_dir(preset_path);
    let aoi_digest = aoi_land_digest(preset_path).unwrap_or_else(|_| "none".to_string());
    let has_aoi = aoi_digest != "none";

    let mut seen_layers: HashSet<(String, String)> = HashSet::new();
    let mut jobs = Vec::new();
    for (source_id, entry) in preset.land.sources.iter().filter(|(_, e)| e.is_enabled()) {
        let rel = entry.path.replace('\\', "/");
        let filter_map = collect_filter_fields_from_source(entry);
        let mut layer_fields_map: HashMap<String, Vec<String>> = HashMap::new();
        for (layer_name, field) in filter_map {
            layer_fields_map
                .entry(layer_name)
                .or_default()
                .push(field);
        }
        for layer in &entry.layers {
            if !seen_layers.insert((rel.clone(), layer.name.clone())) {
                continue;
            }
            let mut fields: Vec<String> = layer_fields_map
                .get(&layer.name)
                .cloned()
                .unwrap_or_default();
            fields.sort();
            fields.dedup();
            jobs.push(PreviewSourceJob {
                source_id: source_id.clone(),
                rel_path: rel.clone(),
                layer_name: layer.name.clone(),
                filter_fields: fields,
            });
        }
    }

    if jobs.is_empty() {
        tracing::info!("land preview warm: no enabled sources");
        return Ok(LandPreviewWarmStats::default());
    }

    let workers = land_boot_workers().min(2);
    tracing::info!(count = jobs.len(), workers, has_aoi, "land preview warm: starting");

    let stats: Mutex<LandPreviewWarmStats> = Mutex::new(LandPreviewWarmStats::default());
    let preset_path = preset_path.to_path_buf();
    let progress = LandBootProgress::new("land preview warm", jobs.len());

    land_boot_pool().install(|| {
        jobs.par_iter().for_each(|job| {
            let unit = format!("{}/{}", job.source_id, job.layer_name);
            progress.begin_unit(&job.source_id, &unit, "preview layer");
            let result = warm_one_preview_source(
                &preset_path,
                project_dir,
                &cache_root,
                &aoi_digest,
                has_aoi,
                preview_warm_cache,
                job,
            );
            let mut stats = stats.lock().expect("preview warm stats");
            match result {
                Ok(local) => {
                    stats.aoi_bases += local.aoi_bases;
                    stats.fields += local.fields;
                    stats.field_values += local.field_values;
                    stats.skipped += local.skipped;
                    progress.finish_unit(&job.source_id, &unit, &preview_outcome(&local));
                }
                Err(e) => {
                    stats.failed += 1;
                    progress.finish_unit(&job.source_id, &unit, "failed");
                    tracing::warn!(
                        source_id = %job.source_id,
                        path = %job.rel_path,
                        layer = %job.layer_name,
                        error = %e,
                        "land preview warm: failed"
                    );
                }
            }
        });
    });

    preview_warm_cache.persist()?;
    let stats = stats.into_inner().expect("preview warm stats");
    tracing::info!(
        aoi_bases = stats.aoi_bases,
        fields = stats.fields,
        field_values = stats.field_values,
        skipped = stats.skipped,
        failed = stats.failed,
        "land preview warm: complete"
    );
    Ok(stats)
}

#[derive(Default)]
struct LocalPreviewWarmCounts {
    aoi_bases: usize,
    fields: usize,
    field_values: usize,
    skipped: usize,
}

fn preview_outcome(counts: &LocalPreviewWarmCounts) -> String {
    let built = counts.aoi_bases + counts.fields + counts.field_values;
    if built == 0 {
        "skipped".to_string()
    } else {
        format!(
            "built aoi={} fields={} values={}",
            counts.aoi_bases, counts.fields, counts.field_values
        )
    }
}

fn warm_one_preview_source(
    preset_path: &Path,
    project_dir: &Path,
    cache_root: &Path,
    aoi_digest: &str,
    has_aoi: bool,
    warm_cache: &LandPreviewWarmCache,
    job: &PreviewSourceJob,
) -> Result<LocalPreviewWarmCounts> {
    let preset = load_preset(preset_path)?;
    let entry = preset
        .land
        .sources
        .get(&job.source_id)
        .with_context(|| format!("unknown land source: {}", job.source_id))?;
    let abs = project_dir.join(&job.rel_path);
    let is_gdb = source_is_gdb(&job.rel_path);
    let fingerprint = source_fingerprint(&abs, is_gdb)?;
    let layers_key = land_source_layers_key(entry);
    let mut counts = LocalPreviewWarmCounts::default();

    // Field name list (source fingerprint only)
    let fields_path = preview_fields_cache_path(cache_root, fingerprint, &job.rel_path, &job.layer_name);
    let fields_key = format!("{}/fields/{}", job.source_id, job.layer_name);
    if warm_cache.is_warm(&fields_key, fingerprint, &layers_key, "fields", &fields_path) {
        counts.skipped += 1;
    } else if fields_path.is_file() {
        warm_cache.remember(&fields_key, fingerprint, &layers_key, "fields");
        counts.skipped += 1;
    } else {
        tracing::info!(
            source_id = %job.source_id,
            layer = %job.layer_name,
            path = %job.rel_path,
            "land preview warm: fields"
        );
        ensure_preview_fields(preset_path, &job.rel_path, &job.layer_name)?;
        warm_cache.remember(&fields_key, fingerprint, &layers_key, "fields");
        counts.fields += 1;
    }

    if has_aoi {
        let aoi_path =
            preview_aoi_geojson_path(cache_root, aoi_digest, &job.rel_path, &job.layer_name);
        let aoi_key = format!("{}/aoi/{}", job.source_id, job.layer_name);
        if warm_cache.is_warm(&aoi_key, fingerprint, &layers_key, "aoi-base", &aoi_path) {
            counts.skipped += 1;
        } else if aoi_path.is_file() {
            warm_cache.remember(&aoi_key, fingerprint, &layers_key, "aoi-base");
            counts.skipped += 1;
        } else {
            tracing::info!(
                source_id = %job.source_id,
                layer = %job.layer_name,
                path = %job.rel_path,
                "land preview warm: aoi clip"
            );
            ensure_aoi_clipped_preview_geojson(preset_path, &job.rel_path, &job.layer_name)?;
            tracing::info!(
                source_id = %job.source_id,
                layer = %job.layer_name,
                path = %job.rel_path,
                "land preview warm: aoi clip done"
            );
            warm_cache.remember(&aoi_key, fingerprint, &layers_key, "aoi-base");
            counts.aoi_bases += 1;
        }

        for field in &job.filter_fields {
            let values_path = preview_field_values_cache_path(
                cache_root,
                aoi_digest,
                &job.rel_path,
                &job.layer_name,
                field,
            );
            let values_key = format!("{}/values/{}/{}", job.source_id, job.layer_name, field);
            let artifact = format!("values:{field}");
            if warm_cache.is_warm(&values_key, fingerprint, &layers_key, &artifact, &values_path) {
                counts.skipped += 1;
                continue;
            }
            if values_path.is_file() {
                warm_cache.remember(&values_key, fingerprint, &layers_key, &artifact);
                counts.skipped += 1;
                continue;
            }
            tracing::info!(
                source_id = %job.source_id,
                layer = %job.layer_name,
                field = %field,
                "land preview warm: field values"
            );
            ensure_preview_field_values(preset_path, &job.rel_path, &job.layer_name, field)?;
            warm_cache.remember(&values_key, fingerprint, &layers_key, &artifact);
            counts.field_values += 1;
        }
    }

    Ok(counts)
}
