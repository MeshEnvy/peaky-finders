//! On-demand RF viewshed generation.

use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Instant;

use anyhow::{bail, Context, Result};
use peaky_preset::{load_preset, resolved_viewshed_root, Preset, SiteEntry};
use serde_json::Value;
use splatter::Session;
use tokio::task::spawn_blocking;

use crate::rf::{preset_to_request_with_sim, viewshed_workspace_digest};
use crate::viewshed_index::{
    image_coordinates_from_bbox, load_bounds_from_manifest, viewshed_cache_png_api_path,
};
use crate::viewshed_log::{viewshed_progress_log, viewshed_progress_log_verbose};
use crate::viewshed_sim::{
    effective_target_raster_for_preset, raster_upgrade_ladder, ViewshedSimOverrides,
    MIN_SERVE_RASTER_DIMENSION,
};

pub const DRAFT_VIEWSHED_SLUG: &str = "_draft";

#[derive(Debug, thiserror::Error)]
#[error("{0}")]
pub struct ViewshedError(pub String);

pub fn preview_site_at(lat: f64, lon: f64) -> Result<SiteEntry, ViewshedError> {
    if !(-90.0..=90.0).contains(&lat) {
        return Err(ViewshedError(format!("lat out of bounds: {lat}")));
    }
    if !(-180.0..=180.0).contains(&lon) {
        return Err(ViewshedError(format!("lon out of bounds: {lon}")));
    }
    Ok(SiteEntry {
        name: "Preview".into(),
        loc: [lat, lon],
        height_m: None,
        description: None,
        tags: Vec::new(),
    })
}

pub fn resolve_viewshed_workdir(
    preset_path: &Path,
    preset: &Preset,
    site: &SiteEntry,
    sim: Option<&ViewshedSimOverrides>,
) -> Result<PathBuf> {
    let req = preset_to_request_with_sim(preset, site.loc[0], site.loc[1], Some(site), sim)?;
    let digest = viewshed_workspace_digest(&req)?;
    Ok(resolved_viewshed_root(preset_path).join(digest))
}

pub async fn ensure_viewshed_png(
    session: Arc<Session>,
    preset_path: &Path,
    site_slug: &str,
    verbose: bool,
) -> Result<PathBuf> {
    let preset = load_preset(preset_path)?;
    let site = preset
        .sites
        .get(site_slug)
        .with_context(|| format!("unknown site {site_slug}"))?;
    ensure_viewshed_for_site(session, preset_path, &preset, site_slug, site, verbose).await
}

pub async fn ensure_viewshed_for_site(
    session: Arc<Session>,
    preset_path: &Path,
    preset: &Preset,
    site_slug: &str,
    site: &SiteEntry,
    verbose: bool,
) -> Result<PathBuf> {
    let preset_path = preset_path.to_path_buf();
    let site_slug = site_slug.to_string();
    let site = site.clone();
    let preset = preset.clone();
    spawn_blocking(move || {
        ensure_viewshed_for_site_blocking(session, &preset_path, &preset, &site_slug, &site, verbose)
    })
    .await
    .context("join coverage task")?
}

pub fn ensure_viewshed_for_site_blocking(
    session: Arc<Session>,
    preset_path: &Path,
    preset: &Preset,
    site_slug: &str,
    site: &SiteEntry,
    verbose: bool,
) -> Result<PathBuf> {
    ensure_viewshed_blocking(
        session,
        preset_path,
        preset,
        site.loc[0],
        site.loc[1],
        Some(site),
        None,
        verbose,
        site_slug,
    )
}

pub fn target_viewshed_digest_for_site(preset: &Preset, site: &SiteEntry) -> Result<String> {
    let req = preset_to_request_with_sim(preset, site.loc[0], site.loc[1], Some(site), None)?;
    viewshed_workspace_digest(&req)
}

