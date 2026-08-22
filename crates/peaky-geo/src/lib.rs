//! GeoJSON land layers, eligible geometry, KML import, and PPM polygonize.

pub mod eligible_land;
pub mod kml_import;
pub mod land_aoi_clip;
pub mod land_boot;
pub mod land_cache;
pub mod land_fetch;
pub mod land_filter;
pub mod land_gdb;
pub mod land_path;
pub mod land_query;
pub mod land_refresh;
pub mod land_validate;
pub mod land_validate_cache;
pub mod polygonize;

pub use eligible_land::{
    aoi_land_digest, build_eligible_geometry, build_eligible_land_union,
    eligible_land_dem_mask_dir, eligible_land_digest, intersect_land_geometry,
    iter_land_layer_entries_by_role, land_geometry_is_empty, load_or_build_eligible_land_filter,
    load_or_build_eligible_land_union, load_preset_aoi_union, EligibleLandError,
};
pub use land_aoi_clip::{
    apply_aoi_clip_to_geojson, clip_geojson_to_aoi, layer_skips_aoi_clip,
};
pub use kml_import::{
    parse_kml_linestring_routes, parse_kml_point_placemarks, parse_kmz_linestring_routes,
    parse_kmz_point_placemarks, serialize_kml_point, KmlLineRoute, KmlPointSite, KmlRoute,
    KmlRouteWaypoint,
};
pub use land_filter::{
    filter_geojson_preview, json_value_as_compare_string, matches_land_attribute_filters,
    property_matches_filter, transform_geojson_for_layer,
};
pub use land_gdb::{
    layer_field_values, layer_fields, list_land_data_gdbs, list_preview_layers,
    preview_layers_json, read_layer_geojson_value, resolve_land_data_path, LandFieldValues,
    LandPreviewField, LandPreviewLayer,
};
pub use land_path::{
    is_land_geojson_path, resolve_land_layer_geojson_path, resolve_land_source_path,
};
pub use land_query::{
    load_land_layer_index, point_hits, query_land_layer_index, resolve_land_layer_entry,
    LandLayerSpatialIndex, LandPointHit,
};
pub use land_refresh::{
    audit_land_refresh_for_preset, prepare_land_at_boot, LandRefreshFailure, LandRefreshReport,
    LandRefreshRunSummary, LandRefreshStatusKind, LandSourceRefreshRow,
};
pub use land_validate::validate_land_source;
pub use polygonize::{
    coverage_mask_from_splat_ppm_rgb, fraction_to_lat_lon, pixel_to_lat_lon,
    polygonize_ppm_coverage, polygonize_ppm_coverage_bbox, read_ppm_rgb, write_ppm_rgb,
    LatLonBox,
};
