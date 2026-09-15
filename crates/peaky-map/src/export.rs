//! End-to-end map overlay export for meshenvy.org /map.

use std::path::{Path, PathBuf};
use std::sync::Arc;

use anyhow::{Context, Result};
use peaky_preset::{
    load_preset, resolved_coverage_max_workers, resolved_dem_fetch_max_workers,
    resolved_skadi_mirror_dir_for_project, resolved_viewshed_root, BoardViewshedResolver,
};
use peaky_serve::viewshed::ensure_viewshed_for_site_blocking;
use rayon::prelude::*;
use serde_json::json;
use splatter::Session;
use tracing::{info, warn};

use crate::fleet::{load_fleet_sites, FleetSite};
use crate::gdal::{
    georeference_splat_png, mosaic_viewsheds_3857, polygonize_alpha_geotiff, run_gdal2tiles,
    write_tile_sidecar,
};
use crate::geojson::{build_feature_collection, write_geojson};
use crate::hub_bridge::compute_hub_bridge_progress;
use crate::merge::{filter_inputs_to_coverage, union_manifest_bounds, SiteMaskInput};
use crate::meshenvy::{self, META_HUB_BRIDGE_KEY};

#[derive(Debug, Clone)]
pub struct MapExportOptions {
    pub workers: usize,
    pub merge_resolution_deg: f64,
    pub close_iterations: u32,
    pub tile_zoom_min: u32,
    pub tile_zoom_max: u32,
    pub tile_workers: u32,
    pub skip_tiles: bool,
    pub silver_base_step_m: f64,
    pub verbose: bool,
}

impl Default for MapExportOptions {
    fn default() -> Self {
        Self {
            workers: default_workers(),
            merge_resolution_deg: 0.00045,
            close_iterations: 0,
            tile_zoom_min: 6,
            tile_zoom_max: 11,
            tile_workers: default_tile_workers(),
            skip_tiles: false,
            silver_base_step_m: 400.0,
            verbose: false,
        }
    }
}

fn default_workers() -> usize {
    std::env::var("PEAKY_MAP_WORKERS")
        .ok()
        .and_then(|s| s.parse().ok())
        .filter(|n| *n > 0)
        .unwrap_or_else(|| {
            let n = std::thread::available_parallelism()
                .map(|p| p.get())
                .unwrap_or(4);
            n.clamp(1, 16)
        })
}

fn default_tile_workers() -> u32 {
    std::env::var("PEAKY_TILE_WORKERS")
        .ok()
        .and_then(|s| s.parse().ok())
        .filter(|n| *n > 0)
        .unwrap_or(4)
        .min(8) as u32
}

pub struct MapExportResult {
    pub fleet_site_count: usize,
    pub viewshed_runs: usize,
    pub viewshed_cache_hits: usize,
    pub geojson_path: PathBuf,
    pub tiles_dir: PathBuf,
}

