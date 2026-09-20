//! Compute fingerprints for peak rows and access profiles.
//!
//! Probe cache prefs keys fingerprint pathfinding settings from config
//! (``Preset::resolved_access_meta()``) plus scan bbox and road-source fingerprints.
//! Bump ``ACCESS_ALGO_VERSION`` / ``PEAK_ALGO_VERSION`` in ``model`` when algorithms change.

use std::collections::hash_map::DefaultHasher;
use std::hash::{Hash, Hasher};

use crate::model::{
    AccessMeta, AccessPreferenceConfig, PeaksDifficultyConfig, PeaksScanConfig,
};

pub use crate::model::{
    ACCESS_ALGO_VERSION, PEAK_ALGO_VERSION,
    DEFAULT_ACCESS_PROFILE_SAMPLE_M as ACCESS_PROFILE_SAMPLE_M,
    DEFAULT_OSM_ROAD_SAMPLE_STEP_M as OSM_ROAD_SAMPLE_STEP_M,
    DEFAULT_PLACE_ROAD_SEARCH_M as PLACE_ACCESS_ROAD_SEARCH_M,
};

fn round_m(v: f64) -> i64 {
    (v * 1000.0).round() as i64
}

fn round_coord(v: f64) -> i64 {
    (v * 1_000_000.0).round() as i64
}

fn hash_parts(parts: &[u64]) -> String {
    let mut hasher = DefaultHasher::new();
    for p in parts {
        p.hash(&mut hasher);
    }
    format!("{:016x}", hasher.finish())
}

fn hash_str(s: &str) -> u64 {
    let mut hasher = DefaultHasher::new();
    s.hash(&mut hasher);
    hasher.finish()
}

fn sorted_joined(items: &[String]) -> String {
    let mut v: Vec<&str> = items.iter().map(String::as_str).collect();
    v.sort_unstable();
    v.join(",")
}

fn sorted_class_m(map: &std::collections::HashMap<String, f64>) -> String {
    let mut keys: Vec<&String> = map.keys().collect();
    keys.sort_unstable();
    keys.iter()
        .map(|k| format!("{k}={:.0}", map[*k]))
        .collect::<Vec<_>>()
        .join(";")
}

fn difficulty_fingerprint(difficulty: &PeaksDifficultyConfig) -> u64 {
    let hike = &difficulty.hike;
    let jeep = &difficulty.jeep;
    let parts = format!(
        "h:{:.0},{:.0},{:.0},{:.0},{:.0},{:.0},{:.0},{:.0}|j:{},{},{},{},{:.0}|c:{}",
        hike.medium.min_max_grade_pct,
        hike.medium.min_avg_grade_pct,
        hike.medium.min_gain_m,
        hike.difficult.min_gain_m,
        hike.difficult.min_avg_grade_pct,
        hike.extreme.min_gain_m,
        hike.extreme.min_avg_grade_pct,
        hike.extreme.max_segment_grade_pct,
        sorted_joined(&jeep.medium.highways),
        sorted_joined(&jeep.medium.tracktypes),
        sorted_joined(&jeep.difficult.tracktypes),
        sorted_joined(&jeep.extreme.tracktypes),
        jeep.extreme.max_route_m,
        sorted_class_m(&jeep.extreme.max_class_m),
    );
    hash_str(&parts)
}

fn preference_fingerprint(preference: &AccessPreferenceConfig) -> u64 {
    let parts = format!(
        "{:.6},{:.6},{:.6},{:.6}",
        preference.w_hike, preference.w_trail, preference.w_jeep, preference.w_difficulty
    );
    hash_str(&parts)
}

/// Settings fingerprint shared by all probes with the same routing prefs.
pub fn probe_prefs_key(
    meta: &AccessMeta,
    scan: &PeaksScanConfig,
    gtlf_fp: &str,
    pbf_fp: &str,
) -> String {
    hash_parts(&[
        meta.algo_version as u64,
        round_m(meta.profile_sample_m) as u64,
        round_m(meta.road_sample_step_m) as u64,
        round_m(meta.max_jeep_m) as u64,
        round_m(meta.place_road_search_m) as u64,
        round_m(meta.hike_path_max_m) as u64,
        hash_str(&sorted_joined(&meta.road_highways)),
        hash_str(&sorted_joined(&meta.paved_highways)),
        round_m(scan.max_road_search_m) as u64,
        round_m(scan.hike_path_max_m) as u64,
        difficulty_fingerprint(&scan.difficulty),
        preference_fingerprint(&scan.preference),
        hash_str(gtlf_fp),
        hash_str(pbf_fp),
    ])
}