pub fn viewshed_workdir_for_raster(
    preset_path: &Path,
    preset: &Preset,
    site: &SiteEntry,
    raster_dimension: u32,
) -> Result<PathBuf> {
    let sim = ViewshedSimOverrides {
        raster_dimension: Some(raster_dimension),
        ..Default::default()
    };
    resolve_viewshed_workdir(preset_path, preset, site, Some(&sim))
}

pub fn viewshed_digest_for_raster(
    preset: &Preset,
    site: &SiteEntry,
    raster_dimension: u32,
) -> Result<String> {
    let sim = ViewshedSimOverrides {
        raster_dimension: Some(raster_dimension),
        ..Default::default()
    };
    let req = preset_to_request_with_sim(preset, site.loc[0], site.loc[1], Some(site), Some(&sim))?;
    viewshed_workspace_digest(&req)
}

pub fn viewshed_overlay_at_workdir(
    project_slug: &str,
    site_slug: &str,
    workdir: &Path,
    digest: &str,
    raster_dimension: u32,
    raster_target: u32,
    ladder_step: Option<u32>,
    ladder_total: Option<u32>,
) -> Result<Option<Value>> {
    let png = workdir.join("splat.png");
    if !png.is_file() {
        return Ok(None);
    }
    let bounds = load_bounds_from_manifest(&workdir.join("manifest.json"))
        .context("viewshed manifest bbox")?;
    let coordinates = image_coordinates_from_bbox(&bounds)?;
    let at_target = raster_dimension >= raster_target;
    let mut overlay = serde_json::json!({
        "slug": site_slug,
        "digest": digest,
        "url": viewshed_cache_png_api_path(project_slug, digest),
        "coordinates": coordinates,
        "raster_dimension": raster_dimension,
        "raster_target": raster_target,
        "at_target": at_target,
    });
    if let Some(obj) = overlay.as_object_mut() {
        if let Some(step) = ladder_step {
            obj.insert("ladder_step".into(), serde_json::json!(step));
        }
        if let Some(total) = ladder_total {
            obj.insert("ladder_total".into(), serde_json::json!(total));
        }
    }
    Ok(Some(overlay))
}

/// Generate (or reuse cache for) each rung on the pixel ladder, invoking ``publish`` after every ready step.
pub fn ensure_viewshed_progressive_for_site_blocking<F>(
    session: Arc<Session>,
    project_slug: &str,
    preset_path: &Path,
    preset: &Preset,
    site_slug: &str,
    site: &SiteEntry,
    verbose: bool,
    mut publish: F,
) -> Result<()>
where
    F: FnMut(Value),
{
    let target = effective_target_raster_for_preset(preset, None);
    let ladder = raster_upgrade_ladder(MIN_SERVE_RASTER_DIMENSION, target);
    let ladder_total = ladder.len() as u32;
    viewshed_progress_log_verbose(
        verbose,
        &format!(
            "progressive {site_slug}: target={target}px, {} rung(s) {:?}",
            ladder_total, ladder
        ),
    );
    for (step_idx, raster) in ladder.iter().copied().enumerate() {
        let step_n = step_idx as u32 + 1;
        let sim = ViewshedSimOverrides {
            raster_dimension: Some(raster),
            ..Default::default()
        };
        let digest = viewshed_digest_for_raster(preset, site, raster)?;
        let workdir = resolved_viewshed_root(preset_path).join(&digest);
        let png = workdir.join("splat.png");
        if !png.is_file() {
            viewshed_progress_log(&format!(
                "{site_slug} [{step_n}/{ladder_total}] generating {raster}px…"
            ));
            let t0 = Instant::now();
            ensure_viewshed_blocking(
                session.clone(),
                preset_path,
                preset,
                site.loc[0],
                site.loc[1],
                Some(site),
                Some(&sim),
                verbose,
                site_slug,
            )?;
            viewshed_progress_log(&format!(
                "{site_slug} [{step_n}/{ladder_total}] {raster}px done ({:.1}s)",
                t0.elapsed().as_secs_f64()
            ));
        } else {
            viewshed_progress_log_verbose(
                verbose,
                &format!("{site_slug} [{step_n}/{ladder_total}] {raster}px cache hit"),
            );
        }
        if let Some(overlay) = viewshed_overlay_at_workdir(
            project_slug,
            site_slug,
            &workdir,
            &digest,
            raster,
            target,
            Some(step_n),
            Some(ladder_total),
        )? {
            publish(overlay);
        }
    }
    viewshed_progress_log(&format!(
        "{site_slug} progressive warm complete (target={target}px)"
    ));
    Ok(())
}

