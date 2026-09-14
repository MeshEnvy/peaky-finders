//! In-process + disk cache for `GET /api/p/{slug}/peaks`.

use std::collections::HashMap;
use std::path::Path;
use std::sync::{LazyLock, Mutex};

use anyhow::Result;
use peaky_preset::{
    build_peaks_list_json, load_peaks_catalog_thin, peaks_list_fingerprint, PeaksCatalog,
    read_peaks_list_disk_cache, write_peaks_list_disk_cache,
};
use serde_json::Value;

static LIST_CACHE: LazyLock<Mutex<HashMap<String, (String, Value)>>> =
    LazyLock::new(|| Mutex::new(HashMap::new()));

static CATALOG_CACHE: LazyLock<Mutex<HashMap<String, (String, PeaksCatalog)>>> =
    LazyLock::new(|| Mutex::new(HashMap::new()));

fn project_cache_key(preset_path: &Path) -> String {
    preset_path
        .parent()
        .map(|p| p.canonicalize().unwrap_or_else(|_| p.to_path_buf()))
        .unwrap_or_else(|| preset_path.to_path_buf())
        .display()
        .to_string()
}

pub fn cached_peaks_list(preset_path: &Path) -> Result<Value> {
    let fingerprint = peaks_list_fingerprint(preset_path)?;
    let key = project_cache_key(preset_path);
    if let Ok(cache) = LIST_CACHE.lock() {
        if let Some((fp, payload)) = cache.get(&key) {
            if fp == &fingerprint {
                return Ok(payload.clone());
            }
        }
    }
    if let Some(payload) = read_peaks_list_disk_cache(preset_path, &fingerprint) {
        if let Ok(mut cache) = LIST_CACHE.lock() {
            cache.insert(key, (fingerprint, payload.clone()));
        }
        return Ok(payload);
    }
    let catalog = load_peaks_catalog_thin(preset_path)?;
    let payload = build_peaks_list_json(&catalog);
    let _ = write_peaks_list_disk_cache(preset_path, &catalog);
    if let Ok(mut cache) = LIST_CACHE.lock() {
        cache.insert(key, (fingerprint, payload.clone()));
    }
    Ok(payload)
}

/// Thin ``peaks/`` rows for RF scans (no ``access/`` merge). Cached in-process by fingerprint.
pub fn cached_peaks_catalog_thin(preset_path: &Path) -> Result<PeaksCatalog> {
    let fingerprint = peaks_list_fingerprint(preset_path)?;
    let key = project_cache_key(preset_path);
    if let Ok(cache) = CATALOG_CACHE.lock() {
        if let Some((fp, catalog)) = cache.get(&key) {
            if fp == &fingerprint {
                return Ok(catalog.clone());
            }
        }
    }
    let catalog = load_peaks_catalog_thin(preset_path)?;
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
    peaky_preset::invalidate_peaks_list_cache(preset_path);
}
