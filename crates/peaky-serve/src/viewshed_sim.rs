//! Ephemeral viewshed simulation overrides from HTTP query params.

use std::collections::HashMap;

use axum::http::StatusCode;
use peaky_preset::{
    effective_target_raster_dimension, effective_viewshed_quality, Preset,
    VIEWSHED_QUALITY_MAX, VIEWSHED_QUALITY_MIN, VIEWSHED_RASTER_MAX, VIEWSHED_RASTER_MIN,
};

pub use peaky_preset::raster_upgrade_ladder;

pub const MIN_SERVE_RADIUS_KM: f64 = 1.0;
pub const MAX_SERVE_RADIUS_KM: f64 = 100.0;
pub const MIN_SERVE_RASTER_DIMENSION: u32 = VIEWSHED_RASTER_MIN;
pub const MAX_SERVE_RASTER_DIMENSION: u32 = VIEWSHED_RASTER_MAX;

#[derive(Debug, Clone, Default)]
pub struct ViewshedSimOverrides {
    pub radius_km: Option<f64>,
    pub quality: Option<u8>,
    /// Explicit pixel target (progressive ladder steps only; not exposed via HTTP).
    pub raster_dimension: Option<u32>,
}

impl ViewshedSimOverrides {
    pub fn active(&self) -> bool {
        self.radius_km.is_some() || self.quality.is_some()
    }
}

pub fn parse_viewshed_sim_params(params: &HashMap<String, String>) -> Result<ViewshedSimOverrides, String> {
    let radius_km = match params.get("radius_km").map(String::as_str) {
        None | Some("") => None,
        Some(raw) => {
            let v: f64 = raw
                .parse()
                .map_err(|_| "radius_km must be a number".to_string())?;
            if !(MIN_SERVE_RADIUS_KM..=MAX_SERVE_RADIUS_KM).contains(&v) {
                return Err(format!(
                    "radius_km must be between {MIN_SERVE_RADIUS_KM} and {MAX_SERVE_RADIUS_KM}"
                ));
            }
            Some(v)
        }
    };
    let quality = match params.get("quality").map(String::as_str) {
        None | Some("") => None,
        Some(raw) => {
            let v: u8 = raw
                .parse::<f64>()
                .map_err(|_| "quality must be an integer".to_string())? as u8;
            if !(VIEWSHED_QUALITY_MIN..=VIEWSHED_QUALITY_MAX).contains(&v) {
                return Err(format!(
                    "quality must be between {VIEWSHED_QUALITY_MIN} and {VIEWSHED_QUALITY_MAX}"
                ));
            }
            Some(v)
        }
    };
    Ok(ViewshedSimOverrides {
        radius_km,
        quality,
        ..Default::default()
    })
}

pub fn parse_lat_lon_params(params: &HashMap<String, String>) -> Result<(f64, f64), String> {
    let lat: f64 = params
        .get("lat")
        .ok_or_else(|| "lat required".to_string())?
        .parse()
        .map_err(|_| "lat must be a number".to_string())?;
    let lon: f64 = params
        .get("lon")
        .ok_or(String::from("lon required"))?
        .parse()
        .map_err(|_| "lon must be a number".to_string())?;
    if !(-90.0..=90.0).contains(&lat) {
        return Err(format!("lat out of bounds: {lat}"));
    }
    if !(-180.0..=180.0).contains(&lon) {
        return Err(format!("lon out of bounds: {lon}"));
    }
    Ok((lat, lon))
}

pub fn sim_status_from_err(msg: String) -> StatusCode {
    if msg.contains("out of bounds") || msg.contains("must be") {
        StatusCode::UNPROCESSABLE_ENTITY
    } else {
        StatusCode::BAD_REQUEST
    }
}

pub fn effective_target_raster_for_preset(preset: &Preset, sim: Option<&ViewshedSimOverrides>) -> u32 {
    if let Some(sim) = sim {
        if let Some(px) = sim.raster_dimension {
            return px.clamp(MIN_SERVE_RASTER_DIMENSION, MAX_SERVE_RASTER_DIMENSION);
        }
    }
    let radius_km = sim.and_then(|s| s.radius_km);
    let quality = sim.and_then(|s| s.quality);
    effective_target_raster_dimension(preset, radius_km, quality)
}

pub fn effective_viewshed_quality_for_preset(preset: &Preset, sim: Option<&ViewshedSimOverrides>) -> u8 {
    effective_viewshed_quality(preset, sim.and_then(|s| s.quality))
}

#[cfg(test)]
mod tests {
    use super::*;
    use peaky_preset::Preset;

    #[test]
    fn parse_quality_param() {
        let mut params = HashMap::new();
        params.insert("quality".into(), "3".into());
        let sim = parse_viewshed_sim_params(&params).unwrap();
        assert_eq!(sim.quality, Some(3));
    }

    #[test]
    fn effective_target_respects_quality_override() {
        let preset = Preset::default();
        let sim = ViewshedSimOverrides {
            quality: Some(1),
            ..Default::default()
        };
        assert_eq!(effective_target_raster_for_preset(&preset, Some(&sim)), 128);
    }
}
