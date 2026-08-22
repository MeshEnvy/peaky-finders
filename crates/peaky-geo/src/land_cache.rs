//! Warm `.peaky/cache/land/` GeoJSON exports from GDB sources.

use std::collections::{HashMap, HashSet};
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::Mutex;

use anyhow::{Context, Result};
use peaky_preset::{load_preset, slugify_files_segment, LandLayerEntry, LandSourceEntry};
use rayon::prelude::*;
use serde_json::{json, Value};

use crate::land_boot::{land_boot_pool, land_boot_workers};
use crate::land_gdb::export_gdb_layer_to_geojson;

#[derive(Debug, Clone, Default, serde::Serialize)]
pub struct LandCacheWarmStats {
    pub exported: u32,
    pub copied: u32,
    pub skipped_geojson: u32,
    pub missing: u32,
}

pub fn land_cache_root(preset_path: &Path) -> PathBuf {
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

fn export_tmp_path(cache_root: &Path, rel_path: &str, layer_name: &str) -> PathBuf {
    cache_root
        .join("_export")
        .join(slugify_files_segment(rel_path))
        .join(format!("{}.geojson", slugify_files_segment(layer_name)))
}

struct LayerCacheJob {
    source_id: String,
    rel: String,
    layer_name: String,
    dest: PathBuf,
    manifest_key: String,
}

struct ExportTask {
    rel: String,
    layer_name: String,
    abs_gdb: PathBuf,
    tmp: PathBuf,
}

pub fn ensure_land_caches_for_preset(preset_path: &Path, _verbose: bool) -> Result<LandCacheWarmStats> {
    let preset = load_preset(preset_path).context("load preset")?;
    let project_dir = preset_path
        .parent()
        .context("preset path must have parent")?;
    let cache_root = land_cache_root(preset_path);
    fs::create_dir_all(&cache_root)?;

    let mut stats = LandCacheWarmStats::default();
    let mut manifest: HashMap<String, Value> = read_manifest(&cache_root);
    let mut jobs: Vec<LayerCacheJob> = Vec::new();
    let mut export_by_key: HashMap<(String, String), ExportTask> = HashMap::new();

    for (source_id, source) in &preset.land.sources {
        if !source.is_enabled() {
            continue;
        }
        collect_source_jobs(
            project_dir,
            source_id,
            source,
            &cache_root,
            &mut stats,
            &mut jobs,
            &mut export_by_key,
        )?;
    }

    if jobs.is_empty() && export_by_key.is_empty() {
        tracing::info!("land cache: no GDB layers to warm");
        return Ok(stats);
    }

    let workers = land_boot_workers();
    tracing::info!(
        layers = jobs.len(),
        exports = export_by_key.len(),
        workers,
        "land cache: warming"
    );

    let exported = AtomicU32::new(0);
    let export_failed = AtomicU32::new(0);
    let export_tasks: Vec<ExportTask> = export_by_key.into_values().collect();

    land_boot_pool().install(|| {
        export_tasks.par_iter().for_each(|task| {
            if task.tmp.is_file() {
                tracing::info!(
                    path = %task.rel,
                    layer = %task.layer_name,
                    "land cache: export skip (cached)"
                );
                return;
            }
            tracing::info!(
                path = %task.rel,
                layer = %task.layer_name,
                "land cache: export start"
            );
            match export_gdb_layer_to_geojson(&task.abs_gdb, &task.layer_name, &task.tmp) {
                Ok(()) => {
                    exported.fetch_add(1, Ordering::Relaxed);
                    tracing::info!(
                        path = %task.rel,
                        layer = %task.layer_name,
                        "land cache: export done"
                    );
                }
                Err(e) => {
                    export_failed.fetch_add(1, Ordering::Relaxed);
                    tracing::warn!(
                        path = %task.rel,
                        layer = %task.layer_name,
                        error = %e,
                        "land cache: export failed"
                    );
                }
            }
        });
    });
    stats.exported = exported.load(Ordering::Relaxed);
    stats.missing += export_failed.load(Ordering::Relaxed);

    let copied = AtomicU32::new(0);
    let copy_failed = AtomicU32::new(0);
    let manifest_lock: Mutex<HashMap<String, Value>> = Mutex::new(HashMap::new());

    land_boot_pool().install(|| {
        jobs.par_iter().for_each(|job| {
            let tmp = export_tmp_path(&cache_root, &job.rel, &job.layer_name);
            if !tmp.is_file() {
                copy_failed.fetch_add(1, Ordering::Relaxed);
                tracing::warn!(
                    source_id = %job.source_id,
                    path = %job.rel,
                    layer = %job.layer_name,
                    "land cache: copy skip (export missing)"
                );
                return;
            }
            tracing::info!(
                source_id = %job.source_id,
                path = %job.rel,
                layer = %job.layer_name,
                "land cache: copy"
            );
            if let Some(parent) = job.dest.parent() {
                if fs::create_dir_all(parent).is_err() {
                    copy_failed.fetch_add(1, Ordering::Relaxed);
                    return;
                }
            }
            match fs::copy(&tmp, &job.dest) {
                Ok(_) => {
                    copied.fetch_add(1, Ordering::Relaxed);
                    manifest_lock
                        .lock()
                        .expect("land cache manifest")
                        .insert(job.manifest_key.clone(), json!({ "digest": "warm" }));
                    tracing::info!(
                        source_id = %job.source_id,
                        path = %job.rel,
                        layer = %job.layer_name,
                        dest = %job.dest.display(),
                        "land cache: ready"
                    );
                }
                Err(e) => {
                    copy_failed.fetch_add(1, Ordering::Relaxed);
                    tracing::warn!(
                        source_id = %job.source_id,
                        path = %job.rel,
                        layer = %job.layer_name,
                        error = %e,
                        "land cache: copy failed"
                    );
                }
            }
        });
    });
    stats.copied = copied.load(Ordering::Relaxed);
    stats.missing += copy_failed.load(Ordering::Relaxed);

    for (key, value) in manifest_lock.into_inner().expect("land cache manifest") {
        manifest.insert(key, value);
    }

    let manifest_path = cache_root.join("manifest.json");
    let mut keys: Vec<_> = manifest.keys().cloned().collect();
    keys.sort();
    let ordered: HashMap<String, Value> = keys
        .into_iter()
        .filter_map(|k| manifest.get(&k).map(|v| (k, v.clone())))
        .collect();
    fs::write(
        &manifest_path,
        serde_json::to_string_pretty(&ordered)? + "\n",
    )
    .with_context(|| format!("write {}", manifest_path.display()))?;

    tracing::info!(
        exported = stats.exported,
        copied = stats.copied,
        skipped_geojson = stats.skipped_geojson,
        missing = stats.missing,
        "land cache: complete"
    );

    Ok(stats)
}

fn collect_source_jobs(
    project_dir: &Path,
    source_id: &str,
    source: &LandSourceEntry,
    cache_root: &Path,
    stats: &mut LandCacheWarmStats,
    jobs: &mut Vec<LayerCacheJob>,
    export_by_key: &mut HashMap<(String, String), ExportTask>,
) -> Result<()> {
    let rel = source.path.replace('\\', "/");
    if !rel.to_ascii_lowercase().ends_with(".gdb") {
        stats.skipped_geojson += 1;
        tracing::info!(
            source_id = %source_id,
            path = %rel,
            "land cache: skip (geojson source)"
        );
        return Ok(());
    }
    let abs_path = project_dir.join(&rel);
    if !abs_path.is_dir() {
        stats.missing += 1;
        tracing::warn!(
            source_id = %source_id,
            path = %rel,
            "land cache: skip (missing GDB)"
        );
        return Ok(());
    }

    let mut seen_keys: HashSet<String> = HashSet::new();
    for layer in &source.layers {
        push_layer_job(
            source_id,
            &rel,
            &abs_path,
            layer,
            cache_root,
            jobs,
            export_by_key,
            &mut seen_keys,
        );
    }
    Ok(())
}

fn push_layer_job(
    source_id: &str,
    rel: &str,
    abs_path: &Path,
    layer: &LandLayerEntry,
    cache_root: &Path,
    jobs: &mut Vec<LayerCacheJob>,
    export_by_key: &mut HashMap<(String, String), ExportTask>,
    seen_keys: &mut HashSet<String>,
) {
    let layer_key = layer.layer_key();
    if !seen_keys.insert(layer_key.clone()) {
        return;
    }

    let dest = layer_cache_path(cache_root, source_id, &layer_key);
    let safe = slugify_files_segment(&layer_key);
    let stem = if safe.is_empty() { "layer" } else { safe.as_str() };
    let manifest_key = format!("{source_id}/{stem}");
    let export_key = (rel.to_string(), layer.name.clone());
    export_by_key.entry(export_key.clone()).or_insert_with(|| ExportTask {
        rel: rel.to_string(),
        layer_name: layer.name.clone(),
        abs_gdb: abs_path.to_path_buf(),
        tmp: export_tmp_path(cache_root, rel, &layer.name),
    });
    jobs.push(LayerCacheJob {
        source_id: source_id.to_string(),
        rel: rel.to_string(),
        layer_name: layer.name.clone(),
        dest,
        manifest_key,
    });
}

fn read_manifest(cache_root: &Path) -> HashMap<String, Value> {
    let path = cache_root.join("manifest.json");
    if !path.is_file() {
        return HashMap::new();
    }
    fs::read_to_string(&path)
        .ok()
        .and_then(|text| serde_json::from_str(&text).ok())
        .unwrap_or_default()
}
