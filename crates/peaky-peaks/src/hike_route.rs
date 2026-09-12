//! Grade-capped DEM A* hike routing with straight-chord fallback.

use std::cmp::{Ordering, Reverse};
use std::collections::{BinaryHeap, HashMap};

use crate::hike::{
    grade_histogram, haversine_m, hike_difficulty, profile_hike_detailed, slope_deg_to_grade_pct,
    HikeProfileDetailed, HikeProfilePoint, HikeSampleElev, HikeSegment,
};

const SEARCH_PAD_M: f64 = 500.0;
/// Site display: if the 30% cap finds nothing, retry these before a chord.
const RELAXED_SLOPE_DEGS: [f64; 4] = [21.8, 26.6, 31.0, 45.0];
/// Naismith-ish: prefer contouring over climb-then-descend on a bump.
const CLIMB_W: f64 = 4.0;
const DESC_W: f64 = 8.0;

fn hike_edge_cost(horiz: f64, vert: f64) -> f64 {
    horiz + CLIMB_W * vert.max(0.0) + DESC_W * (-vert).max(0.0)
}

fn meters_to_deg(lat: f64, dist_m: f64) -> (f64, f64) {
    let lat_deg = dist_m / 111_320.0;
    let cos_lat = lat.to_radians().cos().abs().max(1e-6);
    let lon_deg = dist_m / (111_320.0 * cos_lat);
    (lat_deg, lon_deg)
}

fn cell_center(
    min_lat: f64,
    min_lon: f64,
    row: i32,
    col: i32,
    step_lat: f64,
    step_lon: f64,
) -> (f64, f64) {
    (
        min_lat + row as f64 * step_lat,
        min_lon + col as f64 * step_lon,
    )
}

fn nearest_cell(
    lat: f64,
    lon: f64,
    min_lat: f64,
    min_lon: f64,
    step_lat: f64,
    step_lon: f64,
    rows: i32,
    cols: i32,
) -> (i32, i32) {
    let row = ((lat - min_lat) / step_lat).round() as i32;
    let col = ((lon - min_lon) / step_lon).round() as i32;
    (
        row.clamp(0, rows - 1),
        col.clamp(0, cols - 1),
    )
}

struct HikeEdge {
    len_3d: f64,
    cost: f64,
}

fn segment_grade_ok<E: HikeSampleElev>(
    elev: &E,
    lat1: f64,
    lon1: f64,
    lat2: f64,
    lon2: f64,
    max_slope_deg: f64,
    allow_steep: bool,
) -> Option<HikeEdge> {
    let horiz = haversine_m(lat1, lon1, lat2, lon2);
    if horiz <= 0.5 {
        return Some(HikeEdge {
            len_3d: 0.0,
            cost: 0.0,
        });
    }
    let e1 = elev.sample_elev_m(lat1, lon1);
    let e2 = elev.sample_elev_m(lat2, lon2);
    let vert = e2 - e1;
    let slope = vert.atan2(horiz).to_degrees().abs();
    if !allow_steep && slope > max_slope_deg + 0.5 {
        return None;
    }
    Some(HikeEdge {
        len_3d: (horiz * horiz + vert * vert).sqrt(),
        cost: hike_edge_cost(horiz, vert),
    })
}

