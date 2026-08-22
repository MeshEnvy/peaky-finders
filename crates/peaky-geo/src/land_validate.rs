//! Validate on-disk land source files (GeoJSON parse, GDB layer presence).

use std::collections::HashSet;
use std::path::Path;

use anyhow::{bail, Context, Result};
use geojson::FeatureCollection;
use peaky_preset::LandSourceEntry;

use crate::land_gdb::list_preview_layers;

pub fn validate_land_source(project_dir: &Path, entry: &LandSourceEntry) -> Result<()> {
    let rel = entry.path.replace('\\', "/");
    let abs = project_dir.join(&rel);
    if rel.to_ascii_lowercase().ends_with(".geojson") {
        if !abs.is_file() {
            bail!("land data not found: {rel}");
        }
        let bytes = std::fs::read(&abs).with_context(|| format!("read {rel}"))?;
        let _: FeatureCollection =
            serde_json::from_slice(&bytes).context("invalid GeoJSON (truncated or corrupt)")?;
        return Ok(());
    }
    if rel.to_ascii_lowercase().ends_with(".gdb") {
        if !abs.is_dir() {
            bail!("land GDB not found: {rel}");
        }
        let layers = list_preview_layers(&abs).with_context(|| format!("list layers in {rel}"))?;
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
