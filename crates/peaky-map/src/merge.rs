//! Filter fleet viewsheds to the coverage bbox.

use std::path::Path;

use peaky_serve::load_bounds_from_manifest;
use tracing::warn;

pub struct SiteMaskInput {
    pub png_path: std::path::PathBuf,
    pub manifest_path: std::path::PathBuf,
}

fn intersect_bounds(a: [f64; 4], b: [f64; 4]) -> Option<[f64; 4]> {
    let w = a[0].max(b[0]);
    let s = a[1].max(b[1]);
    let e = a[2].min(b[2]);
    let n = a[3].min(b[3]);
    if w >= e || s >= n {
        return None;
    }
    Some([w, s, e, n])
}

fn ppm_bounds_wgs84(manifest_path: &Path) -> Option<[f64; 4]> {
    let bbox = load_bounds_from_manifest(manifest_path)?;
    let west = *bbox.get("west")?;
    let south = *bbox.get("south")?;
    let east = *bbox.get("east")?;
    let north = *bbox.get("north")?;
    Some([west, south, east, north])
}

pub fn filter_inputs_to_coverage(
    inputs: Vec<SiteMaskInput>,
    coverage: [f64; 4],
) -> Vec<SiteMaskInput> {
    let input_count = inputs.len();
    let mut kept = Vec::new();
    for input in inputs {
        let Some(bounds) = ppm_bounds_wgs84(&input.manifest_path) else {
            warn!("skipping {}: no manifest bbox", input.png_path.display());
            continue;
        };
        if intersect_bounds(bounds, coverage).is_none() {
            warn!(
                "skipping footprint outside coverage area: {}",
                input.png_path.display()
            );
            continue;
        }
        kept.push(input);
    }
    let dropped = input_count.saturating_sub(kept.len());
    if dropped > 0 {
        warn!("dropped {dropped} footprint(s) outside coverage bbox");
    }
    kept
}

pub fn union_manifest_bounds(inputs: &[SiteMaskInput]) -> Option<[f64; 4]> {
    let mut union: Option<[f64; 4]> = None;
    for input in inputs {
        let Some(b) = ppm_bounds_wgs84(&input.manifest_path) else {
            continue;
        };
        union = Some(match union {
            None => b,
            Some(u) => [
                u[0].min(b[0]),
                u[1].min(b[1]),
                u[2].max(b[2]),
                u[3].max(b[3]),
            ],
        });
    }
    union
}
