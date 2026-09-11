//! DEM road-to-summit hike profile (3D length + max slope).

use serde::Serialize;

pub const DEFAULT_MAX_HIKE_M: f64 = 805.0;
/// Search disc for snapping a seed point to the highest nearby DEM cell.
pub const DEFAULT_SUMMIT_SNAP_M: f64 = 500.0;
/// Max segment grade on road-to-summit profile (~slipping-gravel steepness).
pub const DEFAULT_MAX_SLOPE_GRADE_PCT: f64 = 30.0;

/// Percent grade (rise/run × 100) to slope angle in degrees.
pub fn grade_pct_to_deg(pct: f64) -> f64 {
    (pct / 100.0).atan().to_degrees()
}

/// Fixed max segment slope for eligible peaks (30% grade).
pub fn default_max_slope_deg() -> f64 {
    grade_pct_to_deg(DEFAULT_MAX_SLOPE_GRADE_PCT)
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

#[derive(Debug, Clone, Serialize)]
pub struct HikeProfilePoint {
    pub dist_m: f64,
    pub elev_m: f64,
    pub lat: f64,
    pub lon: f64,
}

#[derive(Debug, Clone, Serialize)]
pub struct HikeSegment {
    pub dist_m: f64,
    pub horiz_m: f64,
    pub grade_pct: f64,
    pub slope_deg: f64,
}

#[derive(Debug, Clone, Serialize)]
pub struct GradeHistogramBucket {
    pub label: String,
    pub min_grade_pct: f64,
    pub max_grade_pct: f64,
    pub dist_m: f64,
    pub pct_of_route: f64,
}

#[derive(Debug, Clone, Serialize)]
pub struct HikeProfileDetailed {
    pub hike_m_3d: f64,
    pub horiz_m: f64,
    pub gain_m: f64,
    pub loss_m: f64,
    pub max_slope_deg: f64,
    pub max_grade_pct: f64,
    pub avg_grade_pct: f64,
    pub difficulty: String,
    pub segments: Vec<HikeSegment>,
    pub profile: Vec<HikeProfilePoint>,
    pub histogram: Vec<GradeHistogramBucket>,
}

fn round1(v: f64) -> f64 {
    (v * 10.0).round() / 10.0
}

fn round_coord(v: f64) -> f64 {
    (v * 1e7).round() / 1e7
}

/// Serialize a detailed profile into the catalog ``hike`` block.
pub fn stored_peak_hike(detail: &HikeProfileDetailed) -> peaky_preset::PeakHikeProfile {
    peaky_preset::PeakHikeProfile {
        hike_m_3d: round1(detail.hike_m_3d),
        horiz_m: round1(detail.horiz_m),
        gain_m: round1(detail.gain_m),
        loss_m: round1(detail.loss_m),
        max_slope_deg: round1(detail.max_slope_deg),
        max_grade_pct: round1(detail.max_grade_pct),
        avg_grade_pct: round1(detail.avg_grade_pct),
        difficulty: detail.difficulty.clone(),
        profile: detail
            .profile
            .iter()
            .map(|p| peaky_preset::PeakHikeProfilePoint {
                dist_m: round1(p.dist_m),
                elev_m: round1(p.elev_m),
                lat: round_coord(p.lat),
                lon: round_coord(p.lon),
            })
            .collect(),
        histogram: detail
            .histogram
            .iter()
            .filter(|b| b.dist_m > 0.5)
            .map(|b| peaky_preset::PeakHikeGradeBucket {
                label: b.label.clone(),
                min_grade_pct: b.min_grade_pct,
                max_grade_pct: b.max_grade_pct,
                dist_m: round1(b.dist_m),
                pct_of_route: round1(b.pct_of_route),
            })
            .collect(),
    }
}

const GRADE_BUCKETS: [(f64, f64, &str); 7] = [
    (0.0, 5.0, "0–5%"),
    (5.0, 10.0, "5–10%"),
    (10.0, 15.0, "10–15%"),
    (15.0, 20.0, "15–20%"),
    (20.0, 25.0, "20–25%"),
    (25.0, 30.0, "25–30%"),
    (30.0, f64::INFINITY, "30%+"),
];

pub fn slope_deg_to_grade_pct(deg: f64) -> f64 {
    deg.to_radians().tan() * 100.0
}

/// Overall hike difficulty from segment grades (easy / medium / difficult / extreme).
pub fn hike_difficulty(max_grade_pct: f64, avg_grade_pct: f64) -> &'static str {
    if max_grade_pct >= 25.0 || avg_grade_pct >= 12.0 {
        "extreme"
    } else if max_grade_pct >= 18.0 || avg_grade_pct >= 8.0 {
        "difficult"
    } else if max_grade_pct >= 10.0 || avg_grade_pct >= 5.0 {
        "medium"
    } else {
        "easy"
    }
}

