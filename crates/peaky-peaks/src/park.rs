//! Park-point pick: score jeep-road approaches by hike, not crow-flies.

use crate::hike::{HikeProfileDetailed, HikeSampleElev};
use crate::hike_route::resolve_hike;
use crate::osm::{JeepRoadIndex, PavedAnchorIndex};

const PARK_SECTORS: usize = 8;

#[derive(Debug, Clone, Copy)]
struct ParkCand {
    lat: f64,
    lon: f64,
    dist_m: f64,
    paved: bool,
}

/// One jeep-road sample per occupied bearing sector (nearest to dest in that sector),
/// plus the nearest paved highway so we do not leave pavement for a shoulder track.
pub fn park_candidates(
    roads: &JeepRoadIndex,
    paved: Option<&PavedAnchorIndex>,
    dest_lat: f64,
    dest_lon: f64,
    search_m: f64,
) -> Vec<(f64, f64, f64)> {
    park_candidates_scored(roads, paved, dest_lat, dest_lon, search_m)
        .into_iter()
        .map(|c| (c.lat, c.lon, c.dist_m))
        .collect()
}

fn park_candidates_scored(
    roads: &JeepRoadIndex,
    paved: Option<&PavedAnchorIndex>,
    dest_lat: f64,
    dest_lon: f64,
    search_m: f64,
) -> Vec<ParkCand> {
    let pts = roads.points_within(dest_lat, dest_lon, search_m);
    let mut best: [Option<ParkCand>; PARK_SECTORS] = [None; PARK_SECTORS];
    for (lat, lon, dist) in pts {
        let sector = bearing_sector(dest_lat, dest_lon, lat, lon);
        match best[sector] {
            None => {
                best[sector] = Some(ParkCand {
                    lat,
                    lon,
                    dist_m: dist,
                    paved: false,
                })
            }
            Some(cur) if dist < cur.dist_m => {
                best[sector] = Some(ParkCand {
                    lat,
                    lon,
                    dist_m: dist,
                    paved: false,
                })
            }
            _ => {}
        }
    }
    let mut out: Vec<ParkCand> = best.into_iter().flatten().collect();
    if let Some((lat, lon, dist)) =
        paved.and_then(|p| p.nearest_within(dest_lat, dest_lon, search_m))
    {
        if !out
            .iter()
            .any(|c| (c.lat - lat).abs() < 1e-7 && (c.lon - lon).abs() < 1e-7)
        {
            out.push(ParkCand {
                lat,
                lon,
                dist_m: dist,
                paved: true,
            });
        } else if let Some(c) = out
            .iter_mut()
            .find(|c| (c.lat - lat).abs() < 1e-7 && (c.lon - lon).abs() < 1e-7)
        {
            c.paved = true;
        }
    }
    out
}

fn bearing_sector(dest_lat: f64, dest_lon: f64, lat: f64, lon: f64) -> usize {
    let cos = dest_lat.to_radians().cos().abs().max(1e-6);
    let y = (lon - dest_lon) * cos;
    let x = lat - dest_lat;
    let deg = y.atan2(x).to_degrees();
    let wrapped = (deg + 360.0) % 360.0;
    ((wrapped / 45.0).floor() as usize) % PARK_SECTORS
}

pub struct ParkHike {
    pub road_lat: f64,
    pub road_lon: f64,
    pub road_m: f64,
    pub hike: HikeProfileDetailed,
}

fn hike_better(
    a: &HikeProfileDetailed,
    a_paved: bool,
    b: &HikeProfileDetailed,
    b_paved: bool,
) -> bool {
    if a_paved != b_paved {
        let (paved_h, dirt_h) = if a_paved { (a, b) } else { (b, a) };
        let comparable = paved_h.max_slope_deg <= dirt_h.max_slope_deg + 2.0
            && paved_h.hike_m_3d <= dirt_h.hike_m_3d * 1.25 + 80.0;
        if comparable {
            return a_paved;
        }
    }
    if (a.max_slope_deg - b.max_slope_deg).abs() > 0.5 {
        return a.max_slope_deg < b.max_slope_deg;
    }
    a.hike_m_3d < b.hike_m_3d
}

/// Try sector parks within ``search_m``; keep the gentlest (then shortest) hike.
pub fn select_park_and_hike<E: HikeSampleElev>(
    elev: &E,
    roads: &JeepRoadIndex,
    paved: Option<&PavedAnchorIndex>,
    dest_lat: f64,
    dest_lon: f64,
    search_m: f64,
    max_slope_deg: f64,
    path_max_m: f64,
    sample_m: f64,
    gate_eligibility: bool,
) -> Option<ParkHike> {
    let mut best: Option<(ParkHike, bool)> = None;
    for cand in park_candidates_scored(roads, paved, dest_lat, dest_lon, search_m) {
        let Some(hike) = resolve_hike(
            elev,
            cand.lat,
            cand.lon,
            dest_lat,
            dest_lon,
            max_slope_deg,
            path_max_m,
            sample_m,
            gate_eligibility,
        ) else {
            continue;
        };
        let take = best
            .as_ref()
            .map(|(cur, cur_paved)| hike_better(&hike, cand.paved, &cur.hike, *cur_paved))
            .unwrap_or(true);
        if take {
            best = Some((
                ParkHike {
                    road_lat: cand.lat,
                    road_lon: cand.lon,
                    road_m: cand.dist_m,
                    hike,
                },
                cand.paved,
            ));
        }
    }
    best.map(|(park, _)| park)
}

