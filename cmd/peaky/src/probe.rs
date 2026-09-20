//! ``peaky peaks probe`` — point YAML or region PASS/DROP report.

use std::path::Path;

use anyhow::{Context, Result};
use peaky_peaks::{probe_access_point, probe_access_point_explain, probe_peaks_region, PeaksBuildOptions};

pub fn run_probe_point(
    preset_path: &Path,
    lat: f64,
    lon: f64,
    force_osm: bool,
    explain: bool,
) -> Result<()> {
    if explain {
        let (probe, reports) =
            probe_access_point_explain(preset_path, lat, lon, force_osm).context("probe point")?;
        println!("candidates (score asc = preferred):");
        for (i, r) in reports.iter().enumerate() {
            println!(
                "  [{i}] node={} score={:.2} jeep={:.0}m trail={:.0}m hike={:.0}m budget_ok={} gated={}",
                r.park_node,
                r.score,
                r.jeep_m,
                r.trail_m,
                r.hike_m,
                r.passes_budget,
                r.budget_gated
            );
        }
        println!("{}", serde_yaml::to_string(&probe)?);
        return Ok(());
    }
    let probe = probe_access_point(preset_path, lat, lon, force_osm).context("probe point")?;
    print!("{}", serde_yaml::to_string(&probe)?);
    Ok(())
}

pub fn run_probe_region(preset_path: &Path, opts: PeaksBuildOptions) -> Result<()> {
    probe_peaks_region(preset_path, opts)
}
