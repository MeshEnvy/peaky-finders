//! ``peaky peaks`` subcommand.

use std::path::PathBuf;

use anyhow::{Context, Result};
use peaky_peaks::{
    build_peaks_catalog, resolve_corridor_from_opts, PeaksBuildOptions,
};
use peaky_peaks::{project_gtlf_fingerprint, project_pbf_fingerprint};
use peaky_preset::{
    gc_stale_cache_dirs, load_preset, prefs_key_for_project, resolve_preset_path,
};

pub fn run_build(
    project: PathBuf,
    bbox: Option<String>,
    polygon: Option<PathBuf>,
    corridor: Option<String>,
    corridor_sites: Option<String>,
    corridor_width_mi: f64,
    force: bool,
    verbose: bool,
    stop_after: Option<usize>,
    clean: bool,
) -> Result<()> {
    let preset_path = resolve_preset_path(&project.to_string_lossy());
    if !preset_path.is_file() {
        anyhow::bail!(
            "project not found (expected config.yaml): {}",
            preset_path.display()
        );
    }

    let preset = load_preset(&preset_path).context("load preset")?;
    let corridor_coords = resolve_corridor_from_opts(
        &preset,
        corridor.as_deref(),
        corridor_sites.as_deref(),
    )?;

    let parsed_bbox = bbox
        .as_deref()
        .map(peaky_geo::LonLatBBox::parse_csv)
        .transpose()
        .context("parse --bbox")?
        .map(|b| (b.west, b.south, b.east, b.north));

    let summary = build_peaks_catalog(
        &preset_path,
        PeaksBuildOptions {
            bbox: parsed_bbox,
            polygon,
            corridor: corridor_coords,
            corridor_width_mi,
            force_osm: force,
            verbose,
            stop_after,
            clean,
        },
    )
    .context("peaks catalog build failed")?;

    println!("{}", summary.output.display());
    println!(
        "kept={} denied={} gnis={} site_seeds={} dem={} max_slope_deg={:.1} entries={}",
        summary.kept,
        summary.denied,
        summary.gnis,
        summary.site_seeds,
        summary.dem,
        summary.max_slope_deg,
        summary.entries,
    );
    println!(
        "dropped: land={} road={} hike={} slope={} jeep={} dedup={} region={}",
        summary.dropped_land,
        summary.dropped_road,
        summary.dropped_hike,
        summary.dropped_slope,
        summary.dropped_jeep,
        summary.dropped_dedup,
        summary.dropped_region
    );

    Ok(())
}

pub fn run_gc(project: PathBuf, keep: usize, dry_run: bool) -> Result<()> {
    let preset_path = resolve_preset_path(&project.to_string_lossy());
    if !preset_path.is_file() {
        anyhow::bail!(
            "project not found (expected config.yaml): {}",
            preset_path.display()
        );
    }
    let project_dir = preset_path
        .parent()
        .context("preset path has no parent")?;
    let gtlf = project_gtlf_fingerprint(project_dir);
    let pbf = project_pbf_fingerprint(project_dir);
    let current = prefs_key_for_project(&preset_path, &gtlf, &pbf)?;
    let removed = gc_stale_cache_dirs(project_dir, &current, keep, dry_run)?;
    let mut bytes = 0u64;
    for dir in &removed {
        bytes += dir.bytes;
        println!(
            "{} prefs_key={} probe_files={} index={} bytes={}",
            if dry_run { "would remove" } else { "removed" },
            dir.prefs_key,
            dir.probe_files,
            dir.peaks_index,
            dir.bytes
        );
    }
    println!(
        "current_prefs_key={} stale_dirs={} bytes_freed={}{}",
        current,
        removed.len(),
        bytes,
        if dry_run { " (dry-run)" } else { "" }
    );
    Ok(())
}

fn parse_lat_lon(lat_lon: &str) -> Result<(f64, f64)> {
    let parts: Vec<&str> = lat_lon.split(',').map(str::trim).collect();
    if parts.len() != 2 {
        anyhow::bail!("expected lat,lon (e.g. 39.922869,-117.128549)");
    }
    let lat: f64 = parts[0].parse().context("parse latitude")?;
    let lon: f64 = parts[1].parse().context("parse longitude")?;
    Ok((lat, lon))
}

pub fn run_probe(
    project: PathBuf,
    lat_lon: Option<String>,
    bbox: Option<String>,
    polygon: Option<PathBuf>,
    corridor: Option<String>,
    corridor_sites: Option<String>,
    corridor_width_mi: f64,
    force: bool,
    _verbose: bool,
    explain: bool,
) -> Result<()> {
    let preset_path = resolve_preset_path(&project.to_string_lossy());
    if !preset_path.is_file() {
        anyhow::bail!(
            "project not found (expected config.yaml): {}",
            preset_path.display()
        );
    }
    let region = bbox.is_some()
        || polygon.is_some()
        || corridor.is_some()
        || corridor_sites.is_some();
    match (lat_lon, region) {
        (Some(_lat_lon), true) => anyhow::bail!(
            "pass either LAT,LON or a region (--corridor / --corridor-sites / --bbox / --polygon)"
        ),
        (Some(lat_lon), false) => {
            let (lat, lon) = parse_lat_lon(&lat_lon)?;
            crate::probe::run_probe_point(&preset_path, lat, lon, force, explain)
        }
        (None, false) => anyhow::bail!(
            "need LAT,LON or --corridor / --corridor-sites / --bbox / --polygon"
        ),
        (None, true) => {
            let preset = load_preset(&preset_path).context("load preset")?;
            let corridor_coords = resolve_corridor_from_opts(
                &preset,
                corridor.as_deref(),
                corridor_sites.as_deref(),
            )?;
            let parsed_bbox = bbox
                .as_deref()
                .map(peaky_geo::LonLatBBox::parse_csv)
                .transpose()
                .context("parse --bbox")?
                .map(|b| (b.west, b.south, b.east, b.north));
            crate::probe::run_probe_region(
                &preset_path,
                PeaksBuildOptions {
                    bbox: parsed_bbox,
                    polygon,
                    corridor: corridor_coords,
                    corridor_width_mi,
                    force_osm: force,
                    verbose: _verbose,
                    stop_after: None,
                    clean: false,
                },
            )
        }
    }
}
