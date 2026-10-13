//! On-demand RF viewshed generation.

use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Instant;

use anyhow::{bail, Context, Result};
use peaky_preset::{load_preset, resolved_viewshed_root, BoardViewshedResolver, Preset, SiteEntry};
use serde_json::Value;
use splatter::{CoverageRunContext, Session};
use tokio::task::spawn_blocking;

use crate::rf::{preset_to_request_with_sim, viewshed_workspace_digest};
use crate::viewshed_index::{
    image_coordinates_from_bbox, load_bounds_from_manifest, viewshed_cache_png_api_path,
};
use crate::viewshed_log::{viewshed_progress_log, viewshed_progress_log_verbose};
use crate::viewshed_sim::{
    effective_target_raster_for_preset, effective_target_raster_for_site, ViewshedSimOverrides,
};

pub const DRAFT_VIEWSHED_SLUG: &str = "_draft";

pub struct ViewshedWarmHooks {
    pub progress: Option<Arc<dyn Fn(f64, &str) + Send + Sync>>,
    pub cancel: Option<Arc<AtomicBool>>,
}

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
        node: None,
        access_choice: None,
    })
}

pub fn resolve_viewshed_workdir(
    preset_path: &Path,
    preset: &Preset,
    site: &SiteEntry,
    sim: Option<&ViewshedSimOverrides>,
    board_viewshed: Option<&BoardViewshedResolver>,
) -> Result<PathBuf> {
    let req = preset_to_request_with_sim(
        preset,
        site.loc[0],
        site.loc[1],
        Some(site),
        sim,
        board_viewshed,
    )?;
    let digest = viewshed_workspace_digest(&req)?;
    Ok(resolved_viewshed_root(preset_path).join(digest))
}

pub async fn ensure_viewshed_png(
    session: Arc<Session>,
    preset_path: &Path,
    site_slug: &str,
    board_viewshed: &BoardViewshedResolver,
    verbose: bool,
) -> Result<PathBuf> {
    let preset = load_preset(preset_path)?;
    let site = preset
        .sites
        .get(site_slug)
        .with_context(|| format!("unknown site {site_slug}"))?;
    ensure_viewshed_for_site(
        session,
        preset_path,
        &preset,
        site_slug,
        site,
        board_viewshed,
        verbose,
    )
    .await
}

