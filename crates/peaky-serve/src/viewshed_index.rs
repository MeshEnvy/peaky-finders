//! Bulk viewshed cache index and MapLibre overlay metadata.

use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use peaky_geo::polygonize::{fraction_to_lat_lon, LatLonBox};
use peaky_preset::{
    dem_native_raster_dimension, preset_radius_km, resolved_viewshed_root, BoardViewshedResolver,
    Preset, SiteEntry,
};
use serde_json::{json, Value};
use splatter::CovRequest;

use crate::rf::{preset_to_request, viewshed_workspace_digest};
use crate::viewshed_log::viewshed_progress_log;
use crate::viewshed::viewshed_digest_for_site;
use crate::viewshed_sim::{effective_target_raster_for_site, ViewshedSimOverrides};

/// YAML and query-string parses of the same pin can differ by one f64 ULP (~1e-14°).
const COORD_REUSE_EPS_DEG: f64 = 1e-6;

fn same_viewshed_except_coord_noise(want: &CovRequest, have: &CovRequest) -> bool {
    if (want.lat - have.lat).abs() > COORD_REUSE_EPS_DEG
        || (want.lon - have.lon).abs() > COORD_REUSE_EPS_DEG
    {
        return false;
    }
    let mut snapped = want.clone();
    snapped.lat = have.lat;
    snapped.lon = have.lon;
    match (
        splatter::splat_input_sha256(&snapped),
        splatter::splat_input_sha256(have),
    ) {
        (Ok(a), Ok(b)) => a == b,
        _ => false,
    }
}

/// Copy a finished raster into `dest` when the only digest difference is lat/lon float noise.
pub(crate) fn adopt_coord_noise_viewshed(
    preset_path: &Path,
    dest: &Path,
    want: &CovRequest,
) -> Result<bool> {
    let root = resolved_viewshed_root(preset_path);
    let Ok(rd) = fs::read_dir(&root) else {
        return Ok(false);
    };
    for ent in rd.flatten() {
        let src = ent.path();
        if src == dest {
            continue;
        }
        let png = src.join("splat.png");
        if !png.is_file() {
            continue;
        }
        let Ok(raw) = fs::read_to_string(src.join("request.json")) else {
            continue;
        };
        let Ok(have) = serde_json::from_str::<CovRequest>(&raw) else {
            continue;
        };
        if !same_viewshed_except_coord_noise(want, &have) {
            continue;
        }
        fs::create_dir_all(dest)?;
        fs::copy(&png, dest.join("splat.png"))?;
        let manifest = src.join("manifest.json");
        if manifest.is_file() {
            fs::copy(&manifest, dest.join("manifest.json"))?;
        }
        fs::write(dest.join("request.json"), serde_json::to_string_pretty(want)?)?;
        viewshed_progress_log(&format!(
            "viewshed cache reuse (coord drift) {}",
            src.file_name().unwrap_or_default().to_string_lossy()
        ));
        return Ok(true);
    }
    Ok(false)
}

pub fn viewshed_cache_png_api_path(project_slug: &str, digest: &str) -> String {
    format!("/api/p/{project_slug}/cache/viewsheds/{digest}/splat.png")
}

pub fn load_bounds_from_manifest(path: &Path) -> Option<HashMap<String, f64>> {
    let raw = std::fs::read_to_string(path).ok()?;
    let data: Value = serde_json::from_str(&raw).ok()?;
    let bbox = data.get("bbox")?;
    let north = bbox.get("north")?.as_f64()?;
    let south = bbox.get("south")?.as_f64()?;
    let east = bbox.get("east")?.as_f64()?;
    let west = bbox.get("west")?.as_f64()?;
    let mut out = HashMap::from([
        ("north".to_string(), north),
        ("south".to_string(), south),
        ("east".to_string(), east),
        ("west".to_string(), west),
    ]);
    if let Some(rot) = bbox.get("rotation").and_then(|v| v.as_f64()) {
        if !rot.is_nan() {
            out.insert("rotation".to_string(), rot);
        }
    }
    Some(out)
}

pub fn image_coordinates_from_bbox(bbox: &HashMap<String, f64>) -> Result<Vec<Vec<f64>>> {
    let lat_lon_box = LatLonBox::from_map(bbox)?;
    let mut coords = Vec::with_capacity(4);
    for (u_frac, v_frac) in [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)] {
        let (lat, lon) = fraction_to_lat_lon(u_frac, v_frac, &lat_lon_box)?;
        coords.push(vec![lon, lat]);
    }
    Ok(coords)
}

fn splat_png_is_valid_fast(path: &Path) -> bool {
    let Ok(meta) = std::fs::metadata(path) else {
        return false;
    };
    meta.len() > 256
}

fn site_index_entry(
    project_slug: &str,
    site_slug: &str,
    site: &SiteEntry,
    preset: &Preset,
    viewshed_root: &Path,
    target_raster: u32,
    board_viewshed: Option<&BoardViewshedResolver>,
) -> Result<Value> {
    let target_digest = viewshed_digest_for_site(preset, site, board_viewshed)?;
    let target_workdir = viewshed_root.join(&target_digest);
    let target_png = target_workdir.join("splat.png");
    if target_png.is_file() && splat_png_is_valid_fast(&target_png) {
        return overlay_index_entry(
            project_slug,
            site_slug,
            &target_workdir,
            &target_digest,
            target_raster,
        );
    }

    Ok(json!({
        "slug": site_slug,
        "ready": false,
        "digest": target_digest,
        "raster_dimension": target_raster,
    }))
}

