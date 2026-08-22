//! Warm `.peaky/cache/land/` GeoJSON exports from GDB sources.

use std::collections::{HashMap, HashSet};
use std::fs;
use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use peaky_preset::{load_preset, slugify_files_segment, LandLayerEntry, LandSourceEntry};
use serde_json::{json, Value};

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

pub fn ensure_land_caches_for_preset(preset_path: &Path, verbose: bool) -> Result<LandCacheWarmStats> {
    let preset = load_preset(preset_path).context("load preset")?;
    let project_dir = preset_path
        .parent()
        .context("preset path must have parent")?;
    let cache_root = land_cache_root(preset_path);
    fs::create_dir_all(&cache_root)?;

    let mut stats = LandCacheWarmStats::default();
    let mut raw_exports: HashMap<(String, String), PathBuf> = HashMap::new();
    let mut manifest: HashMap<String, Value> = read_manifest(&cache_root);

    for (source_id, source) in &preset.land.sources {
        if !source.is_enabled() {
            continue;
        }
        warm_one_source(
            project_dir,
            source_id,
            source,
            &cache_root,
            verbose,
            &mut stats,
            &mut raw_exports,
            &mut manifest,
        )?;
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

    Ok(stats)
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

fn warm_one_source(
    project_dir: &Path,
    source_id: &str,
    source: &LandSourceEntry,
    cache_root: &Path,
    verbose: bool,
    stats: &mut LandCacheWarmStats,
    raw_exports: &mut HashMap<(String, String), PathBuf>,
    manifest: &mut HashMap<String, Value>,
) -> Result<()> {
    let rel = source.path.replace('\\', "/");
    if !rel.to_ascii_lowercase().ends_with(".gdb") {
        stats.skipped_geojson += 1;
        return Ok(());
    }
    let abs_path = project_dir.join(&rel);
    if !abs_path.is_dir() {
        eprintln!("warn: missing GDB {rel}");
        stats.missing += 1;
        return Ok(());
    }

    let mut seen_keys: HashSet<String> = HashSet::new();
    for layer in &source.layers {
        warm_layer(
            source_id,
            &rel,
            &abs_path,
            layer,
            cache_root,
            verbose,
            stats,
            raw_exports,
            manifest,
            &mut seen_keys,
        )?;
    }
    Ok(())
}

#[allow(clippy::too_many_arguments)]
fn warm_layer(
    source_id: &str,
    rel: &str,
    abs_path: &Path,
    layer: &LandLayerEntry,
    cache_root: &Path,
    verbose: bool,
    stats: &mut LandCacheWarmStats,
    raw_exports: &mut HashMap<(String, String), PathBuf>,
    manifest: &mut HashMap<String, Value>,
    seen_keys: &mut HashSet<String>,
) -> Result<()> {
    let layer_key = layer.layer_key();
    if !seen_keys.insert(layer_key.clone()) {
        return Ok(());
    }

    let dest = layer_cache_path(cache_root, source_id, &layer_key);
    let export_key = (rel.to_string(), layer.name.clone());
    let tmp = raw_exports.entry(export_key.clone()).or_insert_with(|| {
        export_tmp_path(cache_root, rel, &layer.name)
    });

    if !tmp.is_file() {
        if let Err(e) = export_gdb_layer_to_geojson(abs_path, &layer.name, tmp) {
            eprintln!("error: ogr2ogr failed for {source_id}/{}: {e:#}", layer.name);
            stats.missing += 1;
            return Ok(());
        }
        stats.exported += 1;
    }

    if let Some(parent) = dest.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::copy(tmp, &dest).with_context(|| format!("copy cache {}", dest.display()))?;
    stats.copied += 1;

    let safe = slugify_files_segment(&layer_key);
    let stem = if safe.is_empty() { "layer" } else { safe.as_str() };
    manifest.insert(format!("{source_id}/{stem}"), json!({ "digest": "warm" }));

    if verbose {
        eprintln!("cache {source_id}/{stem}.geojson");
    }
    Ok(())
}
