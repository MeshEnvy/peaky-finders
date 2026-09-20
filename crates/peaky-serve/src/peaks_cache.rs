//! In-process cache for `GET /api/p/{slug}/peaks`.

use std::collections::HashMap;
use std::path::Path;
use std::sync::{LazyLock, Mutex};

use anyhow::{Context, Result};
use peaky_peaks::{project_gtlf_fingerprint, project_pbf_fingerprint};
use peaky_preset::{
    build_peaks_list_json, load_latest_peaks_index, load_peaks_index, prefs_key_for_project,
    PeaksIndex,
};
use peaky_preset::project::ProjectLayout;
use serde_json::{json, Value};

static LIST_CACHE: LazyLock<Mutex<HashMap<String, (String, Value)>>> =
    LazyLock::new(|| Mutex::new(HashMap::new()));

static CATALOG_CACHE: LazyLock<Mutex<HashMap<String, (String, PeaksIndex)>>> =
    LazyLock::new(|| Mutex::new(HashMap::new()));

fn project_cache_key(preset_path: &Path) -> String {
    preset_path
        .parent()
        .map(|p| p.canonicalize().unwrap_or_else(|_| p.to_path_buf()))
        .unwrap_or_else(|| preset_path.to_path_buf())
        .display()
        .to_string()
}

fn index_fingerprint(preset_path: &Path) -> Result<String> {
    let layout = ProjectLayout::from_config_path(preset_path)?;
    let project_dir = &layout.project_dir;
    let gtlf = project_gtlf_fingerprint(project_dir);
    let pbf = project_pbf_fingerprint(project_dir);
    let prefs_key = prefs_key_for_project(preset_path, &gtlf, &pbf)?;
    let index_path = layout.peaks_index_path(&prefs_key);
    let mtime = std::fs::metadata(&index_path)
        .ok()
        .and_then(|m| m.modified().ok())
        .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|d| d.as_secs())
        .unwrap_or(0);
    Ok(format!("{prefs_key}:{mtime}"))
}

fn load_index_for_serve(preset_path: &Path) -> Result<Option<(String, PeaksIndex)>> {
    let layout = ProjectLayout::from_config_path(preset_path)?;
    let project_dir = &layout.project_dir;
    let gtlf = project_gtlf_fingerprint(project_dir);
    let pbf = project_pbf_fingerprint(project_dir);
    let prefs_key = prefs_key_for_project(preset_path, &gtlf, &pbf)?;
    let index = load_peaks_index(project_dir, &prefs_key)?;
    if index.peaks.is_empty() && index.manifest.generated_at.is_empty() {
        if let Some(latest) = load_latest_peaks_index(project_dir)? {
            return Ok(Some(latest));
        }
        return Ok(None);
    }
    Ok(Some((prefs_key, index)))
}

pub fn cached_peaks_list(preset_path: &Path) -> Result<Value> {
    let fingerprint = index_fingerprint(preset_path).unwrap_or_else(|_| "missing".into());
    let key = project_cache_key(preset_path);
    if let Ok(cache) = LIST_CACHE.lock() {
        if let Some((fp, payload)) = cache.get(&key) {
            if fp == &fingerprint {
                return Ok(payload.clone());
            }
        }
    }
    let Some((prefs_key, index)) = load_index_for_serve(preset_path)? else {
        let project_dir = preset_path.parent().context("preset parent")?;
        let prefs_key = prefs_key_for_project(
            preset_path,
            &project_gtlf_fingerprint(project_dir),
            &project_pbf_fingerprint(project_dir),
        )
        .unwrap_or_default();
        return Ok(json!({
            "peaks": {},
            "missing_catalog": true,
            "prefs_key": prefs_key,
        }));
    };
    let mut payload = build_peaks_list_json(&index);
    if let Some(obj) = payload.as_object_mut() {
        obj.insert("missing_catalog".into(), json!(false));
        obj.insert("prefs_key".into(), json!(prefs_key));
    }
    if let Ok(mut cache) = LIST_CACHE.lock() {
        cache.insert(key, (fingerprint, payload.clone()));
    }
    Ok(payload)
}

/// Thin index rows for RF scans (no profile blobs). Cached in-process by fingerprint.
pub fn cached_peaks_catalog_thin(preset_path: &Path) -> Result<PeaksIndex> {
    let fingerprint = index_fingerprint(preset_path)?;
    let key = project_cache_key(preset_path);
    if let Ok(cache) = CATALOG_CACHE.lock() {
        if let Some((fp, catalog)) = cache.get(&key) {
            if fp == &fingerprint {
                return Ok(catalog.clone());
            }
        }
    }
    let catalog = load_index_for_serve(preset_path)?
        .ok_or_else(|| anyhow::anyhow!("peaks catalog is empty; run peaky peaks build"))?
        .1;
    if let Ok(mut cache) = CATALOG_CACHE.lock() {
        cache.insert(key, (fingerprint, catalog.clone()));
    }
    Ok(catalog)
}

pub fn invalidate_cached_peaks_list(preset_path: &Path) {
    let key = project_cache_key(preset_path);
    if let Ok(mut cache) = LIST_CACHE.lock() {
        cache.remove(&key);
    }
    if let Ok(mut cache) = CATALOG_CACHE.lock() {
        cache.remove(&key);
    }
}
