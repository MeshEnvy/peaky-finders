//! DEM road-to-summit hike profile (3D length + max slope).

pub const DEFAULT_MAX_HIKE_M: f64 = 805.0;
/// Search disc for snapping a seed point to the highest nearby DEM cell.
pub const DEFAULT_SUMMIT_SNAP_M: f64 = 500.0;

/// Clamp Razorback-calibrated slope ceiling to a sane range.
pub fn clamp_calibrated_slope_deg(deg: f64) -> f64 {
    deg.max(15.0).min(60.0)
}

pub trait HikeSampleElev {
    fn sample_elev_m(&self, lat: f64, lon: f64) -> f64;
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct HikeProfile {
    pub hike_m_3d: f64,
    pub max_slope_deg: f64,
    pub n_samples: usize,
}

/// Destination point given start, bearing (deg), and distance (m).
pub fn destination_point(lat: f64, lon: f64, bearing_deg: f64, dist_m: f64) -> (f64, f64) {
    let r = 6_371_000.0;
    let brng = bearing_deg.to_radians();
    let lat1 = lat.to_radians();
    let lon1 = lon.to_radians();
    let ang = dist_m / r;
    let lat2 = (lat1.sin() * ang.cos() + lat1.cos() * ang.sin() * brng.cos()).asin();
    let lon2 = lon1
        + (ang.sin() * brng.sin())
            .atan2(lat1.cos() * ang.cos() - lat1.sin() * ang.sin() * brng.cos());
    (lat2.to_degrees(), lon2.to_degrees())
}

/// Highest DEM sample within ``radius_m`` of the seed (no ridge-walk beyond the disc).
pub fn snap_to_local_summit<E: HikeSampleElev>(
    elev: &E,
    seed_lat: f64,
    seed_lon: f64,
    radius_m: f64,
    step_m: f64,
) -> Option<(f64, f64, f64)> {
    let seed_elev = elev.sample_elev_m(seed_lat, seed_lon);
    if !(seed_elev.is_finite() && seed_elev > 0.0) {
        return None;
    }
    let step = step_m.max(15.0);
    let max_ring = ((radius_m / step).ceil() as i32).max(0);
    let mut best = (seed_lat, seed_lon, seed_elev);

    for ring in 0..=max_ring {
        let dist = ring as f64 * step;
        if dist > radius_m + 1.0 {
            break;
        }
        if ring == 0 {
            continue;
        }
        let n_dirs = (8 * ring).max(8);
        for i in 0..n_dirs {
            let bearing = 360.0 * (i as f64) / (n_dirs as f64);
            let (lat, lon) = destination_point(seed_lat, seed_lon, bearing, dist);
            if haversine_m(seed_lat, seed_lon, lat, lon) > radius_m + 1.0 {
                continue;
            }
            let e = elev.sample_elev_m(lat, lon);
            if e.is_finite() && e > best.2 {
                best = (lat, lon, e);
            }
        }
    }
    Some(best)
}

pub fn haversine_m(lat1: f64, lon1: f64, lat2: f64, lon2: f64) -> f64 {
    let r = 6_371_000.0;
    let dlat = (lat2 - lat1).to_radians();
    let dlon = (lon2 - lon1).to_radians();
    let a = (dlat / 2.0).sin().powi(2)
        + lat1.to_radians().cos() * lat2.to_radians().cos() * (dlon / 2.0).sin().powi(2);
    let c = 2.0 * a.sqrt().atan2((1.0 - a).sqrt());
    r * c
}

/// Straight-line geodesic profile from road to summit at ~30 m steps.
pub fn profile_hike<E: HikeSampleElev>(
    elev: &E,
    road_lat: f64,
    road_lon: f64,
    peak_lat: f64,
    peak_lon: f64,
    step_m: f64,
) -> Option<HikeProfile> {
    let total_h = haversine_m(road_lat, road_lon, peak_lat, peak_lon);
    if total_h <= 1.0 {
        return Some(HikeProfile {
            hike_m_3d: 0.0,
            max_slope_deg: 0.0,
            n_samples: 2,
        });
    }
    let step = step_m.max(10.0);
    let n_steps = ((total_h / step).ceil() as usize).max(1);
    let mut prev_lat = road_lat;
    let mut prev_lon = road_lon;
    let mut prev_elev = elev.sample_elev_m(road_lat, road_lon);
    let mut hike_3d = 0.0;
    let mut max_slope = 0.0f64;

    for i in 1..=n_steps {
        let t = (i as f64) / (n_steps as f64);
        let lat = road_lat + t * (peak_lat - road_lat);
        let lon = road_lon + t * (peak_lon - road_lon);
        let e = elev.sample_elev_m(lat, lon);
        let horiz = haversine_m(prev_lat, prev_lon, lat, lon);
        if horiz > 0.5 {
            let vert = e - prev_elev;
            let slope = vert.atan2(horiz).to_degrees().abs();
            if slope.is_finite() {
                max_slope = max_slope.max(slope);
            }
            hike_3d += (horiz * horiz + vert * vert).sqrt();
        }
        prev_lat = lat;
        prev_lon = lon;
        prev_elev = e;
    }

    Some(HikeProfile {
        hike_m_3d: hike_3d,
        max_slope_deg: max_slope,
        n_samples: n_steps + 1,
    })
}

pub fn profile_passes(
    profile: &HikeProfile,
    max_hike_m: f64,
    max_slope_deg: f64,
) -> bool {
    profile.hike_m_3d <= max_hike_m + 1.0 && profile.max_slope_deg <= max_slope_deg + 0.5
}

#[cfg(test)]
mod tests {
    use super::*;