/// 8-connected A* on a DEM grid; returns vertex coordinates park→dest.
pub fn route_hike_coords<E: HikeSampleElev>(
    elev: &E,
    park_lat: f64,
    park_lon: f64,
    dest_lat: f64,
    dest_lon: f64,
    max_slope_deg: f64,
    path_max_m: f64,
    sample_m: f64,
) -> Option<Vec<(f64, f64)>> {
    let step_m = sample_m.max(10.0);
    let mid_lat = (park_lat + dest_lat) * 0.5;
    let (step_lat, step_lon) = meters_to_deg(mid_lat, step_m);
    let pad_m = path_max_m.max(SEARCH_PAD_M);
    let (pad_lat, pad_lon) = meters_to_deg(mid_lat, pad_m);
    let min_lat = park_lat.min(dest_lat) - pad_lat;
    let max_lat = park_lat.max(dest_lat) + pad_lat;
    let min_lon = park_lon.min(dest_lon) - pad_lon;
    let max_lon = park_lon.max(dest_lon) + pad_lon;
    let rows = (((max_lat - min_lat) / step_lat).ceil() as i32).max(2);
    let cols = (((max_lon - min_lon) / step_lon).ceil() as i32).max(2);

    let start = nearest_cell(
        park_lat, park_lon, min_lat, min_lon, step_lat, step_lon, rows, cols,
    );
    let goal = nearest_cell(
        dest_lat, dest_lon, min_lat, min_lon, step_lat, step_lon, rows, cols,
    );

    #[derive(Copy, Clone, Eq, PartialEq, Ord, PartialOrd, Hash)]
    struct Node {
        row: i32,
        col: i32,
    }

    let mut open = BinaryHeap::new();
    let mut g_score: HashMap<Node, f64> = HashMap::new();
    let mut g_len: HashMap<Node, f64> = HashMap::new();
    let mut came_from: HashMap<Node, Node> = HashMap::new();

    let start_node = Node {
        row: start.0,
        col: start.1,
    };
    let goal_node = Node {
        row: goal.0,
        col: goal.1,
    };
    let (start_lat, start_lon) =
        cell_center(min_lat, min_lon, start.0, start.1, step_lat, step_lon);
    let (goal_lat, goal_lon) = cell_center(min_lat, min_lon, goal.0, goal.1, step_lat, step_lon);
    let h0 = haversine_m(start_lat, start_lon, goal_lat, goal_lon);
    g_score.insert(start_node, 0.0);
    g_len.insert(start_node, 0.0);
    open.push(Reverse((
        OrderedFloat(h0),
        OrderedFloat(0.0),
        start_node,
    )));

    const NEIGHBORS: [(i32, i32); 8] = [
        (-1, -1),
        (-1, 0),
        (-1, 1),
        (0, -1),
        (0, 1),
        (1, -1),
        (1, 0),
        (1, 1),
    ];

    while let Some(Reverse((OrderedFloat(_f), OrderedFloat(g), current))) = open.pop() {
        if current == goal_node {
            let mut path = Vec::new();
            let mut node = current;
            loop {
                let (lat, lon) = cell_center(
                    min_lat,
                    min_lon,
                    node.row,
                    node.col,
                    step_lat,
                    step_lon,
                );
                path.push((lat, lon));
                if let Some(prev) = came_from.get(&node) {
                    node = *prev;
                } else {
                    break;
                }
            }
            path.reverse();
            if path.len() >= 2 {
                path[0] = (park_lat, park_lon);
                *path.last_mut().unwrap() = (dest_lat, dest_lon);
            }
            return Some(path);
        }

        let cur_len = g_len.get(&current).copied().unwrap_or(g);
        if cur_len + step_m > path_max_m + 1.0 {
            continue;
        }

        let (cur_lat, cur_lon) = cell_center(
            min_lat,
            min_lon,
            current.row,
            current.col,
            step_lat,
            step_lon,
        );

        for (dr, dc) in NEIGHBORS {
            let nr = current.row + dr;
            let nc = current.col + dc;
            if nr < 0 || nc < 0 || nr >= rows || nc >= cols {
                continue;
            }
            let next = Node { row: nr, col: nc };
            let (n_lat, n_lon) =
                cell_center(min_lat, min_lon, nr, nc, step_lat, step_lon);
            let Some(edge) = segment_grade_ok(
                elev,
                cur_lat,
                cur_lon,
                n_lat,
                n_lon,
                max_slope_deg,
                next == goal_node,
            ) else {
                continue;
            };
            let tentative_len = cur_len + edge.len_3d;
            if tentative_len > path_max_m + 1.0 {
                continue;
            }
            let tentative = g + edge.cost;
            let better = g_score
                .get(&next)
                .map(|prev| tentative < *prev - 1e-6)
                .unwrap_or(true);
            if !better {
                continue;
            }
            came_from.insert(next, current);
            g_score.insert(next, tentative);
            g_len.insert(next, tentative_len);
            let h = haversine_m(n_lat, n_lon, goal_lat, goal_lon);
            open.push(Reverse((
                OrderedFloat(tentative + h),
                OrderedFloat(tentative),
                next,
            )));
        }
    }

    None
}