fn overlay_index_entry(
    project_slug: &str,
    site_slug: &str,
    workdir: &Path,
    digest: &str,
    raster_dimension: u32,
) -> Result<Value> {
    let bounds = load_bounds_from_manifest(&workdir.join("manifest.json"));
    let Some(bounds) = bounds else {
        return Ok(json!({
            "slug": site_slug,
            "ready": false,
            "digest": digest,
            "raster_dimension": raster_dimension,
        }));
    };
    let coordinates = image_coordinates_from_bbox(&bounds)?;
    Ok(json!({
        "slug": site_slug,
        "ready": true,
        "digest": digest,
        "url": viewshed_cache_png_api_path(project_slug, digest),
        "coordinates": coordinates,
        "raster_dimension": raster_dimension,
    }))
}

pub fn build_viewshed_index(
    project_slug: &str,
    preset_path: &Path,
    preset: &Preset,
    board_viewshed: Option<&BoardViewshedResolver>,
) -> Result<Value> {
    let viewshed_root = resolved_viewshed_root(preset_path);
    let mut entries = serde_json::Map::new();
    let mut ready_count = 0usize;
    for (slug, site) in preset.sites.iter() {
        let target_raster = effective_target_raster_for_site(preset, site, board_viewshed, None);
        let row = site_index_entry(
            project_slug,
            slug,
            site,
            preset,
            &viewshed_root,
            target_raster,
            board_viewshed,
        )?;
        if row.get("ready").and_then(|v| v.as_bool()) == Some(true) {
            ready_count += 1;
        }
        entries.insert(slug.clone(), row);
    }
    let radius_km = preset_radius_km(preset);
    Ok(json!({
        "project": project_slug,
        "sites": Value::Object(entries),
        "ready_count": ready_count,
        "total": preset.sites.len(),
        "sim": {
            "radius_km": radius_km,
            "raster_dimension": dem_native_raster_dimension(radius_km * 1000.0),
        },
    }))
}

pub fn site_viewshed_overlay_if_ready(
    project_slug: &str,
    preset_path: &Path,
    site_slug: &str,
    site: &SiteEntry,
    preset: &Preset,
    sim: Option<&ViewshedSimOverrides>,
    board_viewshed: Option<&BoardViewshedResolver>,
) -> Result<Option<Value>> {
    let viewshed_root = resolved_viewshed_root(preset_path);
    let target_raster = effective_target_raster_for_site(preset, site, board_viewshed, sim);
    let row = site_index_entry(
        project_slug,
        site_slug,
        site,
        preset,
        &viewshed_root,
        target_raster,
        board_viewshed,
    )?;
    if row.get("ready").and_then(|v| v.as_bool()) != Some(true) {
        return Ok(None);
    }
    Ok(Some(row))
}

pub fn resolve_viewshed_cache_png(preset_path: &Path, digest: &str) -> Option<PathBuf> {
    let label = digest.trim().to_lowercase();
    if label.len() != 64 {
        return None;
    }
    let png = resolved_viewshed_root(preset_path).join(&label).join("splat.png");
    if png.is_file() && splat_png_is_valid_fast(&png) {
        Some(png)
    } else {
        None
    }
}

pub fn viewshed_overlay_from_workdir(
    project_slug: &str,
    site_slug: &str,
    preset: &Preset,
    site: &SiteEntry,
    workdir: &Path,
) -> Result<Option<Value>> {
    let digest = viewshed_workspace_digest(&preset_to_request(
        preset,
        site.loc[0],
        site.loc[1],
        Some(site),
    )?)?;
    let png = workdir.join("splat.png");
    if !png.is_file() || !splat_png_is_valid_fast(&png) {
        return Ok(None);
    }
    let bounds = load_bounds_from_manifest(&workdir.join("manifest.json"))
        .context("viewshed manifest bbox")?;
    let coordinates = image_coordinates_from_bbox(&bounds)?;
    Ok(Some(json!({
        "slug": site_slug,
        "digest": digest,
        "url": viewshed_cache_png_api_path(project_slug, &digest),
        "coordinates": coordinates,
    })))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample_req(lon: f64) -> CovRequest {
        let mut v: Value = serde_json::from_str(include_str!(
            "../../../splatter/tests/fixtures/splat_request_hash_fixture.json"
        ))
        .unwrap();
        v["lon"] = json!(lon);
        serde_json::from_value(v).unwrap()
    }

    #[test]
    fn adopt_reuses_one_ulp_longitude() {
        let project = std::env::temp_dir().join(format!(
            "peaky-adopt-{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&project);
        let preset = project.join("config.yaml");
        fs::create_dir_all(&project).unwrap();
        let have = sample_req(-114.96467113494873);
        let want = sample_req(-114.96467113494872);
        let src_digest = splatter::splat_input_sha256(&have).unwrap();
        let dest_digest = splatter::splat_input_sha256(&want).unwrap();
        assert_ne!(src_digest, dest_digest);
        let src = resolved_viewshed_root(&preset).join(&src_digest);
        fs::create_dir_all(&src).unwrap();
        fs::write(
            src.join("request.json"),
            serde_json::to_string_pretty(&have).unwrap(),
        )
        .unwrap();
        fs::write(src.join("splat.png"), vec![7u8; 300]).unwrap();
        let dest = resolved_viewshed_root(&preset).join(&dest_digest);
        assert!(adopt_coord_noise_viewshed(&preset, &dest, &want).unwrap());
        assert_eq!(fs::read(dest.join("splat.png")).unwrap(), vec![7u8; 300]);
        let _ = fs::remove_dir_all(&project);
    }
}
