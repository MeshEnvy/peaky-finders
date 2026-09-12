//! `peaky map export` — splatter overlay for meshenvy.org /map.

use std::path::PathBuf;

use anyhow::Result;
use peaky_map::{run_map_export, MapExportOptions};

pub fn run(
    project: PathBuf,
    out_dir: PathBuf,
    workers: Option<usize>,
    merge_resolution_deg: f64,
    close_iterations: u32,
    tile_zoom_min: u32,
    tile_zoom_max: u32,
    tile_workers: Option<u32>,
    skip_tiles: bool,
    silver_base_step_m: f64,
    verbose: bool,
) -> Result<()> {
    let mut opts = MapExportOptions {
        merge_resolution_deg,
        close_iterations,
        tile_zoom_min,
        tile_zoom_max,
        skip_tiles,
        silver_base_step_m,
        verbose,
        ..MapExportOptions::default()
    };
    if let Some(w) = workers {
        opts.workers = w.max(1);
    }
    if let Some(w) = tile_workers {
        opts.tile_workers = w.max(1);
    }

    let result = run_map_export(&project, &out_dir, &opts)?;
    eprintln!(
        "Wrote {} tiles_dir={} tx={} (viewshed_run={} viewshed_cached={})",
        result.geojson_path.display(),
        result.tiles_dir.display(),
        result.fleet_site_count,
        result.viewshed_runs,
        result.viewshed_cache_hits,
    );
    Ok(())
}
