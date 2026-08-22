//! Validate on-disk land source files (GeoJSON parse, GDB layer presence).

use std::collections::HashSet;
use std::path::Path;

use anyhow::{bail, Context, Result};
use geojson::FeatureCollection;
use peaky_preset::LandSourceEntry;

use crate::land_gdb::list_preview_layers;
use crate::land_validate_cache::{source_fingerprint, LandValidateCache};

pub fn validate_land_source(project_dir: &Path, entry: &LandSourceEntry) -> Result<()> {
    validate_land_source_with_cache(project_dir, entry, None)
}

pub fn validate_land_source_with_cache(
    project_dir: &Path,
    entry: &LandSourceEntry,
    cache: Option<&LandValidateCache>,
) -> Result<()> {
    let rel = entry.path.replace('\\', "/");
    let abs = project_dir.join(&rel);
    let lower = rel.to_ascii_lowercase();
    let is_gdb = lower.ends_with(".gdb");
    let is_geojson = lower.ends_with(".geojson");

    if let Some(cache) = cache {
        let exists = if is_gdb { abs.is_dir() } else { abs.is_file() };
        if exists {
            if let Ok(fingerprint) = source_fingerprint(&abs, is_gdb) {
                if cache.lookup(&rel, entry, fingerprint) {
                    tracing::debug!(
                        path = %rel,
                        size = fingerprint.0,
                        mtime_secs = fingerprint.1,
                        "land validate: cached"
                    );
                    return Ok(());
                }
            }
        }
        let result = validate_land_source_inner(project_dir, entry, &rel, &abs, is_gdb, is_geojson);
        if result.is_ok() && exists {
            if let Ok(fingerprint) = source_fingerprint(&abs, is_gdb) {
                cache.remember_ok(&rel, entry, fingerprint);
            }
        }
        return result;
    }

    validate_land_source_inner(project_dir, entry, &rel, &abs, is_gdb, is_geojson)
}

fn validate_land_source_inner(
    _project_dir: &Path,
    entry: &LandSourceEntry,
    rel: &str,
    abs: &Path,
    is_gdb: bool,
    is_geojson: bool,
) -> Result<()> {
    if is_geojson {
        if !abs.is_file() {
            bail!("land data not found: {rel}");
        }
        let bytes = std::fs::read(abs).with_context(|| format!("read {rel}"))?;
        let _: FeatureCollection =
            serde_json::from_slice(&bytes).context("invalid GeoJSON (truncated or corrupt)")?;
        return Ok(());
    }
    if is_gdb {
        if !abs.is_dir() {
            bail!("land GDB not found: {rel}");
        }
        let layers = list_preview_layers(abs).with_context(|| format!("list layers in {rel}"))?;
        let names: HashSet<_> = layers.iter().map(|l| l.name.as_str()).collect();
        let mut required: HashSet<&str> = HashSet::new();
        for layer in &entry.layers {
            required.insert(layer.name.as_str());
        }
        for name in required {
            if !names.contains(name) {
                bail!("layer `{name}` not found in GDB {rel}");
            }
        }
        return Ok(());
    }
    bail!("unsupported land path (expected .geojson or .gdb): {rel}");
}
