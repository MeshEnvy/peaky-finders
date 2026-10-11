//! Static map overlay export (splatter viewsheds → GeoJSON metadata + XYZ tiles).

mod export;
mod fleet;
mod gdal;
mod geojson;
mod merge;
mod meshenvy;

pub use export::{run_map_export, MapExportOptions, MapExportResult};
pub use fleet::{load_fleet_sites, FleetSite};
pub use meshenvy::{nevada_empty_overlay_bbox, NEVADA_HUB_VERTICES};
