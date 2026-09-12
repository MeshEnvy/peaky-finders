//! Metadata-only GeoJSON for meshenvy.org /map.

use std::path::Path;

use anyhow::Result;
use peaky_preset::Preset;
use peaky_serve::rf::preset_to_request;
use serde_json::{json, Value};
use time::format_description::well_known::Rfc3339;
use time::OffsetDateTime;
use tracing::info;

pub fn utc_now_meta() -> String {
    OffsetDateTime::now_utc()
        .format(&Rfc3339)
        .unwrap_or_else(|_| "1970-01-01T00:00:00Z".to_string())
}

pub fn build_feature_collection(
    fleet_site_count: usize,
    preset: &Preset,
    overlay_meta: Value,
) -> Result<Value> {
    let req = preset_to_request(preset, 0.0, 0.0, None)?;
    let tx_eirp = req.tx_power + req.tx_gain - req.system_loss;
    let meta = json!({
        "generated_at": utc_now_meta(),
        "model": "splatter_v5",
        "frequency_mhz": req.frequency_mhz,
        "rx_sensitivity_dbm": req.signal_threshold,
        "tx_eirp_dbm": tx_eirp,
        "signal_threshold_dbm": req.signal_threshold,
        "fleet_site_count": fleet_site_count,
    });
    let mut merged = meta.as_object().cloned().unwrap_or_default();
    if let Some(extra) = overlay_meta.as_object() {
        for (k, v) in extra {
            merged.insert(k.clone(), v.clone());
        }
    }
    Ok(json!({
        "type": "FeatureCollection",
        "features": [],
        "meshenvy_meta": merged,
    }))
}

pub fn write_geojson(fc: &Value, dest: &Path) -> Result<()> {
    if let Some(parent) = dest.parent() {
        std::fs::create_dir_all(parent)?;
    }
    info!("writing GeoJSON -> {}", dest.display());
    let text = serde_json::to_string(fc)?;
    std::fs::write(dest, text)?;
    Ok(())
}
