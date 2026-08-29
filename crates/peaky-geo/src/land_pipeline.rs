//! AOI-clipped and attribute-filtered land layer GeoJSON pipeline + disk cache.

use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

use anyhow::{Context, Result};
use peaky_preset::{
    load_preset, slugify_files_segment, LandLayerEntry, LandSourceEntry,
};
use rayon::prelude::*;
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::eligible_land::{aoi_land_digest, load_preset_aoi_union};
use crate::land_aoi_clip::apply_aoi_clip_to_geojson;
use crate::land_boot::{land_boot_pool, land_boot_workers, LandBootProgress};
use crate::land_filter::transform_geojson_for_layer;
use crate::land_path::resolve_land_layer_geojson_path;
use crate::land_preview::{apply_filters_to_aoi_base, ensure_aoi_clipped_preview_geojson};
use crate::land_validate_cache::{land_source_layers_key, source_fingerprint};

pub fn land_cache_dir(preset_path: &Path) -> PathBuf {
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

fn pipeline_layer_stem(layer_key: &str) -> String {
    let safe = slugify_files_segment(layer_key);
    if safe.is_empty() {
        "layer".to_string()
    } else {
        safe
    }
}

/// Locate a boot-warmed pipeline GeoJSON without reading the raw statewide source.
pub fn find_warmed_pipeline_geojson(
    cache_root: &Path,
    source_id: &str,
    layer_key: &str,
) -> Option<PathBuf> {
    let dir = cache_root.join("pipeline").join(source_id);
    let prefix = format!("{}.", pipeline_layer_stem(layer_key));
    let mut hits: Vec<PathBuf> = Vec::new();
    for entry in fs::read_dir(&dir).ok()?.flatten() {
        let path = entry.path();
        let name = path.file_name()?.to_string_lossy();
        if name.starts_with(&prefix) && name.ends_with(".geojson") {
            hits.push(path);
        }
    }
    if hits.is_empty() {
        return None;
    }
    if hits.len() == 1 {
        return Some(hits.remove(0));
    }
    hits.into_iter().max_by_key(|path| {
        path.metadata()
            .and_then(|meta| meta.modified())
            .ok()
            .unwrap_or(std::time::SystemTime::UNIX_EPOCH)
    })
}

fn pipeline_cache_key(raw_digest: &str, pipeline_suffix: &str) -> String {
    use std::hash::{Hash, Hasher};
    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    raw_digest.hash(&mut hasher);
    pipeline_suffix.hash(&mut hasher);
    format!("{:016x}", hasher.finish())
}

pub fn pipeline_cache_path_for_layer(
    cache_root: &Path,
    source_id: &str,
    layer_key: &str,
    raw_digest: &str,
    pipeline_suffix: &str,
) -> PathBuf {
    let safe = slugify_files_segment(layer_key);
    let stem = if safe.is_empty() { "layer" } else { safe.as_str() };
    let key = pipeline_cache_key(raw_digest, pipeline_suffix);
    cache_root
        .join("pipeline")
        .join(source_id)
        .join(format!("{stem}.{key}.geojson"))
}

fn read_pipeline_cache(path: &Path) -> Option<Vec<u8>> {
    if !path.is_file() {
        return None;
    }
    fs::read(path).ok()
}

fn write_pipeline_cache(path: &Path, bytes: &[u8]) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let tmp = path.with_extension("geojson.tmp");
    fs::write(&tmp, bytes)?;
    fs::rename(&tmp, path).with_context(|| format!("rename {}", path.display()))?;
    Ok(())
}

pub fn read_land_manifest(cache_root: &Path) -> HashMap<String, Value> {
    let path = cache_root.join("manifest.json");
    if !path.is_file() {
        return HashMap::new();
    }
    fs::read_to_string(&path)
        .ok()
        .and_then(|text| serde_json::from_str(&text).ok())
        .unwrap_or_default()
}

pub fn manifest_layer_digest(
    manifest: &HashMap<String, Value>,
    source_id: &str,
    layer_key: &str,
) -> Option<String> {
    let safe = slugify_files_segment(layer_key);
    let stem = if safe.is_empty() { "layer" } else { safe.as_str() };
    let key = format!("{source_id}/{stem}");
    manifest
        .get(&key)
        .and_then(|v| v.get("digest"))
        .and_then(|d| d.as_str())
        .map(str::to_string)
}

pub fn digest_for_bytes(bytes: &[u8]) -> String {
    use std::hash::{Hash, Hasher};
    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    bytes.hash(&mut hasher);
    format!("{:016x}", hasher.finish())
}

