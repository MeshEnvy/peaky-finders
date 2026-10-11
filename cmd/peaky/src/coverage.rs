//! `peaky coverage points|fleet` — ensure splatter viewshed PNGs and write a footprint manifest.

use std::path::{Path, PathBuf};
use std::sync::Arc;

use anyhow::{bail, Context, Result};
use geojson::{Feature, GeoJson, Value as GeoJsonValue};
use peaky_map::load_fleet_sites;
use peaky_preset::{
    load_preset, resolved_dem_fetch_max_workers, resolved_skadi_mirror_dir_for_project,
    resolved_viewshed_root,
};
use peaky_serve::viewshed::{
    ensure_coords_viewshed_blocking, ensure_viewshed_for_site_blocking, target_viewshed_digest_for_site,
};
use peaky_serve::ViewshedSimOverrides;
use serde_json::json;
use splatter::Session;
use tracing::info;

#[derive(serde::Serialize)]
struct ManifestRow {
    id: String,
    class: String,
    lat: f64,
    lon: f64,
    splat_path: String,
    manifest_path: String,
}

pub fn run_points(
    project: PathBuf,
    geojson: PathBuf,
    quality: u8,
    workers: usize,
    out: PathBuf,
    verbose: bool,
) -> Result<()> {
    let project_dir = peaky_preset::resolve_project_dir(&project.to_string_lossy());
    let preset_path = project_dir.join("config.yaml");
    let preset = load_preset(&preset_path)?;
    let mirror = resolved_skadi_mirror_dir_for_project(&project_dir);
    std::fs::create_dir_all(&mirror)?;
    let dem_workers = resolved_dem_fetch_max_workers(&preset);
    let session = Arc::new(Session::new(mirror, verbose, dem_workers));
    let sim = ViewshedSimOverrides {
        quality: Some(quality),
        ..Default::default()
    };

    let text = std::fs::read_to_string(&geojson)
        .with_context(|| format!("read {}", geojson.display()))?;
    let gj: GeoJson = text.parse().context("parse points GeoJSON")?;
    let features = feature_list(&gj)?;

    // One viewshed at a time. Splatter's row loop uses the process Rayon pool.
    // An outer par_iter shares that pool and leaves the raster on one core.
    let pool = rayon::ThreadPoolBuilder::new()
        .num_threads(workers.max(1))
        .build()
        .context("build rayon pool")?;

    let total = features.len();
    eprintln!("[peaky] {total} point viewsheds");
    let mut rows = Vec::new();
    for (i, (id, lat, lon)) in features.iter().enumerate() {
        eprintln!("[peaky] {}/{total} {id}", i + 1);
        let png = pool.install(|| {
            ensure_coords_viewshed_blocking(
                session.clone(),
                &preset_path,
                &preset,
                *lat,
                *lon,
                Some(&sim),
                verbose,
            )
        });
        match png {
            Ok(png) => {
                let Some(workdir) = png.parent() else { continue };
                rows.push(ManifestRow {
                    id: id.clone(),
                    class: "points".into(),
                    lat: *lat,
                    lon: *lon,
                    splat_path: png.display().to_string(),
                    manifest_path: workdir.join("manifest.json").display().to_string(),
                });
            }
            Err(e) => eprintln!("viewshed failed for {id} ({lat},{lon}): {e:#}"),
        }
    }

    write_manifest(&out, &rows)?;
    info!("wrote {} point footprint(s) -> {}", rows.len(), out.display());
    Ok(())
}

pub fn run_fleet(
    project: PathBuf,
    workers: usize,
    out: PathBuf,
    verbose: bool,
) -> Result<()> {
    let project_dir = peaky_preset::resolve_project_dir(&project.to_string_lossy());
    let preset_path = project_dir.join("config.yaml");
    let preset = load_preset(&preset_path)?;
    let board_viewshed = peaky_preset::BoardViewshedResolver::load(&project_dir)
        .map_err(|e| anyhow::anyhow!("boards.yaml: {e}"))?;
    let fleet = load_fleet_sites(&project_dir)?;
    let mirror = resolved_skadi_mirror_dir_for_project(&project_dir);
    std::fs::create_dir_all(&mirror)?;
    let dem_workers = resolved_dem_fetch_max_workers(&preset);
    let session = Arc::new(Session::new(mirror, verbose, dem_workers));

    let pool = rayon::ThreadPoolBuilder::new()
        .num_threads(workers.max(1))
        .build()
        .context("build rayon pool")?;

    let total = fleet.len();
    eprintln!("[peaky] {total} fleet viewsheds");
    let mut rows = Vec::new();
    for (i, site) in fleet.iter().enumerate() {
        eprintln!("[peaky] {}/{total} {}", i + 1, site.slug);
        let lat = site.site.loc[0];
        let lon = site.site.loc[1];
        let png = pool.install(|| {
            ensure_viewshed_for_site_blocking(
                session.clone(),
                &preset_path,
                &preset,
                &site.slug,
                &site.site,
                Some(&board_viewshed),
                verbose,
            )
        });
        match png {
            Ok(png) => {
                let Ok(digest) = target_viewshed_digest_for_site(&preset, &site.site, Some(&board_viewshed))
                else {
                    continue;
                };
                let workdir = resolved_viewshed_root(&preset_path).join(digest);
                rows.push(ManifestRow {
                    id: site.slug.clone(),
                    class: "fleet".into(),
                    lat,
                    lon,
                    splat_path: png.display().to_string(),
                    manifest_path: workdir.join("manifest.json").display().to_string(),
                });
            }
            Err(e) => eprintln!("viewshed failed for {}: {e:#}", site.slug),
        }
    }

    write_manifest(&out, &rows)?;
    info!("wrote {} fleet footprint(s) -> {}", rows.len(), out.display());
    Ok(())
}

fn write_manifest(out: &Path, rows: &[ManifestRow]) -> Result<()> {
    if let Some(parent) = out.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let payload = json!({ "footprints": rows });
    std::fs::write(out, serde_json::to_string_pretty(&payload)?)?;
    Ok(())
}

fn feature_list(gj: &GeoJson) -> Result<Vec<(String, f64, f64)>> {
    let mut out = Vec::new();
    match gj {
        GeoJson::FeatureCollection(fc) => {
            for (i, feat) in fc.features.iter().enumerate() {
                if let Some(triple) = point_from_feature(feat, i) {
                    out.push(triple);
                }
            }
        }
        GeoJson::Feature(f) => {
            if let Some(triple) = point_from_feature(f, 0) {
                out.push(triple);
            }
        }
        _ => bail!("GeoJSON must be FeatureCollection or Feature"),
    }
    if out.is_empty() {
        bail!("no Point features in GeoJSON");
    }
    Ok(out)
}

fn point_from_feature(feat: &Feature, idx: usize) -> Option<(String, f64, f64)> {
    let geom = feat.geometry.as_ref()?;
    let GeoJsonValue::Point(coords) = &geom.value else {
        return None;
    };
    if coords.len() < 2 {
        return None;
    }
    let lon = coords[0];
    let lat = coords[1];
    let id = feat
        .properties
        .as_ref()
        .and_then(|p| p.get("id"))
        .and_then(|v| v.as_str())
        .map(str::to_string)
        .unwrap_or_else(|| format!("pt-{idx}"));
    Some((id, lat, lon))
}
