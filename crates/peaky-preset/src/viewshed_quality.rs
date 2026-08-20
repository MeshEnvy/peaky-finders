//! Viewshed raster resolution from quality (1–5) and hop radius.

use crate::{Preset, PresetResult};

pub const SKADI_DEM_SPACING_M: f64 = 30.0;
pub const VIEWSHED_QUALITY_MIN: u8 = 1;
pub const VIEWSHED_QUALITY_MAX: u8 = 5;
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

/// Exponential pixel ladder from ``min_px`` to ``target_px`` (double each step, last step lands on target).
pub fn raster_upgrade_ladder(min_px: u32, target_px: u32) -> Vec<u32> {
    let min_px = min_px.clamp(VIEWSHED_RASTER_MIN, VIEWSHED_RASTER_MAX);
    let target_px = target_px.clamp(min_px, VIEWSHED_RASTER_MAX);
    let mut ladder = vec![min_px];
    let mut cur = min_px;
    while cur < target_px {
        let next = (cur.saturating_mul(2)).min(target_px);
        if next <= cur {
            break;
        }
        ladder.push(next);
        cur = next;
    }
    ladder
}

pub fn viewshed_raster_for_quality(quality: u8, radius_m: f64) -> u32 {
    let q = quality.clamp(VIEWSHED_QUALITY_MIN, VIEWSHED_QUALITY_MAX);
    if q == VIEWSHED_QUALITY_MIN {
        return VIEWSHED_RASTER_MIN;
    }
    let full = dem_native_raster_dimension(radius_m);
    if q == VIEWSHED_QUALITY_MAX || full <= VIEWSHED_RASTER_MIN {
        return full;
    }
    let ladder = raster_upgrade_ladder(VIEWSHED_RASTER_MIN, full);
    let idx = ((q - 1) as f64 / (VIEWSHED_QUALITY_MAX - 1) as f64 * (ladder.len() - 1) as f64).round() as usize;
    ladder[idx.min(ladder.len() - 1)]
}

pub fn effective_viewshed_quality(
    preset: &Preset,
    quality_override: Option<u8>,
) -> u8 {
    if let Some(q) = quality_override {
        return q.clamp(VIEWSHED_QUALITY_MIN, VIEWSHED_QUALITY_MAX);
    }
    preset.simulation.viewshed_quality.clamp(VIEWSHED_QUALITY_MIN, VIEWSHED_QUALITY_MAX)
}

pub fn effective_radius_m(preset: &Preset, radius_km_override: Option<f64>) -> f64 {
    radius_km_override
        .unwrap_or_else(|| preset_radius_km(preset))
        * 1000.0
}

pub fn effective_target_raster_dimension(
    preset: &Preset,
    radius_km_override: Option<f64>,
    quality_override: Option<u8>,
) -> u32 {
    let radius_m = effective_radius_m(preset, radius_km_override);
    let quality = effective_viewshed_quality(preset, quality_override);
    viewshed_raster_for_quality(quality, radius_m)
}

pub fn validate_viewshed_quality(quality: u8) -> PresetResult<()> {
    if !(VIEWSHED_QUALITY_MIN..=VIEWSHED_QUALITY_MAX).contains(&quality) {
        return Err(crate::PresetValidationError::Message(format!(
            "simulation.viewshed_quality must be between {} and {}",
            VIEWSHED_QUALITY_MIN, VIEWSHED_QUALITY_MAX
        )));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn quality_one_always_128() {
        for km in [1.0, 50.0, 100.0] {
            assert_eq!(
                viewshed_raster_for_quality(1, km * 1000.0),
                128,
                "radius {km} km"
            );
        }
    }

    #[test]
    fn quality_five_is_dem_native() {
        assert_eq!(viewshed_raster_for_quality(5, 50_000.0), 1667);
        assert_eq!(viewshed_raster_for_quality(5, 10_000.0), 334);
        assert_eq!(viewshed_raster_for_quality(5, 100_000.0), 3334);
    }

    #[test]
    fn quality_monotonic_at_50km() {
        let radius_m = 50_000.0;
        let mut prev = 0u32;
        for q in 1..=5 {
            let px = viewshed_raster_for_quality(q, radius_m);
            assert!(px >= prev, "q={q} px={px} prev={prev}");
            prev = px;
        }
    }

    #[test]
    fn quality_ladder_at_50km() {
        assert_eq!(viewshed_raster_for_quality(2, 50_000.0), 256);
        assert_eq!(viewshed_raster_for_quality(3, 50_000.0), 512);
        assert_eq!(viewshed_raster_for_quality(4, 50_000.0), 1024);
    }

    #[test]
    fn raster_upgrade_ladder_doubles_to_target() {
        assert_eq!(raster_upgrade_ladder(128, 500), vec![128, 256, 500]);
        assert_eq!(
            raster_upgrade_ladder(128, 4096),
            vec![128, 256, 512, 1024, 2048, 4096]
        );
    }

    #[test]
    fn effective_target_from_preset_default() {
        let preset = crate::Preset::default();
        assert_eq!(effective_target_raster_dimension(&preset, None, None), 512);
    }
}
