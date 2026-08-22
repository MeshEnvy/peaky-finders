//! `peaky freeze` subcommand.

use std::path::PathBuf;

use anyhow::{Context, Result};
use peaky_freeze::{build_freeze_dir, default_freeze_output_dir, resolve_preset_path, FreezeOptions};
use peaky_preset::resolve_project_dir;

pub fn run(
    project: PathBuf,
    output: Option<PathBuf>,
    include_sites: bool,
    verbose: bool,
) -> Result<()> {
    let project_dir = resolve_project_dir(&project.to_string_lossy());
    let preset_path = resolve_preset_path(&project);
    if !preset_path.is_file() {
        anyhow::bail!(
            "project not found (expected config.yaml): {}",
            project_dir.display()
        );
    }

    let output_dir = output.unwrap_or_else(|| default_freeze_output_dir(&preset_path));

    if verbose {
        eprintln!(
            "[peaky freeze] source: {}",
            preset_path.display()
        );
        eprintln!(
            "[peaky freeze] output: {}",
            output_dir.display()
        );
    }

    let summary = build_freeze_dir(
        &preset_path,
        &output_dir,
        FreezeOptions {
            include_sites,
            verbose,
        },
    )
    .context("freeze export failed")?;

    println!("{}", output_dir.display());
    println!(
        "layers={} features={} config={}B geojson={}B sites={}",
        summary.layers.len(),
        summary.feature_count,
        summary.config_bytes,
        summary.geojson_bytes,
        if summary.include_sites { "yes" } else { "no" }
    );

    Ok(())
}