    struct FlatElev(f64);

    impl HikeSampleElev for FlatElev {
        fn sample_elev_m(&self, _lat: f64, _lon: f64) -> f64 {
            self.0
        }
    }

    struct SteepRamp {
        base_lat: f64,
    }

    impl HikeSampleElev for SteepRamp {
        fn sample_elev_m(&self, lat: f64, _lon: f64) -> f64 {
            // ~450 m rise over ~1.1 km horizontal (~22 deg).
            (lat - self.base_lat) * 45_000.0
        }
    }

    #[test]
    fn flat_hike_is_horizontal_distance() {
        let flat = FlatElev(2000.0);
        let p = profile_hike(&flat, 38.0, -117.0, 38.004, -117.0, 30.0).unwrap();
        let horiz = haversine_m(38.0, -117.0, 38.004, -117.0);
        assert!((p.hike_m_3d - horiz).abs() < 5.0);
        assert!(p.max_slope_deg < 1.0);
    }

    #[test]
    fn steep_ramp_fails_tight_slope_cap() {
        let ramp = SteepRamp { base_lat: 38.0 };
        let p = profile_hike(&ramp, 38.0, -117.0, 38.01, -117.0, 30.0).unwrap();
        assert!(p.max_slope_deg > 20.0);
        assert!(!profile_passes(&p, DEFAULT_MAX_HIKE_M, 5.0));
    }

    #[test]
    fn calibrated_ceiling_accepts_profile_at_or_below() {
        let ramp = SteepRamp { base_lat: 38.0 };
        let p = profile_hike(&ramp, 38.0, -117.0, 38.003, -117.0, 30.0).unwrap();
        assert!(p.hike_m_3d <= DEFAULT_MAX_HIKE_M);
        let ceiling = p.max_slope_deg;
        assert!(profile_passes(&p, DEFAULT_MAX_HIKE_M, ceiling));
        assert!(!profile_passes(&p, DEFAULT_MAX_HIKE_M, ceiling - 2.0));
    }

    #[test]
    fn clamp_calibrated_slope_bounds() {
        assert_eq!(clamp_calibrated_slope_deg(5.0), 15.0);
        assert_eq!(clamp_calibrated_slope_deg(28.0), 28.0);
        assert_eq!(clamp_calibrated_slope_deg(90.0), 60.0);
    }

    struct OffsetBump {
        peak_lat: f64,
        peak_lon: f64,
        peak_elev: f64,
        base: f64,
    }

    impl HikeSampleElev for OffsetBump {
        fn sample_elev_m(&self, lat: f64, lon: f64) -> f64 {
            if haversine_m(lat, lon, self.peak_lat, self.peak_lon) <= 45.0 {
                self.peak_elev
            } else {
                self.base
            }
        }
    }

    #[test]
    fn snap_moves_seed_to_highest_point_in_disc() {
        let bump = OffsetBump {
            peak_lat: 38.0,
            peak_lon: -117.0,
            peak_elev: 2200.0,
            base: 2000.0,
        };
        let seed_lat = 38.0 + 0.0018;
        let seed_lon = -117.0;
        let (lat, lon, elev) =
            snap_to_local_summit(&bump, seed_lat, seed_lon, DEFAULT_SUMMIT_SNAP_M, 30.0).unwrap();
        assert!((elev - 2200.0).abs() < 1.0);
        assert!(haversine_m(lat, lon, bump.peak_lat, bump.peak_lon) < 60.0);
        assert!(haversine_m(seed_lat, seed_lon, lat, lon) > 100.0);
    }

    #[test]
    fn snap_does_not_chain_beyond_radius() {
        let bump = OffsetBump {
            peak_lat: 38.01,
            peak_lon: -117.0,
            peak_elev: 2400.0,
            base: 2000.0,
        };
        let (lat, _, elev) =
            snap_to_local_summit(&bump, 38.0, -117.0, DEFAULT_SUMMIT_SNAP_M, 30.0).unwrap();
        assert!(elev < 2300.0);
        assert!(haversine_m(38.0, -117.0, lat, -117.0) <= DEFAULT_SUMMIT_SNAP_M + 30.0);
    }

    #[test]
    fn long_hike_fails_half_mile_cap() {
        let flat = FlatElev(2000.0);
        let p = profile_hike(&flat, 38.0, -117.0, 38.05, -117.0, 30.0).unwrap();
        assert!(p.hike_m_3d > DEFAULT_MAX_HIKE_M);
        assert!(!profile_passes(&p, DEFAULT_MAX_HIKE_M, 90.0));
    }
}
