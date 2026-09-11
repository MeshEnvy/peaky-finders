//! Eligible-peaks catalog builder for ``peaky peaks``.

mod corridor;
mod gnis;
mod hike;
mod jeep;
mod osm;
mod run;
mod universe;

pub use hike::{
    format_hike_report, grade_pct_to_deg, hike_difficulty, stored_peak_hike, HikeProfile,
    HikeProfileDetailed,
    HikeSampleElev, profile_hike_detailed,
};
pub use jeep::{profile_along_polyline, route_jeep_detailed, stored_peak_jeep, JeepProfileDetailed};
pub use osm::{build_osm_routing, JeepRoadIndex, OsmRouting, JEEP_HIGHWAY_TAGS, PAVED_HIGHWAY_TAGS};
pub use corridor::{
    corridor_polygon, parse_corridor_coords, resolve_corridor_sites, MI_TO_M,
};
pub use run::{
    build_peaks_catalog, resolve_corridor_from_opts, PeaksBuildOptions, PeaksBuildSummary,
};