/// Point-specific probe fingerprint (lat/lon + prefs stamp).
pub fn probe_point_key(lat: f64, lon: f64, prefs_key: &str) -> String {
    hash_parts(&[
        hash_str(prefs_key),
        round_coord(lat) as u64,
        round_coord(lon) as u64,
    ])
}

/// Full access probe fingerprint for a coordinate.
pub fn probe_key(
    lat: f64,
    lon: f64,
    meta: &AccessMeta,
    scan: &PeaksScanConfig,
    gtlf_fp: &str,
    pbf_fp: &str,
) -> String {
    probe_point_key(lat, lon, &probe_prefs_key(meta, scan, gtlf_fp, pbf_fp))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn prefs_and_point_keys_differ() {
        let meta = AccessMeta::default();
        let scan = PeaksScanConfig::default();
        let prefs = probe_prefs_key(&meta, &scan, "disabled", "");
        assert_ne!(prefs, probe_point_key(38.0, -117.0, &prefs));
    }

    #[test]
    fn probe_key_matches_point_key() {
        let meta = AccessMeta::default();
        let scan = PeaksScanConfig::default();
        let prefs = probe_prefs_key(&meta, &scan, "disabled", "");
        assert_eq!(
            probe_key(38.0, -117.0, &meta, &scan, "disabled", ""),
            probe_point_key(38.0, -117.0, &prefs)
        );
    }

    #[test]
    fn settings_change_moves_prefs_key() {
        let a = AccessMeta::default();
        let mut b = a.clone();
        b.place_road_search_m = 25_000.0;
        let scan = PeaksScanConfig::default();
        assert_ne!(
            probe_prefs_key(&a, &scan, "disabled", ""),
            probe_prefs_key(&b, &scan, "disabled", "")
        );
    }

    #[test]
    fn preference_change_moves_prefs_key() {
        let meta = AccessMeta::default();
        let a = PeaksScanConfig::default();
        let mut b = a.clone();
        b.preference.w_hike = 2.0;
        assert_ne!(
            probe_prefs_key(&meta, &a, "disabled", ""),
            probe_prefs_key(&meta, &b, "disabled", "")
        );
    }

    #[test]
    fn hike_path_max_moves_prefs_key() {
        let a = AccessMeta::default();
        let mut b = a.clone();
        b.hike_path_max_m = 2000.0;
        let scan = PeaksScanConfig::default();
        assert_ne!(
            probe_prefs_key(&a, &scan, "disabled", ""),
            probe_prefs_key(&b, &scan, "disabled", "")
        );
    }

    #[test]
    fn peak_road_search_moves_prefs_key() {
        let meta = AccessMeta::default();
        let mut a = PeaksScanConfig::default();
        a.max_road_search_m = 805.0;
        let mut b = a.clone();
        b.max_road_search_m = 900.0;
        assert_ne!(
            probe_prefs_key(&meta, &a, "disabled", ""),
            probe_prefs_key(&meta, &b, "disabled", "")
        );
    }

    #[test]
    fn gtlf_fingerprint_moves_prefs_key() {
        let meta = AccessMeta::default();
        let scan = PeaksScanConfig::default();
        assert_ne!(
            probe_prefs_key(&meta, &scan, "disabled", ""),
            probe_prefs_key(&meta, &scan, "abc123", "")
        );
    }

    #[test]
    fn pbf_fingerprint_moves_prefs_key() {
        let meta = AccessMeta::default();
        let scan = PeaksScanConfig::default();
        assert_ne!(
            probe_prefs_key(&meta, &scan, "disabled", ""),
            probe_prefs_key(&meta, &scan, "disabled", "pbf-v2")
        );
    }

    #[test]
    fn lat_lon_moves_probe_key() {
        let meta = AccessMeta::default();
        let scan = PeaksScanConfig::default();
        assert_ne!(
            probe_key(38.0, -117.0, &meta, &scan, "disabled", ""),
            probe_key(38.1, -117.0, &meta, &scan, "disabled", "")
        );
    }

    #[test]
    fn algo_version_in_meta_moves_prefs_key() {
        let a = AccessMeta::default();
        let mut b = a.clone();
        b.algo_version = a.algo_version + 1;
        let scan = PeaksScanConfig::default();
        assert_ne!(
            probe_prefs_key(&a, &scan, "disabled", ""),
            probe_prefs_key(&b, &scan, "disabled", "")
        );
    }

    #[test]
    fn default_algo_matches_const() {
        assert_eq!(AccessMeta::default().algo_version, ACCESS_ALGO_VERSION);
    }
}
