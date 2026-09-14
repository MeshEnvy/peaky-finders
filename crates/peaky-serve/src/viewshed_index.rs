//! Bulk viewshed cache index and MapLibre overlay metadata.

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use peaky_geo::polygonize::{fraction_to_lat_lon, LatLonBox};
use peaky_preset::{preset_radius_km, resolved_viewshed_root, NodesBoardIndex, Preset, SiteEntry};
use serde_json::{json, Value};

use crate::rf::{preset_to_request, viewshed_workspace_digest};
use crate::viewshed::viewshed_digest_for_raster;
use crate::viewshed_sim::{
    effective_target_raster_for_site,
    effective_viewshed_quality_for_preset, raster_upgrade_ladder, ViewshedSimOverrides,
    MIN_SERVE_RASTER_DIMENSION,
};

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
    board_index: Option<&NodesBoardIndex>,
) -> Result<Value> {
    let target_digest = viewshed_digest_for_raster(preset, site, target_raster, board_index)?;
    let target_workdir = viewshed_root.join(&target_digest);
    let target_png = target_workdir.join("splat.png");
    if target_png.is_file() && splat_png_is_valid_fast(&target_png) {
        return overlay_index_entry(
            project_slug,
            site_slug,
            &target_workdir,
            &target_digest,
            target_raster,
            target_raster,
            true,
        );
    }

    let ladder = raster_upgrade_ladder(MIN_SERVE_RASTER_DIMENSION, target_raster);
    for raster in ladder.iter().rev().copied().skip(1) {
        let digest = viewshed_digest_for_raster(preset, site, raster, board_index)?;
        let workdir = viewshed_root.join(&digest);
        let png = workdir.join("splat.png");
        if png.is_file() && splat_png_is_valid_fast(&png) {
            return overlay_index_entry(
                project_slug,
                site_slug,
                &workdir,
                &digest,
                raster,
                target_raster,
                false,
            );
        }
    }

    Ok(json!({
        "slug": site_slug,
        "ready": false,
        "digest": target_digest,
        "raster_target": target_raster,
    }))
}

fn overlay_index_entry(
    project_slug: &str,
    site_slug: &str,
    workdir: &Path,
    digest: &str,
    raster_dimension: u32,
    raster_target: u32,
    at_target: bool,
) -> Result<Value> {
    let bounds = load_bounds_from_manifest(&workdir.join("manifest.json"));
    let Some(bounds) = bounds else {
        return Ok(json!({
            "slug": site_slug,
            "ready": false,
            "digest": digest,
            "raster_target": raster_target,
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
        "raster_target": raster_target,
        "at_target": at_target,
    }))
}

pub fn build_viewshed_index(
    project_slug: &str,
    preset_path: &Path,
    preset: &Preset,
    board_index: Option<&NodesBoardIndex>,
) -> Result<Value> {
    let viewshed_root = resolved_viewshed_root(preset_path);
    let mut entries = serde_json::Map::new();
    let mut ready_count = 0usize;
    for (slug, site) in preset.sites.iter() {
        let target_raster = effective_target_raster_for_site(preset, site, board_index, None);
        let row = site_index_entry(
            project_slug,
            slug,
            site,
            preset,
            &viewshed_root,
            target_raster,
            board_index,
        )?;
        if row.get("ready").and_then(|v| v.as_bool()) == Some(true) {
            ready_count += 1;
        }
        entries.insert(slug.clone(), row);
    }
    Ok(json!({
        "project": project_slug,
        "sites": Value::Object(entries),
        "ready_count": ready_count,
        "total": preset.sites.len(),
        "sim": {
            "radius_km": preset_radius_km(preset),
            "viewshed_quality": effective_viewshed_quality_for_preset(preset, None),
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
    board_index: Option<&NodesBoardIndex>,
) -> Result<Option<Value>> {
    let viewshed_root = resolved_viewshed_root(preset_path);
    let target_raster = effective_target_raster_for_site(preset, site, board_index, sim);
    let row = site_index_entry(
        project_slug,
        site_slug,
        site,
        preset,
        &viewshed_root,
        target_raster,
        board_index,
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