/// Progressive warm for coordinate draft viewsheds (seek hops, placement preview).
pub fn ensure_viewshed_progressive_for_coords_blocking<F>(
    session: Arc<Session>,
    project_slug: &str,
    preset_path: &Path,
    preset: &Preset,
    lat: f64,
    lon: f64,
    sim: &ViewshedSimOverrides,
    verbose: bool,
    mut publish: F,
) -> Result<()>
where
    F: FnMut(Value),
{
    let site = preview_site_at(lat, lon).map_err(|e| anyhow::anyhow!(e.0))?;
    let target = effective_target_raster_for_preset(preset, Some(sim));
    let ladder = raster_upgrade_ladder(MIN_SERVE_RASTER_DIMENSION, target);
    let ladder_total = ladder.len() as u32;
    let label = DRAFT_VIEWSHED_SLUG;
    viewshed_progress_log_verbose(
        verbose,
        &format!(
            "progressive {label}: target={target}px, {} rung(s) {:?}",
            ladder_total, ladder
        ),
    );
    for (step_idx, raster) in ladder.iter().copied().enumerate() {
        let step_n = step_idx as u32 + 1;
        let step_sim = ViewshedSimOverrides {
            radius_km: sim.radius_km,
            raster_dimension: Some(raster),
            ..Default::default()
        };
        let req = preset_to_request_with_sim(preset, lat, lon, Some(&site), Some(&step_sim))?;
        let digest = viewshed_workspace_digest(&req)?;
        let workdir = resolved_viewshed_root(preset_path).join(&digest);
        let png = workdir.join("splat.png");
        if !png.is_file() {
            viewshed_progress_log(&format!(
                "{label} [{step_n}/{ladder_total}] generating {raster}px…"
            ));
            let t0 = Instant::now();
            ensure_viewshed_blocking(
                session.clone(),
                preset_path,
                preset,
                lat,
                lon,
                Some(&site),
                Some(&step_sim),
                verbose,
                label,
            )?;
            viewshed_progress_log(&format!(
                "{label} [{step_n}/{ladder_total}] {raster}px done ({:.1}s)",
                t0.elapsed().as_secs_f64()
            ));
        } else {
            viewshed_progress_log_verbose(
                verbose,
                &format!("{label} [{step_n}/{ladder_total}] {raster}px cache hit"),
            );
        }
        if let Some(mut overlay) = viewshed_overlay_at_workdir(
            project_slug,
            label,
            &workdir,
            &digest,
            raster,
            target,
            Some(step_n),
            Some(ladder_total),
        )? {
            if let Some(obj) = overlay.as_object_mut() {
                obj.insert("lat".into(), serde_json::json!(lat));
                obj.insert("lon".into(), serde_json::json!(lon));
            }
            publish(overlay);
        }
    }
    viewshed_progress_log(&format!(
        "{label} progressive warm complete (target={target}px)"
    ));
    Ok(())
}

pub fn ensure_coords_viewshed_blocking(
    session: Arc<Session>,
    preset_path: &Path,
    preset: &Preset,
    lat: f64,
    lon: f64,
    sim: Option<&ViewshedSimOverrides>,
    verbose: bool,
) -> Result<PathBuf> {
    ensure_viewshed_blocking(
        session,
        preset_path,
        preset,
        lat,
        lon,
        None,
        sim,
        verbose,
        DRAFT_VIEWSHED_SLUG,
    )
}

