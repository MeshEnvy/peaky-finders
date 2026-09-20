//! Peak catalog candidate filter outcomes.

use peaky_preset::{AccessProbe, PeakHikeProfile, PeakJeepProfile};

use crate::hike::HikeProfileDetailed;
use crate::universe::RawCandidate;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FilterDrop {
    Land,
    Region,
    Road,
    Hike,
    Slope,
    Jeep,
}

#[derive(Debug, Clone)]
pub struct FilterPass {
    pub cand: RawCandidate,
    pub peak_lat: f64,
    pub peak_lon: f64,
    pub peak_elev: f64,
    pub road_lat: f64,
    pub road_lon: f64,
    pub road_m: f64,
    pub hike_m_3d: f64,
    pub max_slope_deg: f64,
    pub hike: PeakHikeProfile,
    pub hike_detail: HikeProfileDetailed,
    pub paved_lat: f64,
    pub paved_lon: f64,
    pub jeep_m: f64,
    pub jeep: PeakJeepProfile,
    pub trail_lat: f64,
    pub trail_lon: f64,
    pub trail_m: f64,
    pub trail: PeakHikeProfile,
    pub probe: AccessProbe,
}
