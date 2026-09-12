//! Assemble jeep + hike legs from a park pick (four access modes).

use crate::hike::{stored_peak_hike, zero_hike_at, HikeSampleElev};
use crate::jeep::{
    profile_along_polyline, route_jeep_detailed, stored_peak_jeep, JeepProfileDetailed,
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
}

fn zero_jeep_at<E: HikeSampleElev>(
    elev: &E,
    lat: f64,
    lon: f64,
    sample_m: f64,
) -> Option<JeepProfileDetailed> {
    profile_along_polyline(elev, &[(lat, lon)], sample_m, &[])
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
        let route = route_jeep_detailed(
            &routing.graph,
            &routing.paved,
            picked.road_lat,
            picked.road_lon,
            max_jeep_m,
        )?;
        let detail = profile_along_polyline(
            elev,
            &route.coords,
            profile_sample_m,
            &route.road_segments,
        )?;
        (route.paved_lat, route.paved_lon, detail.horiz_m, stored_peak_jeep(&detail))
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
    })
}

#[cfg(test)]
mod tests {
    use super::*;

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
}
