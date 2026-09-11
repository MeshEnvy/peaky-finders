//! Eligible-peaks catalog + place access API.

use std::path::Path;
use std::sync::Arc;

use anyhow::{Context, Result};
use peaky_peaks::{profile_along_polyline, profile_hike_detailed, warm_place_access, HikeSampleElev};
use peaky_preset::{
    ensure_access_meta, load_access, load_peak, load_peaks_catalog, peak_access_compute_key,
    place_access_compute_key, place_access_is_fresh, PlaceAccess,
};
use serde_json::{json, Value};
use splatter::Session;

struct SessionElev<'a>(&'a Session);

impl HikeSampleElev for SessionElev<'_> {
    fn sample_elev_m(&self, lat: f64, lon: f64) -> f64 {
        self.0.sample_elev_m(lat, lon)
    }
}

fn access_json(slug: &str, access: &PlaceAccess) -> Value {
    json!({
        "slug": slug,
        "status": "ready",
        "compute_key": access.compute_key,
        "paved_lat": access.paved_loc.map(|loc| loc[0]),
        "paved_lon": access.paved_loc.map(|loc| loc[1]),
        "road_lat": access.road_loc.map(|loc| loc[0]),
        "road_lon": access.road_loc.map(|loc| loc[1]),
        "hike_m": access.hike_m,
        "max_slope_deg": access.max_slope_deg,
        "hike": access.hike,
        "jeep_m": access.jeep_m,
        "jeep": access.jeep,
    })
}

/// Keys that count as fresh for display (site warm or peak-built).
fn expected_access_keys(preset_path: &Path) -> Vec<String> {
    let access_meta = ensure_access_meta(preset_path).unwrap_or_default();
    let mut keys = vec![place_access_compute_key(&access_meta)];
    let road_search = load_peaks_catalog(preset_path)
        .map(|c| c.rules.max_hike_m)
        .unwrap_or_else(|_| peaky_preset::PeakAccessRules::default().max_hike_m);
    keys.push(peak_access_compute_key(&access_meta, road_search));
    keys
}

pub fn access_fresh_on_disk(preset_path: &Path, access: &PlaceAccess) -> bool {
    let keys = expected_access_keys(preset_path);
    let refs: Vec<&str> = keys.iter().map(String::as_str).collect();
    place_access_is_fresh(access, &refs)
}

pub fn access_payload_json(slug: &str, access: &PlaceAccess) -> Value {
    access_json(slug, access)
}

/// Thin peak list (no profile blobs). Map uses 2-point fallbacks from paved/road locs.
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
                "paved_lat": entry.paved_loc.map(|loc| loc[0]),
                "paved_lon": entry.paved_loc.map(|loc| loc[1]),
                "jeep_m": entry.jeep_m,
                "compute_key": entry.compute_key,
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

pub fn place_access_payload(
    preset_path: &Path,
    session: &Arc<Session>,
    place_slug: &str,
    warm_if_missing: bool,
    warm_lat: Option<f64>,
    warm_lon: Option<f64>,
) -> Result<Value> {
    if let Some(access) = load_access(preset_path, place_slug)? {
        if access_fresh_on_disk(preset_path, &access) || !warm_if_missing {
            return Ok(access_json(place_slug, &access));
        }
        let warm_coords = if let Some(peak) = load_peak(preset_path, place_slug)? {
            Some((peak.lat(), peak.lon()))
        } else if let (Some(lat), Some(lon)) = (warm_lat, warm_lon) {
            Some((lat, lon))
        } else {
            None
        };
        if let Some((lat, lon)) = warm_coords {
            if let Some(fresh) = warm_place_access(preset_path, session, place_slug, lat, lon)? {
                return Ok(access_json(place_slug, &fresh));
            }
        }
        return Ok(access_json(place_slug, &access));
    }
    // Peak row may still embed legacy profiles until migrated (load_peak is thin).
    if let Some(peak) = load_peak(preset_path, place_slug)? {
        let access = peak.to_place_access();
        if access_fresh_on_disk(preset_path, &access) || !warm_if_missing {
            return Ok(access_json(place_slug, &access));
        }
        if let Some(fresh) =
            warm_place_access(preset_path, session, place_slug, peak.lat(), peak.lon())?
        {
            return Ok(access_json(place_slug, &fresh));
        }
        return Ok(access_json(place_slug, &access));
    }
    if warm_if_missing {
        let lat = warm_lat.context("lat required to warm access")?;
        let lon = warm_lon.context("lon required to warm access")?;
        if let Some(access) = warm_place_access(preset_path, session, place_slug, lat, lon)? {
            return Ok(access_json(place_slug, &access));
        }
        anyhow::bail!("access not found for {place_slug}");
    }
    anyhow::bail!("access not found for {place_slug}")
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
    let access = load_access(preset_path, peak_slug)?;
    let road_loc = access
        .as_ref()
        .and_then(|a| a.road_loc)
        .or(entry.road_loc)
        .context("peak has no road access")?;
    let road_lat = road_loc[0];
    let road_lon = road_loc[1];
    let peak_lat = entry.lat();
    let peak_lon = entry.lon();

    let hike = if let Some(stored) = access.as_ref().and_then(|a| a.hike.as_ref()) {
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

pub fn peak_jeep_payload(
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
    let access = load_access(preset_path, peak_slug)?;
    let road_loc = access
        .as_ref()
        .and_then(|a| a.road_loc)
        .or(entry.road_loc)
        .context("peak has no park point")?;
    let road_lat = road_loc[0];
    let road_lon = road_loc[1];
    let paved_loc = access
        .as_ref()
        .and_then(|a| a.paved_loc)
        .or(entry.paved_loc);

    let jeep = if let Some(stored) = access.as_ref().and_then(|a| a.jeep.as_ref()) {
        serde_json::to_value(stored).context("serialize stored jeep")?
    } else {
        let paved_loc = paved_loc.context("peak has no paved anchor")?;
        let paved_lat = paved_loc[0];
        let paved_lon = paved_loc[1];
        session
            .ensure_tiles_for_points(&[(road_lat, road_lon), (paved_lat, paved_lon)], 900.0)
            .context("preload DEM for jeep profile")?;
        let elev = SessionElev(session.as_ref());
        let coords = [(paved_lat, paved_lon), (road_lat, road_lon)];
        let detail =
            profile_along_polyline(&elev, &coords, 30.0, &[]).context("jeep profile")?;
        serde_json::to_value(detail).context("serialize jeep profile")?
    };

    Ok(json!({
        "slug": peak_slug,
        "name": entry.name,
        "road_lat": road_lat,
        "road_lon": road_lon,
        "paved_lat": paved_loc.map(|loc| loc[0]),
        "paved_lon": paved_loc.map(|loc| loc[1]),
        "jeep_m": access.as_ref().and_then(|a| a.jeep_m).or(entry.jeep_m),
        "jeep": jeep,
    }))
}
