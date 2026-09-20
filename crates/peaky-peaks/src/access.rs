//! Assemble jeep + hike legs from a park pick (four access modes).

use crate::hike::{
    default_max_slope_deg, haversine_m, stored_peak_hike, zero_hike_at, HikeProfileDetailed,
    HikeSampleElev,
};
use crate::hike_route::resolve_hike;
use crate::jeep::{
    profile_along_polyline, route_jeep_closest_approach, route_jeep_detailed, stored_peak_jeep,
    JeepLengthBudget, JeepProfileDetailed,
};
use crate::osm::OsmRouting;
use crate::park::ParkHike;

/// Minimum horizontal length before we treat a leg as real (m).
pub const LEG_MIN_M: f64 = 5.0;

pub use crate::park::ON_ROAD_SNAP_M;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AccessMode {
    Both,
    HikeOnly,
    JeepOnly,
    Neither,
}

impl AccessMode {
    pub fn needs_jeep(self) -> bool {
        matches!(self, Self::Both | Self::JeepOnly)
    }

    pub fn needs_hike(self) -> bool {
        matches!(self, Self::Both | Self::HikeOnly)
    }
}

pub fn classify_access_mode(park_paved: bool, hike_horiz_m: f64) -> AccessMode {
    let has_hike = hike_horiz_m > LEG_MIN_M;
    match (park_paved, has_hike) {
        (true, false) => AccessMode::Neither,
        (true, true) => AccessMode::HikeOnly,
        (false, false) => AccessMode::JeepOnly,
        (false, true) => AccessMode::Both,
    }
}

pub struct BuiltAccess {
    pub mode: AccessMode,
    pub road_lat: f64,
    pub road_lon: f64,
    pub road_m: f64,
    pub paved_lat: f64,
    pub paved_lon: f64,
    pub jeep_m: f64,
    pub hike_m: f64,
    pub max_slope_deg: f64,
    pub hike: peaky_preset::PeakHikeProfile,
    pub jeep: peaky_preset::PeakJeepProfile,
    pub hike_detail: HikeProfileDetailed,
}

fn zero_jeep_at<E: HikeSampleElev>(
    elev: &E,
    lat: f64,
    lon: f64,
    sample_m: f64,
) -> Option<JeepProfileDetailed> {
    profile_along_polyline(elev, &[(lat, lon)], sample_m, &[])
}

fn jeep_leg_from_park_strict<E: HikeSampleElev>(
    elev: &E,
    routing: &OsmRouting,
    picked: &ParkHike,
    max_jeep_m: f64,
    profile_sample_m: f64,
) -> Option<(f64, f64, f64, peaky_preset::PeakJeepProfile)> {
    let route = route_jeep_detailed(
        &routing.graph,
        &routing.paved,
        picked.road_lat,
        picked.road_lon,
        max_jeep_m,
        None,
    )?;
    let detail = profile_along_polyline(
        elev,
        &route.coords,
        profile_sample_m,
        &route.road_segments,
    )?;
    Some((
        route.paved_lat,
        route.paved_lon,
        detail.horiz_m,
        stored_peak_jeep(&detail),
    ))
}

fn jeep_leg_from_park_display<E: HikeSampleElev>(
    elev: &E,
    routing: &OsmRouting,
    picked: &ParkHike,
    jeep_search_m: f64,
    profile_sample_m: f64,
) -> (f64, f64, f64, peaky_preset::PeakJeepProfile) {
    if let Some(leg) = jeep_leg_from_park_strict(elev, routing, picked, jeep_search_m, profile_sample_m)
    {
        return leg;
    }
    let detail = zero_jeep_at(elev, picked.road_lat, picked.road_lon, profile_sample_m)
        .expect("zero jeep at park");
    (
        picked.road_lat,
        picked.road_lon,
        0.0,
        stored_peak_jeep(&detail),
    )
}

pub fn build_access_from_park<E: HikeSampleElev>(
    elev: &E,
    routing: &OsmRouting,
    picked: &ParkHike,
    max_jeep_m: f64,
    profile_sample_m: f64,
) -> Option<BuiltAccess> {
    let mode = classify_access_mode(picked.park_paved, picked.hike.horiz_m);
    let (paved_lat, paved_lon, jeep_m, jeep) = if mode.needs_jeep() {
        jeep_leg_from_park_strict(elev, routing, picked, max_jeep_m, profile_sample_m)?
    } else {
        let detail = zero_jeep_at(elev, picked.road_lat, picked.road_lon, profile_sample_m)?;
        (
            picked.road_lat,
            picked.road_lon,
            0.0,
            stored_peak_jeep(&detail),
        )
    };

    let hike_detail = if mode.needs_hike() {
        picked.hike.clone()
    } else {
        zero_hike_at(elev, picked.road_lat, picked.road_lon)
    };

    Some(BuiltAccess {
        mode,
        road_lat: picked.road_lat,
        road_lon: picked.road_lon,
        road_m: picked.road_m,
        paved_lat,
        paved_lon,
        jeep_m,
        hike_m: hike_detail.hike_m_3d,
        max_slope_deg: hike_detail.max_slope_deg,
        hike: stored_peak_hike(&hike_detail),
        jeep,
        hike_detail,
    })
}