fn grade_histogram(segments: &[HikeSegment]) -> Vec<GradeHistogramBucket> {
    let total: f64 = segments.iter().map(|s| s.dist_m).sum();
    GRADE_BUCKETS
        .iter()
        .map(|(min, max, label)| {
            let dist_m: f64 = segments
                .iter()
                .filter(|s| s.grade_pct >= *min && s.grade_pct < *max)
                .map(|s| s.dist_m)
                .sum();
            GradeHistogramBucket {
                label: (*label).to_string(),
                min_grade_pct: *min,
                max_grade_pct: if max.is_finite() { *max } else { 100.0 },
                dist_m,
                pct_of_route: if total > 0.0 {
                    (dist_m / total) * 100.0
                } else {
                    0.0
                },
            }
        })
        .collect()
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
    snap_to_local_summit_filtered(elev, seed_lat, seed_lon, radius_m, step_m, |_, _| true)
}

/// Like [`snap_to_local_summit`] but only considers samples where ``eligible(lat, lon)`` is true.
/// Use for catalog builds so an ineligible crest cell does not steal snap from a green shoulder.
pub fn snap_to_local_summit_filtered<E, F>(
    elev: &E,
    seed_lat: f64,
    seed_lon: f64,
    radius_m: f64,
    step_m: f64,
    eligible: F,
) -> Option<(f64, f64, f64)>
where
    E: HikeSampleElev,
    F: Fn(f64, f64) -> bool,
{
    let step = step_m.max(15.0);
    let max_ring = ((radius_m / step).ceil() as i32).max(0);
    let mut best: Option<(f64, f64, f64)> = None;

    let mut consider = |lat: f64, lon: f64| {
        if !eligible(lat, lon) {
            return;
        }
        let e = elev.sample_elev_m(lat, lon);
        if !(e.is_finite() && e > 0.0) {
            return;
        }
        if best.map(|(_, _, be)| e > be).unwrap_or(true) {
            best = Some((lat, lon, e));
        }
    };

    consider(seed_lat, seed_lon);
    for ring in 1..=max_ring {
        let dist = ring as f64 * step;
        if dist > radius_m + 1.0 {
            break;
        }
        let n_dirs = (8 * ring).max(8);
        for i in 0..n_dirs {
            let bearing = 360.0 * (i as f64) / (n_dirs as f64);
            let (lat, lon) = destination_point(seed_lat, seed_lon, bearing, dist);
            if haversine_m(seed_lat, seed_lon, lat, lon) > radius_m + 1.0 {
                continue;
            }
            consider(lat, lon);
        }
    }
    best
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
pub fn profile_hike_detailed<E: HikeSampleElev>(
    elev: &E,
    road_lat: f64,
    road_lon: f64,
    peak_lat: f64,
    peak_lon: f64,
    step_m: f64,
) -> Option<HikeProfileDetailed> {
    let total_h = haversine_m(road_lat, road_lon, peak_lat, peak_lon);
    if total_h <= 1.0 {
        let e = elev.sample_elev_m(road_lat, road_lon);
        return Some(HikeProfileDetailed {
            hike_m_3d: 0.0,
            horiz_m: 0.0,
            gain_m: 0.0,
            loss_m: 0.0,
            max_slope_deg: 0.0,
            max_grade_pct: 0.0,
            avg_grade_pct: 0.0,
            difficulty: "easy".into(),
            segments: Vec::new(),
            profile: vec![
                HikeProfilePoint {
                    dist_m: 0.0,
                    elev_m: e,
                    lat: road_lat,
                    lon: road_lon,
                },
                HikeProfilePoint {
                    dist_m: 0.0,
                    elev_m: e,
                    lat: peak_lat,
                    lon: peak_lon,
                },
            ],
            histogram: grade_histogram(&[]),
        });
    }
    let step = step_m.max(10.0);
    let n_steps = ((total_h / step).ceil() as usize).max(1);
    let mut prev_lat = road_lat;
    let mut prev_lon = road_lon;
    let mut prev_elev = elev.sample_elev_m(road_lat, road_lon);
    let mut hike_3d = 0.0;
    let mut horiz_total = 0.0;
    let mut gain_m = 0.0;
    let mut loss_m = 0.0;
    let mut max_slope = 0.0f64;
    let mut segments = Vec::with_capacity(n_steps);
    let mut profile = Vec::with_capacity(n_steps + 1);
    profile.push(HikeProfilePoint {
        dist_m: 0.0,
        elev_m: prev_elev,
        lat: road_lat,
        lon: road_lon,
    });

    for i in 1..=n_steps {
        let t = (i as f64) / (n_steps as f64);
        let lat = road_lat + t * (peak_lat - road_lat);
        let lon = road_lon + t * (peak_lon - road_lon);
        let e = elev.sample_elev_m(lat, lon);
        let horiz = haversine_m(prev_lat, prev_lon, lat, lon);
        if horiz > 0.5 {
            let vert = e - prev_elev;
            let slope = vert.atan2(horiz).to_degrees().abs();
            let grade_pct = slope_deg_to_grade_pct(slope);
            if slope.is_finite() {
                max_slope = max_slope.max(slope);
            }
            let seg_3d = (horiz * horiz + vert * vert).sqrt();
            hike_3d += seg_3d;
            horiz_total += horiz;
            if vert > 0.0 {
                gain_m += vert;
            } else {
                loss_m += -vert;
            }
            segments.push(HikeSegment {
                dist_m: seg_3d,
                horiz_m: horiz,
                grade_pct,
                slope_deg: slope,
            });
            profile.push(HikeProfilePoint {
                dist_m: horiz_total,
                elev_m: e,
                lat,
                lon,
            });
        }
        prev_lat = lat;
        prev_lon = lon;
        prev_elev = e;
    }

    let avg_grade_pct = if horiz_total > 0.0 {
        segments
            .iter()
            .map(|s| s.grade_pct * s.horiz_m)
            .sum::<f64>()
            / horiz_total
    } else {
        0.0
    };
    let max_grade_pct = slope_deg_to_grade_pct(max_slope);
    let histogram = grade_histogram(&segments);

    Some(HikeProfileDetailed {
        hike_m_3d: hike_3d,
        horiz_m: horiz_total,
        gain_m,
        loss_m,
        max_slope_deg: max_slope,
        max_grade_pct,
        avg_grade_pct,
        difficulty: hike_difficulty(max_grade_pct, avg_grade_pct).to_string(),
        segments,
        profile,
        histogram,
    })
}

pub fn profile_hike<E: HikeSampleElev>(
    elev: &E,
    road_lat: f64,
    road_lon: f64,
    peak_lat: f64,
    peak_lon: f64,
    step_m: f64,
) -> Option<HikeProfile> {
    profile_hike_detailed(elev, road_lat, road_lon, peak_lat, peak_lon, step_m).map(|d| HikeProfile {
        hike_m_3d: d.hike_m_3d,
        max_slope_deg: d.max_slope_deg,
        n_samples: d.profile.len(),
    })
}

/// Human-readable hike report for CLI / logs.
pub fn format_hike_report(name: Option<&str>, slug: &str, detail: &HikeProfileDetailed) -> String {
    let title = name.unwrap_or(slug);
    let mut out = format!(
        "\n=== Hike profile: {title} ({slug}) ===\n\
         3D distance: {:.0} m ({:.2} mi)\n\
         Horizontal: {:.0} m | Gain: {:.0} m | Loss: {:.0} m\n\
         Max grade: {:.1}% ({:.1}°) | Avg grade: {:.1}%\n\
         Difficulty: {}\n\n\
         Grade breakdown (by segment distance):\n",
        detail.hike_m_3d,
        detail.hike_m_3d / 1609.344,
        detail.horiz_m,
        detail.gain_m,
        detail.loss_m,
        detail.max_grade_pct,
        detail.max_slope_deg,
        detail.avg_grade_pct,
        detail.difficulty,
    );
    for bucket in &detail.histogram {
        if bucket.dist_m < 0.5 {
            continue;
        }
        out.push_str(&format!(
            "  {:>8}: {:>6.0} m ({:>5.1}% of route)\n",
            bucket.label, bucket.dist_m, bucket.pct_of_route
        ));
    }
    out
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
    fn grade_pct_to_deg_30_percent() {
        let deg = grade_pct_to_deg(DEFAULT_MAX_SLOPE_GRADE_PCT);
        assert!((deg - 16.699).abs() < 0.01);
        assert!((default_max_slope_deg() - deg).abs() < 1e-9);
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
    fn snap_on_eligible_skips_ineligible_crest() {
        let bump = OffsetBump {
            peak_lat: 38.0,
            peak_lon: -117.0,
            peak_elev: 2400.0,
            base: 2000.0,
        };
        let seed_lat = 38.0 + 0.0018;
        let seed_lon = -117.0;
        let crest_ineligible =
            |lat: f64, lon: f64| haversine_m(lat, lon, bump.peak_lat, bump.peak_lon) > 50.0;
        let (lat, lon, elev) = snap_to_local_summit_filtered(
            &bump,
            seed_lat,
            seed_lon,
            DEFAULT_SUMMIT_SNAP_M,
            30.0,
            crest_ineligible,
        )
        .unwrap();
        let (crest_lat, _, crest_elev) =
            snap_to_local_summit(&bump, seed_lat, seed_lon, DEFAULT_SUMMIT_SNAP_M, 30.0).unwrap();
        assert!((crest_elev - 2400.0).abs() < 1.0);
        assert!((elev - 2000.0).abs() < 1.0);
        assert!(haversine_m(lat, lon, crest_lat, bump.peak_lon) > 80.0);
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
