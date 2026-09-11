//! Eligible-peaks catalog builder for ``peaky peaks``.

mod corridor;
mod gnis;
mod hike;
mod osm;
mod run;
mod universe;

pub use hike::{HikeProfile, HikeSampleElev};
pub use osm::{JeepRoadIndex, JEEP_HIGHWAY_TAGS};
pub use corridor::{
    corridor_polygon, parse_corridor_coords, resolve_corridor_sites, MI_TO_M,
};
pub use run::{
    build_peaks_catalog, resolve_corridor_from_opts, PeaksBuildOptions, PeaksBuildSummary,
};