fn ensure_viewshed_blocking(
    session: Arc<Session>,
    preset_path: &Path,
    preset: &Preset,
    lat: f64,
    lon: f64,
    site: Option<&SiteEntry>,
    sim: Option<&ViewshedSimOverrides>,
    verbose: bool,
    label: &str,
) -> Result<PathBuf> {
    let req = preset_to_request_with_sim(preset, lat, lon, site, sim)?;
    let digest = viewshed_workspace_digest(&req)?;
    let workdir = resolved_viewshed_root(preset_path).join(&digest);
    let png = workdir.join("splat.png");
    if png.is_file() {
        return Ok(png);
    }
    fs::create_dir_all(&workdir)?;
    let req_json = serde_json::to_string_pretty(&req)?;
    fs::write(workdir.join("request.json"), req_json)?;
    let t0 = Instant::now();
    session.run(&workdir)?;
    viewshed_progress_log_verbose(
        verbose,
        &format!(
            "{label} splatter {}px radius={:.0}km ({:.1}s) {}",
            req.raster_dimension,
            req.radius / 1000.0,
            t0.elapsed().as_secs_f64(),
            png.display()
        ),
    );
    if !png.is_file() {
        bail!("missing splat.png after generation for {label}");
    }
    Ok(png)
}

pub fn viewshed_digest_for_site(preset: &Preset, site: &SiteEntry) -> Result<String> {
    let req = preset_to_request_with_sim(preset, site.loc[0], site.loc[1], Some(site), None)?;
    viewshed_workspace_digest(&req)
}

pub fn coords_viewshed_overlay_if_ready(
    project_slug: &str,
    preset_path: &Path,
    preset: &Preset,
    lat: f64,
    lon: f64,
    sim: Option<&ViewshedSimOverrides>,
) -> Result<Option<Value>> {
    let site = preview_site_at(lat, lon).map_err(|e| anyhow::anyhow!(e.0))?;
    let target_raster = effective_target_raster_for_preset(preset, sim);
    let viewshed_root = resolved_viewshed_root(preset_path);
    let ladder = raster_upgrade_ladder(MIN_SERVE_RASTER_DIMENSION, target_raster);
    for raster in ladder.iter().rev().copied() {
        let step_sim = ViewshedSimOverrides {
            radius_km: sim.and_then(|s| s.radius_km),
            raster_dimension: Some(raster),
            ..Default::default()
        };
        let req = preset_to_request_with_sim(preset, lat, lon, Some(&site), Some(&step_sim))?;
        let digest = viewshed_workspace_digest(&req)?;
        let workdir = viewshed_root.join(&digest);
        let png = workdir.join("splat.png");
        if !png.is_file() {
            continue;
        }
        let bounds = load_bounds_from_manifest(&workdir.join("manifest.json"));
        let Some(bounds) = bounds else {
            continue;
        };
        let coordinates = image_coordinates_from_bbox(&bounds)?;
        return Ok(Some(serde_json::json!({
            "slug": DRAFT_VIEWSHED_SLUG,
            "digest": digest,
            "url": viewshed_cache_png_api_path(project_slug, &digest),
            "coordinates": coordinates,
            "lat": lat,
            "lon": lon,
            "raster_dimension": raster,
            "raster_target": target_raster,
            "at_target": raster == target_raster,
        })));
    }
    Ok(None)
}

pub fn read_coords_viewshed_png_if_ready(
    preset_path: &Path,
    preset: &Preset,
    lat: f64,
    lon: f64,
    sim: Option<&ViewshedSimOverrides>,
) -> Result<Option<PathBuf>> {
    let site = preview_site_at(lat, lon).map_err(|e| anyhow::anyhow!(e.0))?;
    let workdir = resolve_viewshed_workdir(preset_path, preset, &site, sim)?;
    let png = workdir.join("splat.png");
    if png.is_file() {
        Ok(Some(png))
    } else {
        Ok(None)
    }
}