pub fn filter_digest_suffix(layer: &LandLayerEntry) -> String {
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

pub fn layer_needs_geojson_transform(layer: &LandLayerEntry) -> bool {
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

pub fn layer_needs_aoi_clip(layer: &LandLayerEntry, aoi_digest: &str) -> bool {
    !crate::land_aoi_clip::layer_skips_aoi_clip(layer) && aoi_digest != "none"
}

pub fn layer_needs_geojson_pipeline(layer: &LandLayerEntry, aoi_digest: &str) -> bool {
    layer_needs_geojson_transform(layer) || layer_needs_aoi_clip(layer, aoi_digest)
}

pub fn pipeline_digest_suffix(layer: &LandLayerEntry, aoi_digest: &str) -> String {
    let mut parts = Vec::new();
    if layer_needs_aoi_clip(layer, aoi_digest) {
        parts.push(format!("aoi:{aoi_digest}"));
    }
    if layer_needs_geojson_transform(layer) {
        parts.push(format!("filt:{}", filter_digest_suffix(layer)));
    }
    parts.join("|")
}

pub fn effective_layer_digest(
    manifest: &HashMap<String, Value>,
    source_id: &str,
    layer: &LandLayerEntry,
    filtered_bytes: Option<&[u8]>,
    aoi_digest: &str,
) -> String {
    if let Some(base) = manifest_layer_digest(manifest, source_id, &layer.layer_key()) {
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
    source: &LandSourceEntry,
    layer: &LandLayerEntry,
) -> Result<Vec<u8>> {
    let aoi_digest = aoi_land_digest(preset_path).unwrap_or_else(|_| "none".to_string());
    if layer_needs_aoi_clip(layer, &aoi_digest) {
        let base = ensure_aoi_clipped_preview_geojson(preset_path, &source.path, &layer.name)?;
        let filtered = apply_filters_to_aoi_base(base, layer);
        return Ok(serde_json::to_vec(&filtered)?);
    }

    let aoi = load_preset_aoi_union(preset_path)?;
    if aoi.is_none() && !layer_needs_geojson_transform(layer) {
        return Ok(raw_bytes.to_vec());
    }

    let geojson: Value = serde_json::from_slice(raw_bytes).context("parse layer GeoJSON")?;
    let geojson = process_land_geojson_value(geojson, layer, aoi.as_ref());
    Ok(serde_json::to_vec(&geojson)?)
}

pub fn process_land_geojson_value(
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

fn layer_by_key<'a>(source: &'a LandSourceEntry, layer_key: &str) -> Option<&'a LandLayerEntry> {
    source
        .layers
        .iter()
        .find(|layer| layer.layer_key() == layer_key)
}

fn read_raw_layer_bytes(
    project_dir: &Path,
    cache_root: &Path,
    source_id: &str,
    source: &LandSourceEntry,
    layer: &LandLayerEntry,
    layer_key: &str,
) -> Result<Vec<u8>> {
    let cached = layer_cache_path(cache_root, source_id, layer_key);
    if cached.is_file() {
        return Ok(fs::read(&cached)?);
    }
    let geo_path = resolve_land_layer_geojson_path(
        project_dir,
        source_id,
        &layer.name,
        &source.path,
    )?;
    Ok(fs::read(&geo_path)?)
}

/// Ensure pipeline cache exists; returns bytes and API digest.
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
    let cache_root = land_cache_dir(preset_path);
    let manifest = read_land_manifest(&cache_root);
    let aoi_digest = aoi_land_digest(preset_path).unwrap_or_else(|_| "none".to_string());

    let raw_bytes = read_raw_layer_bytes(
        project_dir,
        &cache_root,
        source_id,
        source,
        layer,
        layer_key,
    )?;

    if !layer_needs_geojson_pipeline(layer, &aoi_digest) {
        let digest =
            effective_layer_digest(&manifest, source_id, layer, Some(&raw_bytes), &aoi_digest);
        return Ok((raw_bytes, digest));
    }

    let raw_digest = digest_for_bytes(&raw_bytes);
    let pipeline_suffix = pipeline_digest_suffix(layer, &aoi_digest);
    let pipeline_cache = pipeline_cache_path_for_layer(
        &cache_root,
        source_id,
        layer_key,
        &raw_digest,
        &pipeline_suffix,
    );

    if let Some(bytes) = read_pipeline_cache(&pipeline_cache) {
        tracing::debug!(
            source_id = %source_id,
            layer_key = %layer_key,
            path = %pipeline_cache.display(),
            "land pipeline cache hit"
        );
        let digest = effective_layer_digest(&manifest, source_id, layer, Some(&bytes), &aoi_digest);
        return Ok((bytes, digest));
    }

    tracing::info!(
        source_id = %source_id,
        layer_key = %layer_key,
        "land pipeline cache miss"
    );
    let bytes = apply_land_layer_geojson_pipeline(&raw_bytes, preset_path, source, layer)?;
    write_pipeline_cache(&pipeline_cache, &bytes)?;
    let digest = effective_layer_digest(&manifest, source_id, layer, Some(&bytes), &aoi_digest);
    Ok((bytes, digest))
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
struct PipelineWarmEntry {
    source_size: u64,
    source_mtime_secs: u64,
    layers_key: String,
    pipeline_suffix: String,
}

#[derive(Debug, Default, Serialize, Deserialize)]
struct PipelineWarmFile {
    #[serde(default)]
    aoi_digest: String,
    #[serde(default)]
    entries: HashMap<String, PipelineWarmEntry>,
}

pub struct LandPipelineWarmCache {
    path: PathBuf,
    aoi_digest: String,
    entries: Mutex<HashMap<String, PipelineWarmEntry>>,
    dirty: std::sync::atomic::AtomicBool,
}

impl LandPipelineWarmCache {
    pub fn open(project_dir: &Path, aoi_digest: &str) -> Result<Self> {
        let cache_root = project_dir.join(".peaky/cache/land");
        fs::create_dir_all(&cache_root)?;
        let path = cache_root.join("pipeline-warm.json");
        let (file_aoi, entries) = if path.is_file() {
            fs::read_to_string(&path)
                .ok()
                .and_then(|text| serde_json::from_str::<PipelineWarmFile>(&text).ok())
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
        let entries = self.entries.lock().expect("pipeline warm cache").clone();
        let mut keys: Vec<_> = entries.keys().cloned().collect();
        keys.sort();
        let ordered: HashMap<String, PipelineWarmEntry> = keys
            .into_iter()
            .filter_map(|k| entries.get(&k).map(|v| (k, v.clone())))
            .collect();
        let payload = PipelineWarmFile {
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

    fn entry_key(source_id: &str, layer_key: &str) -> String {
        format!("{source_id}/{layer_key}")
    }

    pub fn is_warm(
        &self,
        source_id: &str,
        layer_key: &str,
        source_fingerprint: (u64, u64),
        layers_key: &str,
        pipeline_suffix: &str,
        pipeline_path: &Path,
    ) -> bool {
        if !pipeline_path.is_file() {
            return false;
        }
        let key = Self::entry_key(source_id, layer_key);
        let cache = self.entries.lock().expect("pipeline warm cache");
        cache.get(&key).is_some_and(|row| {
            row.source_size == source_fingerprint.0
                && row.source_mtime_secs == source_fingerprint.1
                && row.layers_key == layers_key
                && row.pipeline_suffix == pipeline_suffix
        })
    }

    pub fn remember_warm(
        &self,
        source_id: &str,
        layer_key: &str,
        source_fingerprint: (u64, u64),
        layers_key: &str,
        pipeline_suffix: &str,
    ) {
        use std::sync::atomic::Ordering;
        let key = Self::entry_key(source_id, layer_key);
        let row = PipelineWarmEntry {
            source_size: source_fingerprint.0,
            source_mtime_secs: source_fingerprint.1,
            layers_key: layers_key.to_string(),
            pipeline_suffix: pipeline_suffix.to_string(),
        };
        let mut cache = self.entries.lock().expect("pipeline warm cache");
        if cache.get(&key) == Some(&row) {
            return;
        }
        cache.insert(key, row);
        self.dirty.store(true, Ordering::Relaxed);
    }
}

#[derive(Debug, Clone, Default, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LandPipelineWarmStats {
    pub warmed: usize,
    pub skipped: usize,
    pub failed: usize,
}

struct PipelineWarmJob {
    source_id: String,
    layer_key: String,
}

fn source_is_gdb(path: &str) -> bool {
    path.replace('\\', "/")
        .to_ascii_lowercase()
        .ends_with(".gdb")
}

/// Pre-build AOI-clipped / filtered GeoJSON for enabled layers (boot-time).
pub fn warm_land_pipeline_caches_for_preset(
    preset_path: &Path,
    pipeline_warm_cache: &LandPipelineWarmCache,
) -> Result<LandPipelineWarmStats> {
    let preset = load_preset(preset_path)?;
    let project_dir = preset_path
        .parent()
        .context("preset path must have parent")?;
    let cache_root = land_cache_dir(preset_path);
    let aoi_digest = aoi_land_digest(preset_path).unwrap_or_else(|_| "none".to_string());

    let mut jobs = Vec::new();
    for (source_id, entry) in preset.land.sources.iter().filter(|(_, e)| e.is_enabled()) {
        for layer in &entry.layers {
            if !layer_needs_geojson_pipeline(layer, &aoi_digest) {
                continue;
            }
            jobs.push(PipelineWarmJob {
                source_id: source_id.clone(),
                layer_key: layer.layer_key(),
            });
        }
    }

    if jobs.is_empty() {
        tracing::info!("land pipeline warm: no layers need pipeline");
        return Ok(LandPipelineWarmStats::default());
    }

    let workers = land_boot_workers().min(2);
    tracing::info!(count = jobs.len(), workers, "land pipeline warm: starting");

    let stats: Mutex<LandPipelineWarmStats> = Mutex::new(LandPipelineWarmStats::default());
    let preset_path = preset_path.to_path_buf();
    let progress = LandBootProgress::new("land pipeline warm", jobs.len());

    land_boot_pool().install(|| {
        jobs.par_iter().for_each(|job| {
            let unit = format!("{}/{}", job.source_id, job.layer_key);
            progress.begin_unit(&job.source_id, &unit, "pipeline layer");
            let result = warm_one_pipeline_layer(
                &preset_path,
                project_dir,
                &cache_root,
                &aoi_digest,
                pipeline_warm_cache,
                &job.source_id,
                &job.layer_key,
            );
            let mut stats = stats.lock().expect("pipeline warm stats");
            match result {
                Ok(WarmLayerOutcome::Built) => {
                    stats.warmed += 1;
                    progress.finish_unit(&job.source_id, &unit, "built");
                }
                Ok(WarmLayerOutcome::Skipped) => {
                    stats.skipped += 1;
                    progress.finish_unit(&job.source_id, &unit, "skipped");
                }
                Err(e) => {
                    stats.failed += 1;
                    progress.finish_unit(&job.source_id, &unit, "failed");
                    tracing::warn!(
                        source_id = %job.source_id,
                        layer_key = %job.layer_key,
                        error = %e,
                        "land pipeline warm: failed"
                    );
                }
            }
        });
    });

    pipeline_warm_cache.persist()?;
    let stats = stats.into_inner().expect("pipeline warm stats");
    tracing::info!(
        warmed = stats.warmed,
        skipped = stats.skipped,
        failed = stats.failed,
        "land pipeline warm: complete"
    );
    Ok(stats)
}

enum WarmLayerOutcome {
    Built,
    Skipped,
}

fn warm_one_pipeline_layer(
    preset_path: &Path,
    project_dir: &Path,
    cache_root: &Path,
    aoi_digest: &str,
    pipeline_warm_cache: &LandPipelineWarmCache,
    source_id: &str,
    layer_key: &str,
) -> Result<WarmLayerOutcome> {
    let preset = load_preset(preset_path)?;
    let source = preset
        .land
        .sources
        .get(source_id)
        .with_context(|| format!("unknown land source: {source_id}"))?;
    let layer = layer_by_key(source, layer_key)
        .with_context(|| format!("unknown layer key: {layer_key}"))?;

    let rel = source.path.replace('\\', "/");
    let abs = project_dir.join(&rel);
    let is_gdb = source_is_gdb(&rel);
    let fingerprint = source_fingerprint(&abs, is_gdb)?;
    let layers_key = land_source_layers_key(source);
    let pipeline_suffix = pipeline_digest_suffix(layer, aoi_digest);

    let raw_bytes = read_raw_layer_bytes(
        project_dir,
        cache_root,
        source_id,
        source,
        layer,
        layer_key,
    )?;
    let raw_digest = digest_for_bytes(&raw_bytes);
    let pipeline_path = pipeline_cache_path_for_layer(
        cache_root,
        source_id,
        layer_key,
        &raw_digest,
        &pipeline_suffix,
    );

    if pipeline_warm_cache.is_warm(
        source_id,
        layer_key,
        fingerprint,
        &layers_key,
        &pipeline_suffix,
        &pipeline_path,
    ) {
        return Ok(WarmLayerOutcome::Skipped);
    }

    if read_pipeline_cache(&pipeline_path).is_some() {
        pipeline_warm_cache.remember_warm(
            source_id,
            layer_key,
            fingerprint,
            &layers_key,
            &pipeline_suffix,
        );
        return Ok(WarmLayerOutcome::Skipped);
    }

    tracing::info!(
        source_id = %source_id,
        layer_key = %layer_key,
        "land pipeline warm: building"
    );
    let bytes = apply_land_layer_geojson_pipeline(&raw_bytes, preset_path, source, layer)?;
    write_pipeline_cache(&pipeline_path, &bytes)?;
    pipeline_warm_cache.remember_warm(
        source_id,
        layer_key,
        fingerprint,
        &layers_key,
        &pipeline_suffix,
    );
    tracing::info!(
        source_id = %source_id,
        layer_key = %layer_key,
        bytes = bytes.len(),
        "land pipeline warm: ready"
    );
    Ok(WarmLayerOutcome::Built)
}
