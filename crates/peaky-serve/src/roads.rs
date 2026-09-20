//! BLM GTLF inventory GeoJSON for map overlays.

use std::path::Path;

use anyhow::{Context, Result};
use peaky_peaks::read_gtlf_inventory_geojson;

pub fn gtlf_inventory_geojson_bytes(preset_path: &Path, foot: bool) -> Result<Vec<u8>> {
    let project_dir = preset_path
        .parent()
        .context("preset path has no parent")?;
    let geo = read_gtlf_inventory_geojson(project_dir, foot)?;
    serde_json::to_vec(&geo).context("serialize gtlf geojson")
}