/// Site/pin display: jeep from pavement to the closest reachable OSM node, then hike.
pub fn build_access_from_paved_approach<E: HikeSampleElev>(
    elev: &E,
    routing: &OsmRouting,
    dest_lat: f64,
    dest_lon: f64,
    max_jeep_m: f64,
    hike_path_max_m: f64,
    profile_sample_m: f64,
) -> Option<BuiltAccess> {
    let route = route_jeep_closest_approach(
        &routing.graph,
        &routing.paved,
        dest_lat,
        dest_lon,
        max_jeep_m,
        None,
    )?;
    let (park_lat, park_lon) = *route.coords.last()?;
    let crow = haversine_m(park_lat, park_lon, dest_lat, dest_lon);
    let hike_detail = if crow <= LEG_MIN_M {
        zero_hike_at(elev, dest_lat, dest_lon)
    } else {
        resolve_hike(
            elev,
            park_lat,
            park_lon,
            dest_lat,
            dest_lon,
            default_max_slope_deg(),
            hike_path_max_m.max(crow * 2.0 + 500.0),
            profile_sample_m,
            false,
        )?
    };
    let jeep_detail = profile_along_polyline(
        elev,
        &route.coords,
        profile_sample_m,
        &route.road_segments,
    )?;
    let park_paved = jeep_detail.horiz_m <= LEG_MIN_M;
    Some(BuiltAccess {
        mode: classify_access_mode(park_paved, hike_detail.horiz_m),
        road_lat: park_lat,
        road_lon: park_lon,
        road_m: crow,
        paved_lat: route.paved_lat,
        paved_lon: route.paved_lon,
        jeep_m: jeep_detail.horiz_m,
        hike_m: hike_detail.hike_m_3d,
        max_slope_deg: hike_detail.max_slope_deg,
        hike: stored_peak_hike(&hike_detail),
        jeep: stored_peak_jeep(&jeep_detail),
        hike_detail,
    })
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CatalogBuildDrop {
    Jeep,
    Slope,
}

/// Peak catalog: paved-first closest approach with jeep length budgets, then gated hike.
pub fn build_access_catalog<E: HikeSampleElev>(
    elev: &E,
    routing: &OsmRouting,
    dest_lat: f64,
    dest_lon: f64,
    jeep_extreme: &peaky_preset::JeepExtremeBands,
    max_slope_deg: f64,
    hike_path_max_m: f64,
    profile_sample_m: f64,
) -> Result<BuiltAccess, CatalogBuildDrop> {
    let budget = JeepLengthBudget::from_extreme(jeep_extreme);
    let route = route_jeep_closest_approach(
        &routing.graph,
        &routing.paved,
        dest_lat,
        dest_lon,
        jeep_extreme.max_route_m,
        Some(&budget),
    )
    .ok_or(CatalogBuildDrop::Jeep)?;
    let (park_lat, park_lon) = route.coords.last().copied().ok_or(CatalogBuildDrop::Jeep)?;
    let crow = haversine_m(park_lat, park_lon, dest_lat, dest_lon);
    let hike_detail = if crow <= LEG_MIN_M {
        zero_hike_at(elev, dest_lat, dest_lon)
    } else {
        resolve_hike(
            elev,
            park_lat,
            park_lon,
            dest_lat,
            dest_lon,
            max_slope_deg,
            hike_path_max_m,
            profile_sample_m,
            true,
        )
        .ok_or(CatalogBuildDrop::Slope)?
    };
    let jeep_detail = profile_along_polyline(
        elev,
        &route.coords,
        profile_sample_m,
        &route.road_segments,
    )
    .ok_or(CatalogBuildDrop::Jeep)?;
    let park_paved = jeep_detail.horiz_m <= LEG_MIN_M;
    Ok(BuiltAccess {
        mode: classify_access_mode(park_paved, hike_detail.horiz_m),
        road_lat: park_lat,
        road_lon: park_lon,
        road_m: crow,
        paved_lat: route.paved_lat,
        paved_lon: route.paved_lon,
        jeep_m: jeep_detail.horiz_m,
        hike_m: hike_detail.hike_m_3d,
        max_slope_deg: hike_detail.max_slope_deg,
        hike: stored_peak_hike(&hike_detail),
        jeep: stored_peak_jeep(&jeep_detail),
        hike_detail,
    })
}

/// Site/draft fallback when the loaded graph has no paved anchors.
pub fn build_access_from_park_display<E: HikeSampleElev>(
    elev: &E,
    routing: &OsmRouting,
    picked: &ParkHike,
    jeep_search_m: f64,
    profile_sample_m: f64,
) -> BuiltAccess {
    let mode = classify_access_mode(picked.park_paved, picked.hike.horiz_m);
    let (paved_lat, paved_lon, jeep_m, jeep) = if mode.needs_jeep() {
        jeep_leg_from_park_display(elev, routing, picked, jeep_search_m, profile_sample_m)
    } else {
        let detail = zero_jeep_at(elev, picked.road_lat, picked.road_lon, profile_sample_m)
            .expect("zero jeep at park");
        (
            picked.road_lat,
            picked.road_lon,
            0.0,
            stored_peak_jeep(&detail),
        )
    };

    let hike_detail = if mode.needs_hike() {
        picked.hike.clone()
    } else {
        zero_hike_at(elev, picked.road_lat, picked.road_lon)
    };

    BuiltAccess {
        mode,
        road_lat: picked.road_lat,
        road_lon: picked.road_lon,
        road_m: picked.road_m,
        paved_lat,
        paved_lon,
        jeep_m,
        hike_m: hike_detail.hike_m_3d,
        max_slope_deg: hike_detail.max_slope_deg,
        hike: stored_peak_hike(&hike_detail),
        jeep,
        hike_detail,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::hike::{default_max_slope_deg, HikeSampleElev, DEFAULT_MAX_HIKE_M};
    use crate::hike_route::resolve_hike;
    use crate::osm::{
        test_index_from_points, GraphEdge, JeepRoadGraph, OsmRouting, PavedAnchorIndex,
    };
    use crate::park::nearest_park_hike;
    use peaky_preset::{DEFAULT_HIKE_PATH_MAX_M, DEFAULT_PLACE_ROAD_SEARCH_M};

    struct FlatElev(f64);

    impl HikeSampleElev for FlatElev {
        fn sample_elev_m(&self, _lat: f64, _lon: f64) -> f64 {
            self.0
        }
    }

    fn empty_routing(roads: crate::osm::JeepRoadIndex) -> OsmRouting {
        OsmRouting {
            jeep_roads: roads,
            graph: JeepRoadGraph::new(),
            paved: PavedAnchorIndex::test_from_points(&[]),
        }
    }

    #[test]
    fn classify_four_modes() {
        assert_eq!(
            classify_access_mode(true, 0.0),
            AccessMode::Neither
        );
        assert_eq!(
            classify_access_mode(true, 200.0),
            AccessMode::HikeOnly
        );
        assert_eq!(
            classify_access_mode(false, 0.0),
            AccessMode::JeepOnly
        );
        assert_eq!(
            classify_access_mode(false, 500.0),
            AccessMode::Both
        );
    }

    #[test]
    fn display_reaches_farther_road_than_catalog_search() {
        let roads = test_index_from_points(&[(38.0, -117.0)]);
        let dest_lat = 38.018;
        let dest_lon = -117.0;
        let elev = FlatElev(2000.0);
        let catalog_search = DEFAULT_HIKE_PATH_MAX_M;
        assert!(
            nearest_park_hike(
                &elev,
                &roads,
                None,
                dest_lat,
                dest_lon,
                catalog_search,
                default_max_slope_deg(),
                catalog_search,
                30.0,
                false,
            )
            .is_none(),
            "1 mi road search should miss ~2 km jeep road"
        );
        let picked = nearest_park_hike(
            &elev,
            &roads,
            None,
            dest_lat,
            dest_lon,
            DEFAULT_PLACE_ROAD_SEARCH_M,
            default_max_slope_deg(),
            DEFAULT_PLACE_ROAD_SEARCH_M,
            30.0,
            false,
        )
        .expect("20 km display search should reach ~2 km road");
        assert!(picked.road_m > catalog_search);
        assert!(picked.hike.horiz_m > 100.0);
    }

    #[test]
    fn display_without_paved_still_builds() {
        let road = (38.004, -117.0);
        let dest = (38.0045, -117.0);
        let roads = test_index_from_points(&[road]);
        let elev = FlatElev(2000.0);
        let picked = nearest_park_hike(
            &elev,
            &roads,
            None,
            dest.0,
            dest.1,
            DEFAULT_PLACE_ROAD_SEARCH_M,
            default_max_slope_deg(),
            DEFAULT_PLACE_ROAD_SEARCH_M,
            30.0,
            false,
        )
        .expect("park pick");
        let routing = empty_routing(roads);
        let built = build_access_from_park_display(
            &elev,
            &routing,
            &picked,
            DEFAULT_PLACE_ROAD_SEARCH_M,
            30.0,
        );
        assert!(built.hike_m > 0.0);
        assert_eq!(built.paved_lat, picked.road_lat);
    }

    #[test]
    fn display_steep_hike_stored() {
        let roads = test_index_from_points(&[(38.0, -117.0)]);
        let peak_lat = 38.003;
        let peak_lon = -117.0;
        let (rlat, rlon, _) = roads
            .nearest_within(peak_lat, peak_lon, DEFAULT_MAX_HIKE_M)
            .unwrap();
        struct SteepRamp {
            base_lat: f64,
        }
        impl HikeSampleElev for SteepRamp {
            fn sample_elev_m(&self, lat: f64, _lon: f64) -> f64 {
                (lat - self.base_lat) * 45_000.0
            }
        }
        let ramp = SteepRamp { base_lat: 38.0 };
        let detail = resolve_hike(
            &ramp,
            rlat,
            rlon,
            peak_lat,
            peak_lon,
            default_max_slope_deg(),
            DEFAULT_PLACE_ROAD_SEARCH_M,
            30.0,
            false,
        )
        .expect("ungated display keeps steep hike");
        assert!(detail.max_slope_deg > default_max_slope_deg() + 0.5);
    }

    #[test]
    fn catalog_requires_paved_approach() {
        let mut routing = paved_to_track_plus_orphan_dirt();
        routing.paved = PavedAnchorIndex::from_nodes(&routing.graph, &[(0, 38.0, -117.0)]);
        let dest = (38.019, -117.0);
        let elev = FlatElev(2000.0);
        let extreme = peaky_preset::JeepExtremeBands::default();
        assert!(build_access_catalog(
            &elev,
            &routing,
            dest.0,
            dest.1,
            &extreme,
            default_max_slope_deg(),
            DEFAULT_HIKE_PATH_MAX_M,
            30.0,
        )
        .is_ok());
        let no_paved = empty_routing(test_index_from_points(&[(38.004, -117.0)]));
        assert!(build_access_catalog(
            &elev,
            &no_paved,
            dest.0,
            dest.1,
            &extreme,
            default_max_slope_deg(),
            DEFAULT_HIKE_PATH_MAX_M,
            30.0,
        )
        .is_err());
    }

    fn paved_to_track_plus_orphan_dirt() -> OsmRouting {
        let mut graph = JeepRoadGraph::new();
        let n0 = graph.insert_node(38.0, -117.0);
        let n1 = graph.insert_node(38.012, -117.0);
        let n2 = graph.insert_node(38.018, -117.0);
        let len = haversine_m(38.0, -117.0, 38.012, -117.0);
        graph.add_edge(
            n0,
            GraphEdge {
                to: n1,
                length_m: len,
                highway: "track".into(),
                tracktype: None,
                surface: None,
                fourwd_only: false,
            },
        );
        graph.add_edge(
            n1,
            GraphEdge {
                to: n0,
                length_m: len,
                highway: "track".into(),
                tracktype: None,
                surface: None,
                fourwd_only: false,
            },
        );
        let _ = n2;
        OsmRouting {
            jeep_roads: test_index_from_points(&[(38.012, -117.0), (38.018, -117.0)]),
            graph,
            paved: PavedAnchorIndex::from_nodes(
                &JeepRoadGraph::new(),
                &[(n0, 38.0, -117.0)],
            ),
        }
    }

    #[test]
    fn display_jeeps_from_paved_not_orphan_dirt() {
        let mut routing = paved_to_track_plus_orphan_dirt();
        routing.paved = PavedAnchorIndex::from_nodes(&routing.graph, &[(0, 38.0, -117.0)]);
        let dest = (38.019, -117.0);
        let elev = FlatElev(2000.0);
        let built = build_access_from_paved_approach(
            &elev,
            &routing,
            dest.0,
            dest.1,
            DEFAULT_PLACE_ROAD_SEARCH_M,
            DEFAULT_PLACE_ROAD_SEARCH_M,
            30.0,
        )
        .expect("paved approach");
        assert!(built.jeep_m > 1000.0);
        assert!((built.paved_lat - 38.0).abs() < 0.0002);
        assert!((built.road_lat - 38.012).abs() < 0.0002);
        assert!(built.hike_m > 500.0);
    }

    #[test]
    fn paved_approach_none_without_paved_anchors() {
        let roads = test_index_from_points(&[(38.004, -117.0)]);
        let routing = empty_routing(roads);
        let elev = FlatElev(2000.0);
        assert!(build_access_from_paved_approach(
            &elev,
            &routing,
            38.0045,
            -117.0,
            DEFAULT_PLACE_ROAD_SEARCH_M,
            DEFAULT_PLACE_ROAD_SEARCH_M,
            30.0,
        )
        .is_none());
    }
}