#[derive(Copy, Clone, PartialEq)]
struct OrderedFloat(f64);

impl Eq for OrderedFloat {}

impl PartialOrd for OrderedFloat {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

impl Ord for OrderedFloat {
    fn cmp(&self, other: &Self) -> Ordering {
        self.0.partial_cmp(&other.0).unwrap_or(Ordering::Equal)
    }
}

/// Profile a polyline at ``step_m`` spacing (same stats as straight hike profiler).
pub fn profile_polyline_detailed<E: HikeSampleElev>(
    elev: &E,
    coords: &[(f64, f64)],
    step_m: f64,
) -> Option<HikeProfileDetailed> {
    if coords.len() < 2 {
        return None;
    }
    let step = step_m.max(10.0);
    let mut prev_lat = coords[0].0;
    let mut prev_lon = coords[0].1;
    let mut prev_elev = elev.sample_elev_m(prev_lat, prev_lon);
    let mut hike_3d = 0.0;
    let mut horiz_total = 0.0;
    let mut gain_m = 0.0;
    let mut loss_m = 0.0;
    let mut max_slope = 0.0f64;
    let mut segments = Vec::new();
    let mut profile = Vec::new();
    profile.push(HikeProfilePoint {
        dist_m: 0.0,
        elev_m: prev_elev,
        lat: prev_lat,
        lon: prev_lon,
    });

    for w in coords.windows(2) {
        let (lat1, lon1) = w[0];
        let (lat2, lon2) = w[1];
        let seg_len = haversine_m(lat1, lon1, lat2, lon2);
        if seg_len <= 0.5 {
            continue;
        }
        let n = ((seg_len / step).ceil() as usize).max(1);
        for i in 1..=n {
            let t = (i as f64) / (n as f64);
            let lat = lat1 + t * (lat2 - lat1);
            let lon = lon1 + t * (lon2 - lon1);
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
        difficulty: hike_difficulty(max_grade_pct, avg_grade_pct, horiz_total, gain_m).to_string(),
        segments,
        profile,
        histogram,
    })
}

fn detail_passes_gate(detail: &HikeProfileDetailed, max_slope_deg: f64, path_max_m: f64) -> bool {
    detail.hike_m_3d <= path_max_m + 1.0 && detail.max_slope_deg <= max_slope_deg + 0.5
}

/// Pathfind when crow-flies ≤ ``path_max_m``; fall back to grade-safe chord when allowed.
pub fn resolve_hike<E: HikeSampleElev>(
    elev: &E,
    park_lat: f64,
    park_lon: f64,
    dest_lat: f64,
    dest_lon: f64,
    max_slope_deg: f64,
    path_max_m: f64,
    sample_m: f64,
    gate_eligibility: bool,
) -> Option<HikeProfileDetailed> {
    let crow = haversine_m(park_lat, park_lon, dest_lat, dest_lon);

    if crow <= path_max_m + 1.0 {
        let mut caps = vec![max_slope_deg];
        if !gate_eligibility {
            caps.extend(
                RELAXED_SLOPE_DEGS
                    .iter()
                    .copied()
                    .filter(|c| *c > max_slope_deg + 0.5),
            );
        }
        for cap in caps {
            if let Some(coords) = route_hike_coords(
                elev,
                park_lat,
                park_lon,
                dest_lat,
                dest_lon,
                cap,
                path_max_m,
                sample_m,
            ) {
                if let Some(detail) = profile_polyline_detailed(elev, &coords, sample_m) {
                    if !gate_eligibility || detail_passes_gate(&detail, max_slope_deg, path_max_m) {
                        return Some(detail);
                    }
                }
            }
        }
    }

    let chord = profile_hike_detailed(
        elev,
        park_lat,
        park_lon,
        dest_lat,
        dest_lon,
        sample_m,
    )?;
    if gate_eligibility && chord.max_slope_deg > max_slope_deg + 0.5 {
        return None;
    }
    Some(chord)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::hike::{default_max_slope_deg, DEFAULT_MAX_HIKE_M};

    struct FlatElev(f64);

    impl HikeSampleElev for FlatElev {
        fn sample_elev_m(&self, _lat: f64, _lon: f64) -> f64 {
            self.0
        }
    }

    /// Steep cliff band on the direct chord; flat terrain elsewhere.
    struct CliffOnChord {
        park_lat: f64,
        park_lon: f64,
        dest_lat: f64,
        dest_lon: f64,
        base: f64,
        cliff_elev: f64,
    }

    impl HikeSampleElev for CliffOnChord {
        fn sample_elev_m(&self, lat: f64, lon: f64) -> f64 {
            let total = haversine_m(self.park_lat, self.park_lon, self.dest_lat, self.dest_lon);
            if total <= 1.0 {
                return self.base;
            }
            let d_park = haversine_m(lat, lon, self.park_lat, self.park_lon);
            let d_dest = haversine_m(lat, lon, self.dest_lat, self.dest_lon);
            let along = (d_park + d_dest - total).abs();
            if along < 35.0 && d_park > 80.0 && d_dest > 80.0 {
                self.cliff_elev
            } else {
                self.base
            }
        }
    }

    /// Steep everywhere inside the search disc.
    struct SteepDisc {
        center_lat: f64,
        center_lon: f64,
        base: f64,
    }

    impl HikeSampleElev for SteepDisc {
        fn sample_elev_m(&self, lat: f64, lon: f64) -> f64 {
            let h = haversine_m(lat, lon, self.center_lat, self.center_lon);
            self.base + (h / 10.0) * 80.0
        }
    }

    fn path_max_m() -> f64 {
        1609.0
    }

    #[test]
    fn cliff_on_chord_routes_around() {
        let park_lat = 38.0;
        let park_lon = -117.0;
        let dest_lat = 38.003;
        let dest_lon = -117.0;
        let elev = CliffOnChord {
            park_lat,
            park_lon,
            dest_lat,
            dest_lon,
            base: 2000.0,
            cliff_elev: 2400.0,
        };
        let ceiling = default_max_slope_deg();
        let detail = resolve_hike(
            &elev,
            park_lat,
            park_lon,
            dest_lat,
            dest_lon,
            ceiling,
            path_max_m(),
            30.0,
            true,
        )
        .expect("wrap-around path");
        assert!(detail.max_slope_deg <= ceiling + 0.5);
        assert!(detail.hike_m_3d <= path_max_m() + 1.0);
        let chord = profile_hike_detailed(&elev, park_lat, park_lon, dest_lat, dest_lon, 30.0)
            .unwrap();
        assert!(chord.max_slope_deg > ceiling + 0.5);
        assert!(detail.horiz_m > chord.horiz_m * 0.95);
    }

    #[test]
    fn steep_disc_no_path_peak_rejected() {
        let park_lat = 38.0;
        let park_lon = -117.0;
        let dest_lat = 38.002;
        let dest_lon = -117.0;
        let mid_lat = (park_lat + dest_lat) * 0.5;
        let mid_lon = (park_lon + dest_lon) * 0.5;
        let elev = SteepDisc {
            center_lat: mid_lat,
            center_lon: mid_lon,
            base: 2000.0,
        };
        let ceiling = default_max_slope_deg();
        assert!(resolve_hike(
            &elev,
            park_lat,
            park_lon,
            dest_lat,
            dest_lon,
            ceiling,
            path_max_m(),
            30.0,
            true,
        )
        .is_none());
    }

    #[test]
    fn flat_chord_fallback_when_astar_degenerate() {
        let flat = FlatElev(2000.0);
        let park_lat = 38.0;
        let park_lon = -117.0;
        let dest_lat = 38.001;
        let dest_lon = -117.0;
        let ceiling = default_max_slope_deg();
        let detail = resolve_hike(
            &flat,
            park_lat,
            park_lon,
            dest_lat,
            dest_lon,
            ceiling,
            path_max_m(),
            30.0,
            true,
        )
        .unwrap();
        assert!(detail.max_slope_deg < 1.0);
        let horiz = haversine_m(park_lat, park_lon, dest_lat, dest_lon);
        assert!((detail.horiz_m - horiz).abs() < 5.0);
    }

    #[test]
    fn site_ungated_keeps_steep_chord_when_astar_skipped() {
        let park_lat = 38.0;
        let park_lon = -117.0;
        let dest_lat = 38.05;
        let dest_lon = -117.0;
        let elev = SteepDisc {
            center_lat: (park_lat + dest_lat) * 0.5,
            center_lon: park_lon,
            base: 2000.0,
        };
        let detail = resolve_hike(
            &elev,
            park_lat,
            park_lon,
            dest_lat,
            dest_lon,
            default_max_slope_deg(),
            path_max_m(),
            30.0,
            false,
        )
        .expect("ungated chord");
        assert!(detail.hike_m_3d > DEFAULT_MAX_HIKE_M);
    }

    struct Pinnacle {
        dest_lat: f64,
        dest_lon: f64,
        base: f64,
        peak: f64,
    }

    impl HikeSampleElev for Pinnacle {
        fn sample_elev_m(&self, lat: f64, lon: f64) -> f64 {
            if haversine_m(lat, lon, self.dest_lat, self.dest_lon) < 22.0 {
                self.peak
            } else {
                self.base
            }
        }
    }

    /// Gentle north-south bump on the chord; flat just off to the east.
    struct BumpThenSaddle {
        park_lat: f64,
        park_lon: f64,
        dest_lat: f64,
        dest_lon: f64,
        base: f64,
        bump: f64,
    }

    impl HikeSampleElev for BumpThenSaddle {
        fn sample_elev_m(&self, lat: f64, lon: f64) -> f64 {
            let total = haversine_m(
                self.park_lat,
                self.park_lon,
                self.dest_lat,
                self.dest_lon,
            );
            if total <= 1.0 {
                return self.base;
            }
            let d_park = haversine_m(lat, lon, self.park_lat, self.park_lon);
            let d_dest = haversine_m(lat, lon, self.dest_lat, self.dest_lon);
            let along_err = (d_park + d_dest - total).abs();
            let t = d_park / total;
            if along_err < 50.0 && (0.2..0.65).contains(&t) {
                let mid = 0.42;
                let half = 0.22;
                let u = (1.0 - (t - mid).abs() / half).max(0.0);
                return self.base + (self.bump - self.base) * u;
            }
            self.base
        }
    }

    #[test]
    fn bump_then_saddle_avoids_crest() {
        let park_lat = 38.0;
        let park_lon = -117.0;
        let dest_lat = 38.006;
        let dest_lon = -117.0;
        let elev = BumpThenSaddle {
            park_lat,
            park_lon,
            dest_lat,
            dest_lon,
            base: 1000.0,
            bump: 1040.0,
        };
        let detail = resolve_hike(
            &elev,
            park_lat,
            park_lon,
            dest_lat,
            dest_lon,
            default_max_slope_deg(),
            path_max_m(),
            30.0,
            true,
        )
        .expect("contour around the bump");
        assert!(
            detail.loss_m < 8.0,
            "should not climb then descend the bump, loss {}",
            detail.loss_m
        );
        let crest = detail
            .profile
            .iter()
            .map(|p| p.elev_m)
            .fold(f64::NEG_INFINITY, f64::max);
        assert!(
            crest < 1030.0,
            "path crested the bump at {crest} m"
        );
    }

    #[test]
    fn last_cell_may_exceed_slope_cap() {
        let park_lat = 38.0;
        let park_lon = -117.0;
        let dest_lat = 38.0008;
        let dest_lon = -117.0;
        let elev = Pinnacle {
            dest_lat,
            dest_lon,
            base: 2000.0,
            peak: 2025.0,
        };
        let coords = route_hike_coords(
            &elev,
            park_lat,
            park_lon,
            dest_lat,
            dest_lon,
            default_max_slope_deg(),
            path_max_m(),
            30.0,
        )
        .expect("A* should finish even if the last hop is steep");
        assert!(coords.len() >= 2);
    }
}
