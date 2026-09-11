//! Eligible-peaks catalog API (`peaks.yaml`).

use std::path::Path;
use std::sync::Arc;

use anyhow::{Context, Result};
use peaky_peaks::{profile_hike_detailed, HikeSampleElev};
use peaky_preset::load_peaks_catalog;
use serde_json::{json, Value};
use splatter::Session;

struct SessionElev<'a>(&'a Session);

impl HikeSampleElev for SessionElev<'_> {
    fn sample_elev_m(&self, lat: f64, lon: f64) -> f64 {
        self.0.sample_elev_m(lat, lon)
    }
}

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
                "hike": entry.hike,
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

pub fn peak_hike_payload(
    preset_path: &Path,
    session: &Arc<Session>,
    peak_slug: &str,
) -> Result<Value> {
    let catalog = load_peaks_catalog(preset_path)?;
    let entry = catalog
        .entries
        .get(peak_slug)
        .filter(|e| !e.deny.unwrap_or(false))
        .context("peak not found")?;
    let road_loc = entry.road_loc.context("peak has no road access")?;
    let road_lat = road_loc[0];
    let road_lon = road_loc[1];
    let peak_lat = entry.lat();
    let peak_lon = entry.lon();

    let hike = if let Some(stored) = &entry.hike {
        serde_json::to_value(stored).context("serialize stored hike")?
    } else {
        session
            .ensure_tiles_for_points(&[(peak_lat, peak_lon), (road_lat, road_lon)], 900.0)
            .context("preload DEM for hike profile")?;
        let elev = SessionElev(session.as_ref());
        let detail = profile_hike_detailed(&elev, road_lat, road_lon, peak_lat, peak_lon, 30.0)
            .context("hike profile")?;
        serde_json::to_value(detail).context("serialize hike profile")?
    };

    Ok(json!({
        "slug": peak_slug,
        "name": entry.name,
        "lat": peak_lat,
        "lon": peak_lon,
        "elev_m": entry.elev_m,
        "road_m": entry.road_m,
        "road_lat": road_lat,
        "road_lon": road_lon,
        "hike": hike,
    }))
}
