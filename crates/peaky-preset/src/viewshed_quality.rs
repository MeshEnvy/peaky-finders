//! Viewshed raster resolution: DEM-native spacing (~30 m Skadi).

use crate::Preset;

pub const SKADI_DEM_SPACING_M: f64 = 30.0;
pub const VIEWSHED_RASTER_MIN: u32 = 128;
pub const VIEWSHED_RASTER_MAX: u32 = 4096;

pub fn preset_radius_km(preset: &Preset) -> f64 {
    match &preset.simulation.radius_km {
        serde_yaml::Value::Number(n) => n.as_f64().unwrap_or(50.0),
        serde_yaml::Value::String(s) => s.parse().unwrap_or(50.0),
        _ => 50.0,
    }
}

pub fn preset_radius_m(preset: &Preset) -> f64 {
    preset_radius_km(preset) * 1000.0
}

pub fn dem_native_raster_dimension(radius_m: f64) -> u32 {
    let px = (radius_m / SKADI_DEM_SPACING_M).ceil() as u32;
    px.clamp(VIEWSHED_RASTER_MIN, VIEWSHED_RASTER_MAX)
}

pub fn effective_radius_m(preset: &Preset, radius_km_override: Option<f64>) -> f64 {
    radius_km_override
        .unwrap_or_else(|| preset_radius_km(preset))
        * 1000.0
}

pub fn effective_target_raster_dimension(
    preset: &Preset,
    radius_km_override: Option<f64>,
) -> u32 {
    let radius_m = effective_radius_m(preset, radius_km_override);
    dem_native_raster_dimension(radius_m)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dem_native_at_50km() {
        assert_eq!(dem_native_raster_dimension(50_000.0), 1667);
    }

    #[test]
    fn dem_native_at_72km() {
        assert_eq!(dem_native_raster_dimension(72_000.0), 2400);
    }

    #[test]
    fn effective_target_from_preset_default() {
        let preset = crate::Preset::default();
        assert_eq!(effective_target_raster_dimension(&preset, None), 1667);
    }
}
