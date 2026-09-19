//! ``peaky peaks`` subcommand.

use std::path::PathBuf;

use anyhow::{Context, Result};
use peaky_peaks::{build_peaks_catalog, probe_peak, resolve_corridor_from_opts, PeaksBuildOptions};
use peaky_preset::{load_preset, resolve_preset_path};

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

pub fn run_probe(project: PathBuf, lat_lon: String) -> Result<()> {
    let preset_path = resolve_preset_path(&project.to_string_lossy());
    if !preset_path.is_file() {
        anyhow::bail!(
            "project not found (expected config.yaml): {}",
            preset_path.display()
        );
    }
    let parts: Vec<&str> = lat_lon.split(',').map(str::trim).collect();
    if parts.len() != 2 {
        anyhow::bail!("expected lat,lon (e.g. 39.922869,-117.128549)");
    }
    let lat: f64 = parts[0].parse().context("parse latitude")?;
    let lon: f64 = parts[1].parse().context("parse longitude")?;
    println!("probe {lat:.6},{lon:.6} project={}", preset_path.display());
    probe_peak(&preset_path, lat, lon)
}
