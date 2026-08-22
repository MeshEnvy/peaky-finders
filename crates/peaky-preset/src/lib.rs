//! Peaky preset YAML schema, I/O, and path resolution.

pub mod home;
pub mod io;
pub mod model;
pub mod paths;
pub mod viewshed_quality;
pub mod sites;

pub use home::{
    environment_catalog_from_preset, load_environment_catalog, load_modem_catalog,
    modem_catalog_from_preset,
};

pub use io::{
    delete_land_source, import_land_source, insert_preset_site, land_source_id_for_path,
    load_preset, load_preset_raw, parse_preset_dict, patch_land_sidebar, patch_land_source,
    patch_land_source_last_updated,
    patch_preset_site, patch_preset_site_tags, patch_preset_sites_tags, preset_site_slugs,
    read_preset_document, remove_preset_site, save_preset, write_preset_document,
};
pub use model::{
    default_viewshed_polygon_style, resolved_coverage_dispatcher_max_workers,
    resolved_viewshed_polygon_style, slugify_files_segment, validate_preset,
    validate_project_preset_document, CoverageProvider, DisplayConfig, LandAttributeFilter,
    LandConfig, LandDownloadKind, LandLayerEntry, LandLayerRole, LandLayerStyle, LandLayerStyleValue,
    LandSidebar, LandSidebarFolder, LandSourceEntry, LandSourceRefresh, Preset, PresetResult,
    PresetValidationError, SeekConfig, SeekPlan, SeekPlanHop, SimulationConfig,
    SimulationMaxWorkers, SiteEntry, ViewshedPolygonStyle,
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
    normalize_site_tags, site_row_from_entry, site_row_from_yaml_ent, slugify, unique_site_slug,
    validate_coords, validate_site_height,
};
