//! Fresnel + FSPL coverage engine.

pub mod eligible_mask;
pub mod dem;
pub mod dem_mirror;
pub mod dem_tile_cache;
pub mod dem_tiles;
pub mod engine;
pub mod hash;
pub mod kml;
pub mod lora;
pub mod peak_links;
pub mod peaks;
pub mod ppm;
pub mod progress_log;
pub mod propagate;
pub mod ray_cache;
pub mod session;
pub mod skadi_fetch;

pub use dem_mirror::{
    priority_label, AoiTileStats, DemMirror, PRIORITY_DEM_BACKGROUND, PRIORITY_DEM_BLOCKING,
    PRIORITY_DEM_MAP, PRIORITY_DEM_VIEWPORT,
};
pub use hash::{splat_input_sha256, Request as CovRequest, SPLAT_CACHE_SCHEMA_VERSION};
pub use peaks::LandFilterIndex;
pub use session::Session;
