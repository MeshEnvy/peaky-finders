//! Peaky preset YAML schema, I/O, and path resolution.

pub mod boards;
pub mod compute_key;
pub mod difficulty;
pub mod home;
pub mod init;
pub mod io;
pub mod model;
pub mod nodes_board;
pub mod paths;
pub mod peaks;
pub mod peaks_index;
pub mod project;
pub mod roads_book;
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

pub use difficulty::{
    access_difficulty_from_stored, classify_hike, classify_jeep, derived_hike_difficulty,
    derived_jeep_difficulty, difficulty_label, hike_difficulty, jeep_class_reason, jeep_difficulty,
    jeep_road_rank, stored_access_difficulty, stored_hike_difficulty, stored_jeep_difficulty,
    worse_difficulty, JeepClassReason,
    HIKE_DIFFICULT_MIN_AVG_GRADE_PCT, HIKE_DIFFICULT_MIN_GAIN_M, HIKE_EXTREME_MIN_AVG_GRADE_PCT,
    HIKE_EXTREME_MIN_GAIN_M, HIKE_MEDIUM_MIN_AVG_GRADE_PCT, HIKE_MEDIUM_MIN_GAIN_M,
    HIKE_MEDIUM_MIN_MAX_GRADE_PCT,
};
pub use compute_key::{
    probe_key, probe_point_key, probe_prefs_key, ACCESS_ALGO_VERSION, ACCESS_PROFILE_SAMPLE_M,
    OSM_ROAD_SAMPLE_STEP_M, PEAK_ALGO_VERSION, PLACE_ACCESS_ROAD_SEARCH_M,
};

pub use peaks_index::{
    build_peaks_list_json, gc_stale_cache_dirs, index_row_from_probe, list_stale_cache_dirs,
    load_latest_peaks_index,
    load_peak_overrides, load_peaks_index, near_denied, place_slugs, prefs_key_for_project,
    write_peak_overrides, write_peaks_index, PeakIndexRow, PeakKind, PeakOverride,
    PeakOverridesFile, PeaksIndex, PeaksManifest, StaleCacheDir,
};
pub use peaks::{PeakCatalogEntry, PeaksCatalog};

pub use io::{
    delete_land_source, import_land_source, insert_preset_site, land_source_id_for_path,
    load_preset, load_preset_raw, load_site_entry, parse_preset_dict, patch_land_sidebar,
    patch_land_source,
    patch_land_source_last_updated,
    patch_preset_site, patch_preset_site_tags, patch_preset_sites_tags, preset_site_slugs,
    remove_preset_site, save_preset, write_preset_document,
};
pub use yaml_io::read_preset_document;
pub use boards::{
    board_viewshed_radius_km, load_boards_catalog, load_boards_catalog_from_path,
    reject_legacy_simulation_boards, site_viewshed_radius_km, validate_board_radius_km,
    validate_boards_catalog, BoardConfig, BoardViewshedResolver, BoardsCatalog,
};
pub use model::{
    default_viewshed_polygon_style, resolved_coverage_max_workers,
    resolved_dem_fetch_max_workers,
    resolved_viewshed_polygon_style, slugify_files_segment, validate_preset,
    validate_project_preset_document, CoverageProvider, DisplayConfig,
    LandAttributeFilter, LandConfig, LandDownloadKind, LandLayerEntry, LandLayerRole,
    LandLayerStyle, LandLayerStyleValue, LandSidebar, LandSidebarFolder, LandSourceEntry,
    LandSourceRefresh, AccessMeta, AccessPreferenceConfig, AccessProbe, AccessRoute,
    BudgetLegReport, PeakAccessRules, PeakHikeGradeBucket, PeakHikeProfile,
    PeakHikeProfilePoint, PeakJeepLinePoint, PeakJeepProfile, PeakJeepProfilePoint,
    jeep_budget_class_key,
    PeakJeepRoadSegment, PlaceAccess, RouteBudgetReport, SiteAccessOption,
    resolve_site_display_route, DEFAULT_MAX_JEEP_M,
    DEFAULT_HIKE_PATH_MAX_M,
    DEFAULT_GTLF_MAPSERVER, DEFAULT_OSM_PBF_URL, DEFAULT_PLACE_ROAD_SEARCH_M, GnisSourceConfig,
    GtlfSourceConfig, OsmSourceConfig,
    HikeDifficultyBands, HikeDifficultBands, HikeExtremeBands, HikeMediumBands, JeepDifficultBands,
    JeepDifficultyBands, JeepExtremeBands, JeepMediumBands, PeaksDifficultyConfig, PeaksScanConfig,
    PeaksSeedConfig, Preset, PresetResult, PresetValidationError, ScanConfig,
    SourcesConfig,
    SimulationConfig, SimulationMaxWorkers, SiteEntry, ViewshedPolygonStyle,
    DEFAULT_COVERAGE_MAX_WORKERS, DEFAULT_DEM_MAX_WORKERS,
};
pub use nodes_board::{load_nodes_board_index, load_nodes_board_index_from_path, NodesBoardIndex};
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
pub use roads_book::{
    ensure_roads_book, gtlf_coord_hash, load_roads_book, roads_fingerprint,
    roads_global_fingerprint, roads_local_fingerprint, roads_path_for_config,
    save_roads_book, seed_roads_book, GtlfOverrideKey, RoadClassRow, RoadCustom, RoadRole,
    RoadSurfaceMatch, RoadsBook, SegmentOverride, SegmentOverrideKey,
};
pub use sites::{
    normalize_site_tags, site_row_from_entry, site_row_from_yaml_ent, slugify, unique_place_slug,
    unique_site_slug, validate_coords, validate_site_height,
};
