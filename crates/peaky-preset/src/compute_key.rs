//! Compute fingerprints for peak rows and access profiles.
//!
//! Settings + ``algo_version`` live in ``access/_meta.yaml`` (and eligibility in
//! ``peaks/_meta.yaml``). Bump ``ACCESS_ALGO_VERSION`` / ``PEAK_ALGO_VERSION`` in
//! ``model`` when algorithms change for the same numeric settings.

use std::collections::hash_map::DefaultHasher;
use std::hash::{Hash, Hasher};

use crate::model::{AccessMeta, PeakAccessRules, PlaceAccess};

pub use crate::model::{
    ACCESS_ALGO_VERSION, PEAK_ALGO_VERSION,
    DEFAULT_ACCESS_PROFILE_SAMPLE_M as ACCESS_PROFILE_SAMPLE_M,
    DEFAULT_OSM_ROAD_SAMPLE_STEP_M as OSM_ROAD_SAMPLE_STEP_M,
    DEFAULT_PLACE_ROAD_SEARCH_M as PLACE_ACCESS_ROAD_SEARCH_M,
};

fn round_m(v: f64) -> i64 {
    (v * 1000.0).round() as i64
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

/// Fingerprint for paved→park→pad path compute (stored on ``access/<slug>.yaml``).
pub fn access_compute_key(
    algo_version: u32,
    mode: &str,
    road_search_m: f64,
    max_jeep_m: f64,
    profile_sample_m: f64,
    road_sample_step_m: f64,
    hike_path_max_m: f64,
    jeep_highways: &[String],
    paved_highways: &[String],
) -> String {
    hash_parts(&[
        algo_version as u64,
        hash_str(mode),
        round_m(road_search_m) as u64,
        round_m(max_jeep_m) as u64,
        round_m(profile_sample_m) as u64,
        round_m(road_sample_step_m) as u64,
        round_m(hike_path_max_m) as u64,
        hash_str(&sorted_joined(jeep_highways)),
        hash_str(&sorted_joined(paved_highways)),
    ])
}

/// Key used by site/place warm (display routing, ungated).
pub fn place_access_compute_key(meta: &AccessMeta) -> String {
    access_compute_key(
        meta.algo_version,
        "place",
        meta.place_road_search_m,
        meta.max_jeep_m,
        meta.profile_sample_m,
        meta.road_sample_step_m,
        meta.hike_path_max_m,
        &meta.road_highways,
        &meta.paved_highways,
    )
}

/// Key used when ``peaky peaks`` writes access for an eligible peak.
///
/// ``road_search_m`` is the peak park-search radius (usually ``rules.max_hike_m``).
pub fn peak_access_compute_key(meta: &AccessMeta, road_search_m: f64) -> String {
    access_compute_key(
        meta.algo_version,
        "peak",
        road_search_m,
        meta.max_jeep_m,
        meta.profile_sample_m,
        meta.road_sample_step_m,
        meta.hike_path_max_m,
        &meta.road_highways,
        &meta.paved_highways,
    )
}

/// Fingerprint for a peak catalog row (eligibility + snap + land + access algo).
pub fn peak_row_compute_key(
    rules: &PeakAccessRules,
    access: &AccessMeta,
    land_digest: &str,
    summit_snap_m: f64,
) -> String {
    hash_parts(&[
        PEAK_ALGO_VERSION as u64,
        hash_str(land_digest),
        round_m(summit_snap_m) as u64,
        round_m(rules.max_hike_m) as u64,
        round_m(rules.max_slope_grade_pct) as u64,
        round_m(rules.max_slope_deg) as u64,
        hash_str(&peak_access_compute_key(access, rules.max_hike_m)),
    ])
}

/// Full hike+jeep profiles present (scalars alone are incomplete / stale).
pub fn place_access_has_profiles(access: &PlaceAccess) -> bool {
    access.hike.is_some() && access.jeep.is_some()
}

/// True when profiles exist and ``compute_key`` matches one of ``expected``.
pub fn place_access_is_fresh(access: &PlaceAccess, expected: &[&str]) -> bool {
    if !place_access_has_profiles(access) {
        return false;
    }
    let Some(key) = access.compute_key.as_deref() else {
        return false;
    };
    expected.iter().any(|e| *e == key)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn place_and_peak_keys_differ() {
        let meta = AccessMeta::default();
        let rules = PeakAccessRules::default();
        assert_ne!(
            place_access_compute_key(&meta),
            peak_access_compute_key(&meta, rules.max_hike_m)
        );
    }

    #[test]
    fn settings_change_moves_key() {
        let a = AccessMeta::default();
        let mut b = a.clone();
        b.place_road_search_m = 25_000.0;
        assert_ne!(place_access_compute_key(&a), place_access_compute_key(&b));
    }

    #[test]
    fn hike_path_max_moves_key() {
        let a = AccessMeta::default();
        let mut b = a.clone();
        b.hike_path_max_m = 2000.0;
        assert_ne!(place_access_compute_key(&a), place_access_compute_key(&b));
    }

    #[test]
    fn peak_road_search_moves_key() {
        let meta = AccessMeta::default();
        assert_ne!(
            peak_access_compute_key(&meta, 805.0),
            peak_access_compute_key(&meta, 900.0)
        );
    }

    #[test]
    fn land_digest_moves_peak_row_key() {
        let rules = PeakAccessRules::default();
        let meta = AccessMeta::default();
        assert_ne!(
            peak_row_compute_key(&rules, &meta, "land-a", 500.0),
            peak_row_compute_key(&rules, &meta, "land-b", 500.0)
        );
    }

    #[test]
    fn algo_version_in_meta_moves_key() {
        let a = AccessMeta::default();
        let mut b = a.clone();
        b.algo_version = a.algo_version + 1;
        assert_ne!(place_access_compute_key(&a), place_access_compute_key(&b));
    }

    #[test]
    fn missing_key_is_stale() {
        let access = PlaceAccess {
            hike: Some(crate::model::PeakHikeProfile {
                hike_m_3d: 1.0,
                horiz_m: 1.0,
                gain_m: 0.0,
                loss_m: 0.0,
                max_slope_deg: 0.0,
                max_grade_pct: 0.0,
                avg_grade_pct: 0.0,
                difficulty: "easy".into(),
                profile: vec![],
                histogram: vec![],
            }),
            jeep: Some(crate::model::PeakJeepProfile {
                jeep_m_3d: 1.0,
                horiz_m: 1.0,
                gain_m: 0.0,
                loss_m: 0.0,
                max_slope_deg: 0.0,
                max_grade_pct: 0.0,
                avg_grade_pct: 0.0,
                difficulty: "easy".into(),
                profile: vec![],
                histogram: vec![],
                segments: vec![],
            }),
            compute_key: None,
            ..Default::default()
        };
        let key = place_access_compute_key(&AccessMeta::default());
        assert!(!place_access_is_fresh(&access, &[&key]));
    }

    #[test]
    fn default_algo_matches_const() {
        assert_eq!(AccessMeta::default().algo_version, ACCESS_ALGO_VERSION);
    }
}
