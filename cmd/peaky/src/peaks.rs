//! ``peaky peaks`` subcommand.

use std::path::PathBuf;

use anyhow::{Context, Result};
use peaky_peaks::{build_peaks_catalog, PeaksBuildOptions};
use peaky_preset::resolve_preset_path;

pub fn run(project: PathBuf, bbox: Option<String>, force: bool, verbose: bool) -> Result<()> {
    let preset_path = resolve_preset_path(&project.to_string_lossy());
    if !preset_path.is_file() {
        anyhow::bail!(
            "project not found (expected config.yaml): {}",
            preset_path.display()
        );
    }

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
            force_osm: force,
            verbose,
        },
    )
    .context("peaks catalog build failed")?;

    println!("{}", summary.output.display());
    println!(
        "kept={} denied={} gnis={} site_seeds={} dem={} max_slope_deg={:.1}",
        summary.kept,
        summary.denied,
        summary.gnis,
        summary.site_seeds,
        summary.dem,
        summary.max_slope_deg
    );
    println!(
        "dropped: land={} road={} hike={} slope={} dedup={}",
        summary.dropped_land,
        summary.dropped_road,
        summary.dropped_hike,
        summary.dropped_slope,
        summary.dropped_dedup
    );

    Ok(())
}
