//! Eligible-peaks catalog builder for ``peaky peaks``.

mod access;
mod access_probe;
mod access_warm;
mod catalog_filter;
mod corridor;
mod gnis;
mod gtlf;
mod hike;
mod hike_route;
mod jeep;
mod network_geojson;
mod network_index;
mod osm;
mod osm_cache;
mod park;
mod probe;
mod road_inventory;
mod roads_merge;
mod roads_patch;
mod roads_pipeline;
mod run;
mod segment_groups;
mod trail;
mod universe;

pub use access_probe::{
    access_route_uses_custom, append_draft_site_access_options, build_site_access_options,
    drop_early_jeep_stops, select_distinct_park_nodes, site_access_option_id,
    SITE_ACCESS_OPTIONS_REV,
};
pub use access_probe::{
    built_to_access_route, compute_budget_report, filter_pass_from_probe, probe, probe_drop_reason,
    probe_explain, AccessContext, ProbeCandidateReport,
};
pub use access_warm::{
    compute_pin_routes, compute_place_access, ensure_site_access_options_on_probe,
    load_probe_cache, load_routing_for_bbox, load_routing_for_bbox_progress,
    load_routing_for_points, load_routing_for_points_progress, load_routing_near, probe_cache_path,
    project_gtlf_fingerprint, project_pbf_fingerprint, repatch_project_routing, resolve_probe,
    routing_bbox_for_points, routing_coverage_bbox, save_probe_cache, warm_place_access,
    warm_place_access_with_routing, warm_trusted_reach_trees, ProjectRouting, RoutingTrees,
    SessionElev, OSM_ROUTE_PAD_DEG,
};
pub use catalog_filter::{FilterDrop, FilterPass};
pub use corridor::{corridor_polygon, parse_corridor_coords, resolve_corridor_sites, MI_TO_M};
pub use gtlf::{
    apply_gtlf_to_routing, ensure_gtlf_cache, gtlf_cache_dir, gtlf_cache_fingerprint,
    load_gtlf_display_inventory, read_gtlf_inventory_geojson, GtlfBundle,
};
pub use hike::{
    default_max_slope_deg, format_hike_report, grade_pct_to_deg, hike_difficulty,
    profile_hike_detailed, stored_peak_hike, HikeProfile, HikeProfileDetailed, HikeSampleElev,
};
pub use hike_route::{profile_polyline_detailed, resolve_hike, route_hike_coords};
pub use jeep::{
    profile_along_polyline, route_jeep_closest_approach, route_jeep_detailed, stored_peak_jeep,
    JeepProfileDetailed, PavedReachTree,
};
pub use network_geojson::{
    osm_routing_opts_from_preset, routing_network_drive_geojson_bytes,
    routing_network_layer_geojson, routing_network_layer_geojson_bytes, NetworkLayer,
    NetworkViewBBox,
};
pub use network_index::NetworkViewCacheKey;
pub use osm::{
    build_osm_routing, duplicate_osm_routing, ensure_osm_pbf, osm_routing_opts_with_roads,
    JeepRoadIndex, OsmParsePhase, OsmRouting, OsmRoutingBuild, OsmRoutingOpts, JEEP_HIGHWAY_TAGS,
    OSM_BUILD_PAD_DEG, PAVED_HIGHWAY_TAGS,
};
pub use osm_cache::{
    invalidate_osm_routing_caches, save_merged_routing_cache, save_osm_routing_cache,
    save_reach_trees_cache, try_load_merged_routing_cache, try_load_osm_routing_cache,
    try_load_reach_trees_cache, try_load_road_inventory_cache,
};
pub use peaky_preset::{DEFAULT_MAX_JEEP_M as PLACE_ACCESS_MAX_JEEP_M, PLACE_ACCESS_ROAD_SEARCH_M};
pub use probe::{probe_access_point, probe_access_point_explain, probe_peak, probe_peaks_region};
pub use road_inventory::{
    classify_osm_inventory_from_opts, inventory_all_geojson, inventory_all_geojson_bytes,
    inventory_other_geojson, inventory_other_geojson_bytes, GtlfRoadInventory, InventoryRole,
    OsmRoadInventory,
};
pub use roads_patch::{
    apply_roads_book_patch, osm_routing_base_for_cache, patch_preserves_trusted_reach,
    refresh_after_roads_book_save, refresh_inventory_roles, strip_custom_drive_segments,
};
pub use roads_pipeline::{
    build_project_routing_from_master, file_fingerprint, update_all_sources, update_roads,
    RoadSegment, SourceFetch,
};
pub use run::{
    build_peaks_catalog, resolve_corridor_from_opts, PeaksBuildOptions, PeaksBuildSummary,
};
pub use trail::{
    build_trail_graph, route_trail_closest_approach, trail_park_candidates, TrailRouting,
    TRAIL_MAX_M, TRAIL_PARK_SNAP_M,
};
