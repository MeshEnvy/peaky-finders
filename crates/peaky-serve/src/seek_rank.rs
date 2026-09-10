//! Goal-seek ranking: complete the goal, then extra mesh links, then weaker-leg margin.
//! Elevation is not a score. Forward reach is the last tiebreak (approach hops).

const PAST_GOAL_MARGIN_M: f64 = 250.0;

#[derive(Debug, Clone, Copy, Default)]
pub(crate) struct SeekRankScore {
    pub completes: bool,
    pub mesh_links: u32,
    pub margin_db: f64,
}

pub(crate) fn haversine_m(lat1: f64, lon1: f64, lat2: f64, lon2: f64) -> f64 {
    splatter::propagate::haversine_m(lat1, lon1, lat2, lon2)
}

pub(crate) fn bearing_deg(lat1: f64, lon1: f64, lat2: f64, lon2: f64) -> f64 {
    let phi1 = lat1.to_radians();
    let phi2 = lat2.to_radians();
    let dlambda = (lon2 - lon1).to_radians();
    let y = dlambda.sin() * phi2.cos();
    let x = phi1.cos() * phi2.sin() - phi1.sin() * phi2.cos() * dlambda.cos();
    (y.atan2(x).to_degrees() + 360.0) % 360.0
}

pub(crate) fn angle_diff_deg(a: f64, b: f64) -> f64 {
    let mut d = (a - b).abs() % 360.0;
    if d > 180.0 {
        d = 360.0 - d;
    }
    d
}

/// Hop distance projected onto the source→goal bearing (m). Backward/side hops score lower.
pub(crate) fn forward_reach_m(
    from_lat: f64,
    from_lon: f64,
    goal_lat: f64,
    goal_lon: f64,
    peak_lat: f64,
    peak_lon: f64,
) -> f64 {
    let hop_m = haversine_m(from_lat, from_lon, peak_lat, peak_lon);
    if hop_m <= 0.0 {
        return 0.0;
    }
    let goal_bearing = bearing_deg(from_lat, from_lon, goal_lat, goal_lon);
    let peak_bearing = bearing_deg(from_lat, from_lon, peak_lat, peak_lon);
    let delta = angle_diff_deg(peak_bearing, goal_bearing).to_radians();
    hop_m * delta.cos().max(0.0)
}

/// True when `peak` lies past the goal along the from→goal axis.
pub(crate) fn peak_is_past_goal(
    from_lat: f64,
    from_lon: f64,
    goal_lat: f64,
    goal_lon: f64,
    peak_lat: f64,
    peak_lon: f64,
) -> bool {
    let d_goal = haversine_m(from_lat, from_lon, goal_lat, goal_lon);
    let d_peak = haversine_m(from_lat, from_lon, peak_lat, peak_lon);
    if d_goal < 1.0 {
        return d_peak > PAST_GOAL_MARGIN_M;
    }
    let goal_bearing = bearing_deg(from_lat, from_lon, goal_lat, goal_lon);
    let peak_bearing = bearing_deg(from_lat, from_lon, peak_lat, peak_lon);
    let delta = angle_diff_deg(peak_bearing, goal_bearing);
    if delta >= 90.0 {
        return false;
    }
    let along_m = d_peak * delta.to_radians().cos();
    along_m > d_goal + PAST_GOAL_MARGIN_M
}

/// Completers first; non-completers by goal progress, then mesh links, margin, forward reach.
pub(crate) fn seek_peak_rank_key(
    from_lat: f64,
    from_lon: f64,
    goal_lat: f64,
    goal_lon: f64,
    peak_lon: f64,
    peak_lat: f64,
    score: SeekRankScore,
) -> (u8, f64, u32, f64, f64) {
    let forward = forward_reach_m(from_lat, from_lon, goal_lat, goal_lon, peak_lat, peak_lon);
    let goal_progress = if score.completes {
        0.0
    } else {
        haversine_m(from_lat, from_lon, goal_lat, goal_lon)
            - haversine_m(peak_lat, peak_lon, goal_lat, goal_lon)
    };
    (
        u8::from(score.completes),
        goal_progress,
        score.mesh_links,
        score.margin_db,
        forward,
    )
}