/// Site/place fallback: nearest road even when it is farther than a hike path.
pub fn nearest_park_hike<E: HikeSampleElev>(
    elev: &E,
    roads: &JeepRoadIndex,
    paved: Option<&PavedAnchorIndex>,
    dest_lat: f64,
    dest_lon: f64,
    search_m: f64,
    max_slope_deg: f64,
    path_max_m: f64,
    sample_m: f64,
    gate_eligibility: bool,
) -> Option<ParkHike> {
    let jeep = roads.nearest_within(dest_lat, dest_lon, search_m);
    let paved_pt = paved.and_then(|p| p.nearest_within(dest_lat, dest_lon, search_m));
    let (road_lat, road_lon, road_m) = match (jeep, paved_pt) {
        (Some(j), Some(p)) if p.2 <= j.2 + 80.0 => p,
        (Some(j), _) => j,
        (None, Some(p)) => p,
        (None, None) => return None,
    };
    let hike = resolve_hike(
        elev,
        road_lat,
        road_lon,
        dest_lat,
        dest_lon,
        max_slope_deg,
        path_max_m,
        sample_m,
        gate_eligibility,
    )?;
    Some(ParkHike {
        road_lat,
        road_lon,
        road_m,
        hike,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::hike::{default_max_slope_deg, haversine_m};
    use crate::osm::test_index_from_points;

    /// South of the peak is a cliff face; the east saddle is flat.
    struct SouthCliff {
        peak_lat: f64,
    }

    impl HikeSampleElev for SouthCliff {
        fn sample_elev_m(&self, lat: f64, _lon: f64) -> f64 {
            if lat < self.peak_lat - 0.0015 {
                1600.0
            } else {
                2400.0
            }
        }
    }

    #[test]
    fn sectors_include_non_nearest_park() {
        let roads = test_index_from_points(&[(38.001, -117.0), (38.004, -116.994)]);
        let cands = park_candidates(&roads, None, 38.004, -117.0, 1609.0);
        assert!(
            cands.len() >= 2,
            "expected both approach sides, got {cands:?}"
        );
    }

    #[test]
    fn prefers_farther_saddle_over_nearest_cliff() {
        let near = (38.001, -117.0);
        let far = (38.004, -116.994);
        let peak = (38.004, -117.0);
        let roads = test_index_from_points(&[near, far]);
        let elev = SouthCliff { peak_lat: peak.0 };
        let picked = select_park_and_hike(
            &elev,
            &roads,
            None,
            peak.0,
            peak.1,
            1609.0,
            default_max_slope_deg(),
            1609.0,
            30.0,
            true,
        )
        .expect("saddle hike");
        let far_d = haversine_m(picked.road_lat, picked.road_lon, far.0, far.1);
        let near_d = haversine_m(picked.road_lat, picked.road_lon, near.0, near.1);
        assert!(
            far_d < 20.0 && near_d > 50.0,
            "should park at east saddle, got {} {}",
            picked.road_lat,
            picked.road_lon
        );
        assert!(picked.hike.max_slope_deg <= default_max_slope_deg() + 0.5);
    }

    #[test]
    fn empty_roads_yield_no_park() {
        let roads = test_index_from_points(&[]);
        let elev = SouthCliff { peak_lat: 38.004 };
        assert!(select_park_and_hike(
            &elev,
            &roads,
            None,
            38.004,
            -117.0,
            1609.0,
            default_max_slope_deg(),
            1609.0,
            30.0,
            true,
        )
        .is_none());
    }

    struct FlatPark(f64);

    impl HikeSampleElev for FlatPark {
        fn sample_elev_m(&self, _lat: f64, _lon: f64) -> f64 {
            self.0
        }
    }

    #[test]
    fn prefers_nearby_paved_over_closer_dirt() {
        let peak = (38.0, -117.0);
        let dirt = (38.0, -117.0004);
        let paved_pt = (38.0, -116.9992);
        let roads = test_index_from_points(&[dirt]);
        let paved = crate::osm::PavedAnchorIndex::test_from_points(&[paved_pt]);
        let elev = FlatPark(900.0);
        let picked = select_park_and_hike(
            &elev,
            &roads,
            Some(&paved),
            peak.0,
            peak.1,
            1609.0,
            default_max_slope_deg(),
            1609.0,
            30.0,
            true,
        )
        .expect("park");
        let paved_d = haversine_m(picked.road_lat, picked.road_lon, paved_pt.0, paved_pt.1);
        let dirt_d = haversine_m(picked.road_lat, picked.road_lon, dirt.0, dirt.1);
        assert!(
            paved_d < 20.0 && dirt_d > 40.0,
            "should park on pavement, got {} {}",
            picked.road_lat,
            picked.road_lon
        );
    }
}
