//! Eligible-peaks catalog API (`peaks.yaml`).

use std::path::Path;

use anyhow::Result;
use peaky_preset::load_peaks_catalog;
use serde_json::{json, Value};

pub fn list_peaks_payload(preset_path: &Path) -> Result<Value> {
    let catalog = load_peaks_catalog(preset_path)?;
    let mut peaks: Vec<Value> = catalog
        .entries
        .iter()
        .filter(|(_, entry)| !entry.deny.unwrap_or(false))
        .map(|(slug, entry)| {
            json!({
                "slug": slug,
                "name": entry.name,
                "lat": entry.lat(),
                "lon": entry.lon(),
                "elev_m": entry.elev_m,
                "source": entry.source,
                "road_m": entry.road_m,
                "road_lat": entry.road_loc.map(|loc| loc[0]),
                "road_lon": entry.road_loc.map(|loc| loc[1]),
                "hike_m": entry.hike_m,
                "max_slope_deg": entry.max_slope_deg,
            })
        })
        .collect();
    peaks.sort_by(|a, b| {
        a.get("slug")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .cmp(b.get("slug").and_then(|v| v.as_str()).unwrap_or(""))
    });
    Ok(json!({
        "generated_at": catalog.generated_at,
        "rules": catalog.rules,
        "peaks": peaks,
    }))
}
