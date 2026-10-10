//! Eligible-peaks catalog builder for ``peaky peaks``.

mod access;
mod access_probe;
mod access_warm;
mod catalog_filter;
mod corridor;
mod gtlf;
mod gnis;
mod hike;
mod hike_route;
mod jeep;
mod osm;
mod osm_cache;
mod park;
mod probe;
mod run;
mod trail;
mod network_geojson;
mod network_index;
mod road_inventory;
mod segment_groups;
mod universe;

pub use access_warm::{
    compute_place_access, load_probe_cache, load_routing_for_bbox, load_routing_for_bbox_progress,
    load_routing_for_points, load_routing_for_points_progress, load_routing_near,
    probe_cache_path, project_gtlf_fingerprint, project_pbf_fingerprint, resolve_probe,
    routing_bbox_for_points,
    routing_coverage_bbox, save_probe_cache, ensure_site_access_options_on_probe,
    warm_place_access, warm_place_access_with_routing, ProjectRouting, RoutingTrees,
    SessionElev, OSM_ROUTE_PAD_DEG,
};
pub use access_probe::{
    access_route_uses_custom, append_draft_site_access_options, build_site_access_options,
    drop_early_jeep_stops, select_distinct_park_nodes, site_access_option_id,
    SITE_ACCESS_OPTIONS_REV,
};
pub use catalog_filter::{FilterDrop, FilterPass};
pub use gtlf::{
    apply_gtlf_to_routing, ensure_gtlf_cache, gtlf_cache_dir, gtlf_cache_fingerprint,
    load_gtlf_display_inventory, read_gtlf_inventory_geojson, GtlfBundle,
};
pub use road_inventory::{
    inventory_all_geojson, inventory_all_geojson_bytes, classify_osm_inventory_from_opts,
    InventoryRole, OsmRoadInventory, GtlfRoadInventory,
};
pub use trail::{
    build_trail_graph, route_trail_closest_approach, trail_park_candidates, TrailRouting,
    TRAIL_MAX_M, TRAIL_PARK_SNAP_M,
};
pub use network_geojson::{
    osm_routing_opts_from_preset, routing_network_drive_geojson_bytes,
    routing_network_layer_geojson, routing_network_layer_geojson_bytes, NetworkLayer,
    NetworkViewBBox,
};
pub use network_index::NetworkViewCacheKey;
pub use peaky_preset::{PLACE_ACCESS_ROAD_SEARCH_M, DEFAULT_MAX_JEEP_M as PLACE_ACCESS_MAX_JEEP_M};
pub use hike::{
    default_max_slope_deg, format_hike_report, grade_pct_to_deg, hike_difficulty, stored_peak_hike,
    HikeProfile, HikeProfileDetailed, HikeSampleElev, profile_hike_detailed,
};
pub use hike_route::{profile_polyline_detailed, resolve_hike, route_hike_coords};
pub use access_probe::{
    built_to_access_route, compute_budget_report, filter_pass_from_probe, probe, probe_drop_reason,
    probe_explain, AccessContext, ProbeCandidateReport,
};
pub use jeep::{
    profile_along_polyline, route_jeep_closest_approach, route_jeep_detailed, stored_peak_jeep,
    JeepProfileDetailed, PavedReachTree,
};
pub use osm::{
    build_osm_routing, ensure_osm_pbf, osm_routing_opts_with_roads, OsmRoutingBuild, JeepRoadIndex,
    OsmParsePhase, OsmRouting, OsmRoutingOpts,
    JEEP_HIGHWAY_TAGS, OSM_BUILD_PAD_DEG, PAVED_HIGHWAY_TAGS,
};
pub use osm_cache::{
    invalidate_osm_routing_caches, save_merged_routing_cache, save_osm_routing_cache,
    try_load_merged_routing_cache, try_load_osm_routing_cache, try_load_road_inventory_cache,
};
pub use corridor::{corridor_polygon, parse_corridor_coords, resolve_corridor_sites, MI_TO_M};
pub use probe::{
    probe_access_point, probe_access_point_explain, probe_peak, probe_peaks_region,
};
pub use run::{
    build_peaks_catalog, resolve_corridor_from_opts, PeaksBuildOptions, PeaksBuildSummary,
};
