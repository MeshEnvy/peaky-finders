//! Eligible-peaks catalog builder for ``peaky peaks``.

mod access;
mod access_warm;
mod corridor;
mod gnis;
mod hike;
mod hike_route;
mod jeep;
mod osm;
mod osm_cache;
mod park;
mod run;
mod universe;

pub use access_warm::{
    compute_place_access, load_routing_for_points, load_routing_for_points_progress,
    load_routing_near, routing_bbox_for_points, routing_coverage_bbox, warm_place_access,
    warm_place_access_with_routing, OSM_ROUTE_PAD_DEG,
};
pub use peaky_preset::{PLACE_ACCESS_ROAD_SEARCH_M, DEFAULT_MAX_JEEP_M as PLACE_ACCESS_MAX_JEEP_M};
pub use hike::{
    default_max_slope_deg, format_hike_report, grade_pct_to_deg, hike_difficulty, stored_peak_hike,
    HikeProfile, HikeProfileDetailed, HikeSampleElev, profile_hike_detailed,
};
pub use hike_route::{profile_polyline_detailed, resolve_hike, route_hike_coords};
pub use jeep::{profile_along_polyline, route_jeep_detailed, stored_peak_jeep, JeepProfileDetailed};
pub use osm::{
    build_osm_routing, JeepRoadIndex, OsmParsePhase, OsmRouting, OsmRoutingOpts, JEEP_HIGHWAY_TAGS,
    OSM_BUILD_PAD_DEG, PAVED_HIGHWAY_TAGS,
};
pub use osm_cache::{save_osm_routing_cache, try_load_osm_routing_cache};
pub use corridor::{corridor_polygon, parse_corridor_coords, resolve_corridor_sites, MI_TO_M};
pub use run::{
    build_peaks_catalog, resolve_corridor_from_opts, PeaksBuildOptions, PeaksBuildSummary,
};
