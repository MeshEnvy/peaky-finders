//! Peaky preset YAML schema, I/O, and path resolution.

pub mod access;
pub mod compute_key;
pub mod home;
pub mod init;
pub mod io;
pub mod model;
pub mod paths;
pub mod peaks;
pub mod project;
pub mod viewshed_quality;
pub mod sites;
pub mod yaml_io;

pub use home::{
    environment_catalog_from_preset, load_environment_catalog, load_modem_catalog,
    modem_catalog_from_preset,
};

pub use init::{
    default_meshcore_preset, ensure_project_initialized, project_needs_init,
};

pub use access::{
    access_dir, access_meta_path, access_path, delete_access, ensure_access_meta,
    list_access_slugs, load_access, load_access_meta, upsert_access,
};
pub use compute_key::{
    access_compute_key, peak_access_compute_key, peak_row_compute_key, place_access_compute_key,
    place_access_has_profiles, place_access_is_fresh, ACCESS_ALGO_VERSION, ACCESS_PROFILE_SAMPLE_M,
    OSM_ROAD_SAMPLE_STEP_M, PEAK_ALGO_VERSION, PLACE_ACCESS_ROAD_SEARCH_M,
};

pub use peaks::{
    clean_peaks_catalog, denied_slugs, delete_peak, list_peak_slugs, load_peak, load_peaks_catalog,
    peaks_catalog_path, peaks_dir, place_slugs, preserve_denied_entries, upsert_peak,
    upsert_peak_with_access, write_peaks_catalog,
};
pub use io::{
    delete_land_source, import_land_source, insert_preset_site, land_source_id_for_path,
    load_preset, load_preset_raw, parse_preset_dict, patch_land_sidebar, patch_land_source,
    patch_land_source_last_updated,
    patch_preset_site, patch_preset_site_tags, patch_preset_sites_tags, preset_site_slugs,
    remove_preset_site, save_preset, write_preset_document,
};
pub use yaml_io::read_preset_document;
pub use model::{
    default_viewshed_polygon_style, drop_stale_seek_plan, resolved_coverage_max_workers,
    resolved_dem_fetch_max_workers,
    resolved_viewshed_polygon_style, slugify_files_segment, validate_preset,
    validate_project_preset_document, CoverageProvider, DisplayConfig, LandAttributeFilter,
    LandConfig, LandDownloadKind, LandLayerEntry, LandLayerRole, LandLayerStyle, LandLayerStyleValue,
    LandSidebar, LandSidebarFolder, LandSourceEntry, LandSourceRefresh, AccessMeta, PeakAccessRules,
    PeakCatalogEntry, PeakHikeGradeBucket, PeakHikeProfile, PeakHikeProfilePoint,
    PeakJeepProfile, PeakJeepProfilePoint, PeakJeepRoadSegment, PeaksCatalog, PlaceAccess,
    DEFAULT_MAX_JEEP_M, DEFAULT_PLACE_ROAD_SEARCH_M,
    Preset, PresetResult, PresetValidationError, SeekConfig,
    SeekPlan, SeekPlanHop, SimulationConfig,
    SimulationMaxWorkers, SiteEntry, ViewshedPolygonStyle, DEFAULT_COVERAGE_MAX_WORKERS,
    DEFAULT_DEM_MAX_WORKERS,
};
pub use viewshed_quality::{
    dem_native_raster_dimension, effective_radius_m, effective_target_raster_dimension,
    effective_viewshed_quality, preset_radius_km, preset_radius_m, raster_upgrade_ladder,
    validate_viewshed_quality, viewshed_raster_for_quality, SKADI_DEM_SPACING_M,
    VIEWSHED_QUALITY_MAX, VIEWSHED_QUALITY_MIN, VIEWSHED_RASTER_MAX, VIEWSHED_RASTER_MIN,
};
pub use paths::{
    peaky_home, peaky_projects_dir, resolve_preset_path, resolve_project_dir,
    resolved_preset_cache_dir,
    resolved_preset_slug, resolved_skadi_mirror_dir, resolved_skadi_mirror_dir_for_project,
    resolved_viewshed_root, workspace_root,
};
pub use sites::{
    normalize_site_tags, site_row_from_entry, site_row_from_yaml_ent, slugify, unique_place_slug,
    unique_site_slug, validate_coords, validate_site_height,
};