pub fn run_map_export(
    project: &Path,
    out_dir: &Path,
    opts: &MapExportOptions,
) -> Result<MapExportResult> {
    let project_dir = peaky_preset::resolve_project_dir(&project.to_string_lossy());
    let preset_path = project_dir.join("config.yaml");
    let preset = load_preset(&preset_path)?;
    let board_viewshed = BoardViewshedResolver::load(&project_dir)
        .map_err(|e| anyhow::anyhow!("boards.yaml: {e}"))?;
    let hub_config = meshenvy::nevada_hub_bridge_config();
    let coverage_bbox = hub_config.bbox(0.5);

    let fleet = load_fleet_sites(&project_dir)?;
    let fleet_site_count = fleet.len();

    if fleet.is_empty() {
        warn!("no deployed fleet sites; writing empty overlay");
        return write_empty_overlay(
            &preset_path,
            &preset,
            out_dir,
            opts,
            fleet_site_count,
            &hub_config,
            coverage_bbox,
        );
    }

    let mirror = resolved_skadi_mirror_dir_for_project(&project_dir);
    std::fs::create_dir_all(&mirror)?;
    let dem_workers = resolved_dem_fetch_max_workers(&preset);
    let _coverage_workers = resolved_coverage_max_workers(&preset);
    let session = Arc::new(Session::new(mirror, opts.verbose, dem_workers));

    info!(
        "generating splatter viewsheds for {} fleet site(s) with {} worker(s)",
        fleet.len(),
        opts.workers
    );

    let pool = rayon::ThreadPoolBuilder::new()
        .num_threads(opts.workers)
        .build()
        .context("build rayon pool")?;

    let stats = std::sync::Mutex::new((0usize, 0usize));
    let inputs: Vec<SiteMaskInput> = pool.install(|| {
        fleet
            .par_iter()
            .filter_map(|fleet_site| {
                match ensure_site_viewshed(
                    session.clone(),
                    &preset_path,
                    &preset,
                    fleet_site,
                    &board_viewshed,
                    opts.verbose,
                )
                {
                    Ok((workdir, cache_hit)) => {
                        let mut guard = stats.lock().unwrap();
                        if cache_hit {
                            guard.1 += 1;
                        } else {
                            guard.0 += 1;
                        }
                        Some(SiteMaskInput {
                            png_path: workdir.join("splat.png"),
                            manifest_path: workdir.join("manifest.json"),
                        })
                    }
                    Err(e) => {
                        warn!("viewshed failed for {}: {e:#}", fleet_site.slug);
                        None
                    }
                }
            })
            .collect()
    });

    let (viewshed_runs, viewshed_cache_hits) = *stats.lock().unwrap();
    info!(
        "viewsheds ready: {} footprint(s) ({} generated, {} cache hits)",
        inputs.len(),
        viewshed_runs,
        viewshed_cache_hits
    );

    let filtered = filter_inputs_to_coverage(inputs, coverage_bbox);
    if filtered.is_empty() {
        warn!("no viewsheds inside coverage bbox; writing empty overlay");
        return write_empty_overlay(
            &preset_path,
            &preset,
            out_dir,
            opts,
            fleet_site_count,
            &hub_config,
            coverage_bbox,
        );
    }

    if opts.close_iterations > 0 {
        warn!(
            "close_iterations={} ignored (splatter export defaults gap-fill off)",
            opts.close_iterations
        );
    }

    let build_dir = out_dir.join(".build/viewsheds");
    if build_dir.exists() {
        std::fs::remove_dir_all(&build_dir)?;
    }
    std::fs::create_dir_all(&build_dir)?;

    let mut geotiffs = Vec::new();
    for (i, input) in filtered.iter().enumerate() {
        let dest = build_dir.join(format!("site-{i:02}.tif"));
        georeference_splat_png(&input.png_path, &input.manifest_path, &dest)?;
        geotiffs.push(dest);
    }

    let rgba3857 = out_dir.join(".build/coverage_rgba3857.tif");
    mosaic_viewsheds_3857(&geotiffs, &rgba3857)?;

    info!("polygonizing mosaic alpha for hub-bridge progress");
    let geoms = polygonize_alpha_geotiff(&rgba3857)?;
    let hub_bridge = compute_hub_bridge_progress(&geoms, &hub_config, opts.silver_base_step_m);

    let coverage_bbox = union_manifest_bounds(&filtered).unwrap_or(coverage_bbox);
    let overlay_meta = json!({
        "coverage_bbox": coverage_bbox,
        META_HUB_BRIDGE_KEY: hub_bridge,
        "coverage_tile_min_zoom": opts.tile_zoom_min,
        "coverage_tile_max_zoom": opts.tile_zoom_max,
    });

    let geojson_path = out_dir.join("coverage-network.geojson");
    let fc = build_feature_collection(fleet_site_count, &preset, overlay_meta)?;
    write_geojson(&fc, &geojson_path)?;

    let tiles_dir = out_dir.join("coverage-tiles");
    if !opts.skip_tiles {
        run_gdal2tiles(
            &rgba3857,
            &tiles_dir,
            opts.tile_zoom_min,
            opts.tile_zoom_max,
            opts.tile_workers,
        )?;
        write_tile_sidecar(&tiles_dir, coverage_bbox, opts.tile_zoom_max)?;
        info!("wrote coverage tiles under {}", tiles_dir.display());
    } else {
        warn!("skipping gdal2tiles (--skip-tiles)");
    }

    Ok(MapExportResult {
        fleet_site_count,
        viewshed_runs,
        viewshed_cache_hits,
        geojson_path,
        tiles_dir,
    })
}

fn ensure_site_viewshed(
    session: Arc<Session>,
    preset_path: &Path,
    preset: &peaky_preset::Preset,
    fleet_site: &FleetSite,
    board_viewshed: &BoardViewshedResolver,
    verbose: bool,
) -> Result<(PathBuf, bool)> {
    let digest = peaky_serve::viewshed::target_viewshed_digest_for_site(
        preset,
        &fleet_site.site,
        Some(board_viewshed),
    )?;
    let workdir = resolved_viewshed_root(preset_path).join(&digest);
    let png = workdir.join("splat.png");
    let cache_hit = png.is_file() && workdir.join("manifest.json").is_file();
    ensure_viewshed_for_site_blocking(
        session,
        preset_path,
        preset,
        &fleet_site.slug,
        &fleet_site.site,
        Some(board_viewshed),
        verbose,
    )?;
    Ok((workdir, cache_hit))
}

fn write_empty_overlay(
    preset_path: &Path,
    preset: &peaky_preset::Preset,
    out_dir: &Path,
    opts: &MapExportOptions,
    fleet_site_count: usize,
    hub_config: &crate::hub_bridge::HubBridgeConfig,
    coverage_bbox: [f64; 4],
) -> Result<MapExportResult> {
    let hub_bridge = compute_hub_bridge_progress(&[], hub_config, opts.silver_base_step_m);
    let overlay_meta = json!({
        "coverage_bbox": coverage_bbox,
        META_HUB_BRIDGE_KEY: hub_bridge,
        "coverage_tile_min_zoom": opts.tile_zoom_min,
        "coverage_tile_max_zoom": opts.tile_zoom_max,
    });
    let geojson_path = out_dir.join("coverage-network.geojson");
    let mut fc = build_feature_collection(fleet_site_count, preset, overlay_meta)?;
    if let Some(meta) = fc.get_mut("meshenvy_meta").and_then(|m| m.as_object_mut()) {
        meta.insert("model".into(), json!("none"));
    }
    write_geojson(&fc, &geojson_path)?;

    let tiles_dir = out_dir.join("coverage-tiles");
    if !opts.skip_tiles {
        if tiles_dir.exists() {
            std::fs::remove_dir_all(&tiles_dir)?;
        }
        std::fs::create_dir_all(&tiles_dir)?;
        write_tile_sidecar(&tiles_dir, coverage_bbox, opts.tile_zoom_max)?;
    }

    let _ = preset_path;
    Ok(MapExportResult {
        fleet_site_count,
        viewshed_runs: 0,
        viewshed_cache_hits: 0,
        geojson_path,
        tiles_dir,
    })
}