pub async fn ensure_viewshed_for_site(
    session: Arc<Session>,
    preset_path: &Path,
    preset: &Preset,
    site_slug: &str,
    site: &SiteEntry,
    board_viewshed: &BoardViewshedResolver,
    verbose: bool,
) -> Result<PathBuf> {
    let preset_path = preset_path.to_path_buf();
    let site_slug = site_slug.to_string();
    let site = site.clone();
    let preset = preset.clone();
    let board_viewshed = board_viewshed.clone();
    spawn_blocking(move || {
        ensure_viewshed_for_site_blocking(
            session,
            &preset_path,
            &preset,
            &site_slug,
            &site,
            Some(&board_viewshed),
            verbose,
            None,
        )
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
    board_viewshed: Option<&BoardViewshedResolver>,
    verbose: bool,
    hooks: Option<&ViewshedWarmHooks>,
) -> Result<PathBuf> {
    ensure_viewshed_blocking(
        session,
        preset_path,
        preset,
        site.loc[0],
        site.loc[1],
        Some(site),
        None,
        board_viewshed,
        verbose,
        site_slug,
        hooks,
    )
}

pub fn target_viewshed_digest_for_site(
    preset: &Preset,
    site: &SiteEntry,
    board_viewshed: Option<&BoardViewshedResolver>,
) -> Result<String> {
    viewshed_digest_for_site(preset, site, board_viewshed)
}

pub fn viewshed_overlay_at_workdir(
    project_slug: &str,
    site_slug: &str,
    workdir: &Path,
    digest: &str,
    raster_dimension: u32,
) -> Result<Option<Value>> {
    let png = workdir.join("splat.png");
    if !png.is_file() {
        return Ok(None);
    }
    let Some(bounds) = load_bounds_from_manifest(&workdir.join("manifest.json")) else {
        return Ok(None);
    };
    let coordinates = image_coordinates_from_bbox(&bounds)?;
    Ok(Some(serde_json::json!({
        "slug": site_slug,
        "digest": digest,
        "url": viewshed_cache_png_api_path(project_slug, digest),
        "coordinates": coordinates,
        "raster_dimension": raster_dimension,
    })))
}

/// Generate (or reuse cache for) the DEM-native viewshed, invoking ``publish`` when ready.
pub fn ensure_viewshed_progressive_for_site_blocking<F>(
    session: Arc<Session>,
    project_slug: &str,
    preset_path: &Path,
    preset: &Preset,
    site_slug: &str,
    site: &SiteEntry,
    board_viewshed: Option<&BoardViewshedResolver>,
    verbose: bool,
    mut publish: F,
) -> Result<()>
where
    F: FnMut(Value),
{
    ensure_viewshed_progressive_for_site_blocking_with_hooks(
        session,
        project_slug,
        preset_path,
        preset,
        site_slug,
        site,
        board_viewshed,
        verbose,
        None,
        publish,
    )
}

pub fn ensure_viewshed_progressive_for_site_blocking_with_hooks<F>(
    session: Arc<Session>,
    project_slug: &str,
    preset_path: &Path,
    preset: &Preset,
    site_slug: &str,
    site: &SiteEntry,
    board_viewshed: Option<&BoardViewshedResolver>,
    verbose: bool,
    hooks: Option<&ViewshedWarmHooks>,
    mut publish: F,
) -> Result<()>
where
    F: FnMut(Value),
{
    let target = effective_target_raster_for_site(preset, site, board_viewshed, None);
    viewshed_progress_log_verbose(
        verbose,
        &format!("warm {site_slug}: native target={target}px"),
    );
    let digest = viewshed_digest_for_site(preset, site, board_viewshed)?;
    let workdir = resolved_viewshed_root(preset_path).join(&digest);
    let png = workdir.join("splat.png");
    if !png.is_file() {
        let req = preset_to_request_with_sim(
            preset,
            site.loc[0],
            site.loc[1],
            Some(site),
            None,
            board_viewshed,
        )?;
        if crate::viewshed_index::adopt_coord_noise_viewshed(preset_path, &workdir, &req)? {
            viewshed_progress_log(&format!("{site_slug} {target}px cache reuse (coord drift)"));
        } else {
            viewshed_progress_log(&format!("{site_slug} generating {target}px…"));
            let t0 = Instant::now();
            ensure_viewshed_blocking(
                session.clone(),
                preset_path,
                preset,
                site.loc[0],
                site.loc[1],
                Some(site),
                None,
                board_viewshed,
                verbose,
                site_slug,
                hooks,
            )?;
            viewshed_progress_log(&format!(
                "{site_slug} {target}px done ({:.1}s)",
                t0.elapsed().as_secs_f64()
            ));
        }
    } else {
        viewshed_progress_log_verbose(verbose, &format!("{site_slug} {target}px cache hit"));
    }
    if hooks
        .and_then(|h| h.cancel.as_ref())
        .is_some_and(|c| c.load(Ordering::Relaxed))
    {
        return Ok(());
    }
    if let Some(overlay) = viewshed_overlay_at_workdir(
        project_slug,
        site_slug,
        &workdir,
        &digest,
        target,
    )? {
        publish(overlay);
    }
    viewshed_progress_log(&format!("{site_slug} warm complete (target={target}px)"));
    Ok(())
}

/// Native warm for coordinate draft viewsheds (seek hops, placement preview).
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
    ensure_viewshed_progressive_for_coords_blocking_with_hooks(
        session,
        project_slug,
        preset_path,
        preset,
        lat,
        lon,
        sim,
        verbose,
        None,
        publish,
    )
}

pub fn ensure_viewshed_progressive_for_coords_blocking_with_hooks<F>(
    session: Arc<Session>,
    project_slug: &str,
    preset_path: &Path,
    preset: &Preset,
    lat: f64,
    lon: f64,
    sim: &ViewshedSimOverrides,
    verbose: bool,
    hooks: Option<&ViewshedWarmHooks>,
    mut publish: F,
) -> Result<()>
where
    F: FnMut(Value),
{
    let site = preview_site_at(lat, lon).map_err(|e| anyhow::anyhow!(e.0))?;
    let target = effective_target_raster_for_preset(preset, Some(sim));
    let label = DRAFT_VIEWSHED_SLUG;
    viewshed_progress_log_verbose(
        verbose,
        &format!("warm {label}: native target={target}px"),
    );
    let req = preset_to_request_with_sim(
        preset,
        lat,
        lon,
        Some(&site),
        Some(sim),
        None,
    )?;
    let digest = viewshed_workspace_digest(&req)?;
    let workdir = resolved_viewshed_root(preset_path).join(&digest);
    let png = workdir.join("splat.png");
    if !png.is_file() {
        if crate::viewshed_index::adopt_coord_noise_viewshed(preset_path, &workdir, &req)? {
            viewshed_progress_log(&format!("{label} {target}px cache reuse (coord drift)"));
        } else {
            viewshed_progress_log(&format!("{label} generating {target}px…"));
            let t0 = Instant::now();
            ensure_viewshed_blocking(
                session.clone(),
                preset_path,
                preset,
                lat,
                lon,
                Some(&site),
                Some(sim),
                None,
                verbose,
                label,
                hooks,
            )?;
            viewshed_progress_log(&format!(
                "{label} {target}px done ({:.1}s)",
                t0.elapsed().as_secs_f64()
            ));
        }
    } else {
        viewshed_progress_log_verbose(verbose, &format!("{label} {target}px cache hit"));
    }
    if hooks
        .and_then(|h| h.cancel.as_ref())
        .is_some_and(|c| c.load(Ordering::Relaxed))
    {
        return Ok(());
    }
    if let Some(mut overlay) = viewshed_overlay_at_workdir(
        project_slug,
        label,
        &workdir,
        &digest,
        target,
    )? {
        if let Some(obj) = overlay.as_object_mut() {
            obj.insert("lat".into(), serde_json::json!(lat));
            obj.insert("lon".into(), serde_json::json!(lon));
        }
        publish(overlay);
    }
    viewshed_progress_log(&format!("{label} warm complete (target={target}px)"));
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
        None,
        verbose,
        DRAFT_VIEWSHED_SLUG,
        None,
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
    board_viewshed: Option<&BoardViewshedResolver>,
    verbose: bool,
    label: &str,
    hooks: Option<&ViewshedWarmHooks>,
) -> Result<PathBuf> {
    if hooks
        .and_then(|h| h.cancel.as_ref())
        .is_some_and(|c| c.load(Ordering::Relaxed))
    {
        bail!("viewshed cancelled");
    }
    let req = preset_to_request_with_sim(preset, lat, lon, site, sim, board_viewshed)?;
    let digest = viewshed_workspace_digest(&req)?;
    let workdir = resolved_viewshed_root(preset_path).join(&digest);
    let png = workdir.join("splat.png");
    if png.is_file() {
        return Ok(png);
    }
    if crate::viewshed_index::adopt_coord_noise_viewshed(preset_path, &workdir, &req)? {
        return Ok(png);
    }
    fs::create_dir_all(&workdir)?;
    let req_json = serde_json::to_string_pretty(&req)?;
    fs::write(workdir.join("request.json"), req_json)?;
    let t0 = Instant::now();
    let on_progress: Option<&(dyn Fn(f64, &str) + Send + Sync)> = hooks
        .and_then(|h| h.progress.as_ref())
        .map(|arc| arc.as_ref());
    let cancel: Option<&AtomicBool> = hooks
        .and_then(|h| h.cancel.as_ref())
        .map(|arc| arc.as_ref());
    let run_ctx = CoverageRunContext {
        verbose,
        on_progress,
        cancel,
    };
    session.run_with_context(&workdir, run_ctx)?;
    if cancel.is_some_and(|c| c.load(Ordering::Relaxed)) {
        bail!("viewshed cancelled");
    }
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

pub fn viewshed_digest_for_site(
    preset: &Preset,
    site: &SiteEntry,
    board_viewshed: Option<&BoardViewshedResolver>,
) -> Result<String> {
    let req = preset_to_request_with_sim(
        preset,
        site.loc[0],
        site.loc[1],
        Some(site),
        None,
        board_viewshed,
    )?;
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
    let workdir = resolve_viewshed_workdir(preset_path, preset, &site, sim, None)?;
    let digest = viewshed_workspace_digest(&preset_to_request_with_sim(
        preset,
        lat,
        lon,
        Some(&site),
        sim,
        None,
    )?)?;
    let png = workdir.join("splat.png");
    if !png.is_file() {
        return Ok(None);
    }
    let bounds = load_bounds_from_manifest(&workdir.join("manifest.json"));
    let Some(bounds) = bounds else {
        return Ok(None);
    };
    let coordinates = image_coordinates_from_bbox(&bounds)?;
    Ok(Some(serde_json::json!({
        "slug": DRAFT_VIEWSHED_SLUG,
        "digest": digest,
        "url": viewshed_cache_png_api_path(project_slug, &digest),
        "coordinates": coordinates,
        "lat": lat,
        "lon": lon,
        "raster_dimension": target_raster,
    })))
}

pub fn read_coords_viewshed_png_if_ready(
    preset_path: &Path,
    preset: &Preset,
    lat: f64,
    lon: f64,
    sim: Option<&ViewshedSimOverrides>,
) -> Result<Option<PathBuf>> {
    let site = preview_site_at(lat, lon).map_err(|e| anyhow::anyhow!(e.0))?;
    let workdir = resolve_viewshed_workdir(preset_path, preset, &site, sim, None)?;
    let png = workdir.join("splat.png");
    if png.is_file() {
        Ok(Some(png))
    } else {
        Ok(None)
    }
}
