//! GNIS summit features from ``Gazetteer_National.gdb`` via GDAL ogr2ogr.

use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::Instant;

use anyhow::{bail, Context, Result};
use geojson::{FeatureCollection, GeoJson, Value as GjValue};
use tracing::info;

use crate::universe::RawCandidate;

const DEFAULT_GNIS_REL: &str = "data/sources/Gazetteer_National.gdb";
const NESTED_GNIS_GDB: &str = "Gazetteer_National_GDB.gdb";
const GNIS_SUMMIT_LAYER: &str = "Gaz_Features";

pub fn default_gnis_path(project_dir: &Path) -> PathBuf {
    project_dir.join(DEFAULT_GNIS_REL)
}

pub fn resolve_gnis_gdb(project_dir: &Path) -> Option<PathBuf> {
    let wrapper = default_gnis_path(project_dir);
    let nested = wrapper.join(NESTED_GNIS_GDB);
    if nested.is_dir() {
        return Some(nested);
    }
    if wrapper.is_dir() && ogrinfo_opens(&wrapper) {
        return Some(wrapper);
    }
    None
}

fn ogrinfo_opens(path: &Path) -> bool {
    Command::new("ogrinfo")
        .arg("-so")
        .arg("-q")
        .arg(path)
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
}

fn ogr2ogr_available() -> bool {
    Command::new("ogr2ogr")
        .arg("--version")
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
}

pub fn load_gnis_candidates(
    project_dir: &Path,
    west: f64,
    south: f64,
    east: f64,
    north: f64,
) -> Result<Vec<RawCandidate>> {
    let Some(gdb) = resolve_gnis_gdb(project_dir) else {
        info!(
            path = %default_gnis_path(project_dir).display(),
            "peaks: GNIS GDB not found — skipping"
        );
        return Ok(Vec::new());
    };
    if !ogr2ogr_available() {
        info!("peaks: ogr2ogr not on PATH — skipping GNIS");
        return Ok(Vec::new());
    }

    let tmp = tempfile::NamedTempFile::new().context("temp geojson")?;
    let out_path = tmp.path().with_extension("geojson");
    info!(
        layer = GNIS_SUMMIT_LAYER,
        path = %gdb.display(),
        bbox = format!("{west:.4},{south:.4},{east:.4},{north:.4}"),
        "peaks: exporting GNIS summits…"
    );
    let t0 = Instant::now();
    let status = Command::new("ogr2ogr")
        .arg("-f")
        .arg("GeoJSON")
        .arg(&out_path)
        .arg("-spat")
        .arg(format!("{west}"))
        .arg(format!("{south}"))
        .arg(format!("{east}"))
        .arg(format!("{north}"))
        .arg("-where")
        .arg("feature_class='Summit'")
        .arg(&gdb)
        .arg(GNIS_SUMMIT_LAYER)
        .status()
        .context("run ogr2ogr for GNIS")?;
    if !status.success() {
        bail!("ogr2ogr GNIS export failed");
    }

    let text = std::fs::read_to_string(&out_path).context("read GNIS geojson")?;
    let geo: GeoJson = text.parse().context("parse GNIS geojson")?;
    let GeoJson::FeatureCollection(FeatureCollection { features, .. }) = geo else {
        return Ok(Vec::new());
    };

    let mut out = Vec::new();
    for feature in features {
        let (lat, lon) = match feature.geometry.as_ref().map(|g| &g.value) {
            Some(GjValue::Point(coords)) if coords.len() >= 2 => (coords[1], coords[0]),
            _ => {
                let props = feature.properties.as_ref();
                let lat = props
                    .and_then(|p| p.get("prim_lat_dec"))
                    .and_then(|v| v.as_f64());
                let lon = props
                    .and_then(|p| p.get("prim_long_dec"))
                    .and_then(|v| v.as_f64());
                match (lat, lon) {
                    (Some(lat), Some(lon)) => (lat, lon),
                    _ => continue,
                }
            }
        };
        let props = feature.properties.as_ref();
        let elev = props
            .and_then(|p| p.get("ELEV_IN_M"))
            .or_else(|| props.and_then(|p| p.get("ELEV_M")))
            .or_else(|| props.and_then(|p| p.get("ELEVATION")))
            .and_then(|v| v.as_f64().or_else(|| v.as_i64().map(|i| i as f64)));
        let name = props
            .and_then(|p| p.get("feature_name"))
            .or_else(|| props.and_then(|p| p.get("FEATURE_NAME")))
            .or_else(|| props.and_then(|p| p.get("GNIS_NAME")))
            .or_else(|| props.and_then(|p| p.get("NAME")))
            .and_then(|v| v.as_str())
            .map(str::to_string);
        out.push(RawCandidate {
            name,
            lat,
            lon,
            elev_m: elev,
            source: "gnis".into(),
            seed_slug: None,
        });
    }
    info!(
        count = out.len(),
        elapsed_secs = t0.elapsed().as_secs_f64(),
        "peaks: GNIS export done"
    );
    Ok(out)
}