pub(crate) fn cmp_seek_peak_rank(
    from_lat: f64,
    from_lon: f64,
    goal_lat: f64,
    goal_lon: f64,
    a: (f64, f64, f64),
    b: (f64, f64, f64),
    a_score: SeekRankScore,
    b_score: SeekRankScore,
) -> std::cmp::Ordering {
    let ka = seek_peak_rank_key(
        from_lat,
        from_lon,
        goal_lat,
        goal_lon,
        a.0,
        a.1,
        a_score,
    );
    let kb = seek_peak_rank_key(
        from_lat,
        from_lon,
        goal_lat,
        goal_lon,
        b.0,
        b.1,
        b_score,
    );
    kb.partial_cmp(&ka).unwrap_or(std::cmp::Ordering::Equal)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn score(completes: bool, mesh_links: u32, margin_db: f64) -> SeekRankScore {
        SeekRankScore {
            completes,
            mesh_links,
            margin_db,
        }
    }

    #[test]
    fn farthest_rank_prefers_distant_peak_on_goal_bearing() {
        let from_lat = 38.0;
        let from_lon = -117.0;
        let goal_lat = 40.0;
        let goal_lon = -117.0;
        let near = (from_lon, from_lat + 0.1, 2500.0);
        let far = (from_lon, from_lat + 0.5, 2500.0);
        let zero = score(false, 0, 0.0);
        assert_eq!(
            cmp_seek_peak_rank(
                from_lat, from_lon, goal_lat, goal_lon, far, near, zero, zero
            ),
            std::cmp::Ordering::Less
        );
    }

    #[test]
    fn rank_ignores_elevation() {
        let from_lat = 38.0;
        let from_lon = -117.0;
        let goal_lat = 40.0;
        let goal_lon = -117.0;
        let high = (from_lon, from_lat + 0.3, 2800.0);
        let low = (from_lon, from_lat + 0.3, 2100.0);
        let zero = score(false, 0, 0.0);
        assert_eq!(
            cmp_seek_peak_rank(
                from_lat, from_lon, goal_lat, goal_lon, high, low, zero, zero
            ),
            std::cmp::Ordering::Equal
        );
    }

    #[test]
    fn rank_prefers_more_mesh_links() {
        let from_lat = 38.0;
        let from_lon = -117.0;
        let goal_lat = 38.4;
        let goal_lon = -117.0;
        let a = (from_lon, 38.35, 2000.0);
        let b = (from_lon, 38.35, 2600.0);
        assert_eq!(
            cmp_seek_peak_rank(
                from_lat,
                from_lon,
                goal_lat,
                goal_lon,
                a,
                b,
                score(true, 2, 4.0),
                score(true, 0, 20.0),
            ),
            std::cmp::Ordering::Less
        );
    }

    #[test]
    fn rank_prefers_margin_when_mesh_ties() {
        let from_lat = 38.0;
        let from_lon = -117.0;
        let goal_lat = 38.4;
        let goal_lon = -117.0;
        let a = (from_lon, 38.35, 2000.0);
        let b = (from_lon, 38.36, 2000.0);
        assert_eq!(
            cmp_seek_peak_rank(
                from_lat,
                from_lon,
                goal_lat,
                goal_lon,
                a,
                b,
                score(true, 1, 12.0),
                score(true, 1, 3.0),
            ),
            std::cmp::Ordering::Less
        );
    }

    #[test]
    fn farthest_rank_take_cap_gets_farthest_not_nearest() {
        let from_lat = 38.0;
        let from_lon = -117.0;
        let goal_lat = 40.0;
        let goal_lon = -117.0;
        let mut peaks = vec![
            (from_lon, from_lat + 0.01, 2000.0),
            (from_lon, from_lat + 0.5, 2200.0),
            (from_lon + 0.4, from_lat, 2500.0),
        ];
        let zero = score(false, 0, 0.0);
        peaks.sort_by(|a, b| {
            cmp_seek_peak_rank(from_lat, from_lon, goal_lat, goal_lon, *a, *b, zero, zero)
        });
        let best = peaks.first().unwrap();
        let hop_m = haversine_m(from_lat, from_lon, best.1, best.0);
        assert!(
            hop_m > 30_000.0,
            "best peak should be farthest on bearing, got {hop_m}m"
        );
    }

    #[test]
    fn past_goal_detects_overshoot() {
        let from_lat = 38.0;
        let from_lon = -117.0;
        let goal_lat = 38.4;
        let goal_lon = -117.0;
        assert!(!peak_is_past_goal(
            from_lat, from_lon, goal_lat, goal_lon, 38.3, -117.0
        ));
        assert!(peak_is_past_goal(
            from_lat, from_lon, goal_lat, goal_lon, 38.6, -117.0
        ));
    }

    #[test]
    fn rank_among_completers_prefers_distance_when_mesh_and_margin_tie() {
        let from_lat = 38.0;
        let from_lon = -117.0;
        let goal_lat = 38.4;
        let goal_lon = -117.0;
        let near = (from_lon, 38.35, 2100.0);
        let far = (from_lon, 38.55, 2100.0);
        let s = score(true, 0, 0.0);
        assert_eq!(
            cmp_seek_peak_rank(from_lat, from_lon, goal_lat, goal_lon, far, near, s, s),
            std::cmp::Ordering::Less
        );
    }

    #[test]
    fn virginia_nightengale_marks_toulon_as_overshoot() {
        let from_lat = 39.75567;
        let from_lon = -119.46126;
        let goal_lat = 39.776974;
        let goal_lon = -119.053759;
        let toulon_lat = 40.11782;
        let toulon_lon = -118.7273;
        let midway_lat = 39.766;
        let midway_lon = -119.25;
        assert!(peak_is_past_goal(
            from_lat, from_lon, goal_lat, goal_lon, toulon_lat, toulon_lon
        ));
        assert!(!peak_is_past_goal(
            from_lat, from_lon, goal_lat, goal_lon, midway_lat, midway_lon
        ));
        assert!(haversine_m(from_lat, from_lon, goal_lat, goal_lon) < 72_000.0);
        let ridge_lat = 39.770778;
        let ridge_lon = -119.174345;
        assert!(!peak_is_past_goal(
            from_lat, from_lon, goal_lat, goal_lon, ridge_lat, ridge_lon
        ));
    }

    #[test]
    fn rank_prefers_closer_non_completer_over_stronger_sideways_margin() {
        let from_lat = 38.0;
        let from_lon = -117.0;
        let goal_lat = 40.0;
        let goal_lon = -117.0;
        let closer = (from_lon + 0.2, from_lat + 0.35, 2200.0);
        let sideways = (from_lon + 0.45, from_lat + 0.05, 2400.0);
        assert_eq!(
            cmp_seek_peak_rank(
                from_lat,
                from_lon,
                goal_lat,
                goal_lon,
                closer,
                sideways,
                score(false, 0, 2.0),
                score(false, 0, 18.0),
            ),
            std::cmp::Ordering::Less
        );
    }

    #[test]
    fn rank_prefers_completing_hop_over_farther_relay() {
        let from_lat = 38.0;
        let from_lon = -117.0;
        let goal_lat = 38.4;
        let goal_lon = -117.0;
        let completing = (from_lon, 38.35, 2000.0);
        let far_relay = (from_lon, 38.55, 2600.0);
        assert_eq!(
            cmp_seek_peak_rank(
                from_lat,
                from_lon,
                goal_lat,
                goal_lon,
                completing,
                far_relay,
                score(true, 0, 0.0),
                score(false, 3, 20.0),
            ),
            std::cmp::Ordering::Less
        );
    }
}
