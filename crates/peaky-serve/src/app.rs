//! Axum HTTP application.

use std::net::SocketAddr;
use std::sync::Arc;

use anyhow::{Context, Result};
use axum::Router;
use peaky_geo::prepare_land_at_boot;
use peaky_preset::{
    load_preset, resolved_coverage_max_workers, resolved_dem_fetch_max_workers,
    resolved_skadi_mirror_dir_for_project,
};
use splatter::Session;
use tokio::sync::Semaphore;
use tower_http::trace::TraceLayer;

use crate::api;
use crate::alternates::AlternatesHub;
use crate::fortify::FortifyHub;
use crate::link_solver::LinkSolverHub;
use crate::state::AppState;
use crate::static_files;
use crate::warm::WarmHub;

pub fn router(state: AppState) -> Router {
    Router::new()
        .merge(static_files::router())
        .merge(api::router())
        .layer(TraceLayer::new_for_http())
        .with_state(state)
}

pub async fn run_server(
    host: &str,
    port: u16,
    verbose: bool,
    project: &std::path::Path,
    land_refresh: bool,
) -> Result<()> {
    let project_dir = peaky_preset::resolve_project_dir(&project.to_string_lossy());
    let is_new = peaky_preset::project_needs_init(&project_dir);
    let project_dir = peaky_preset::ensure_project_initialized(project)?;
    let preset_path = project_dir.join("config.yaml");
    let slug = peaky_preset::resolved_preset_slug(&preset_path);
    if is_new {
        tracing::info!(
            "initialized new project {} ({}) with MeshCore defaults",
            slug,
            project_dir.display()
        );
    }
    tracing::info!("project {} ({})", slug, project_dir.display());

    if land_refresh {
        tracing::info!("land: validating sources and refreshing stale data");
        let boot_preset = preset_path.clone();
        let summary = tokio::task::spawn_blocking(move || prepare_land_at_boot(&boot_preset, verbose))
            .await
            .context("land boot prepare task")?
            .context("land boot prepare")?;
        if !summary.failed.is_empty() {
            for failure in &summary.failed {
                tracing::warn!(
                    source_id = %failure.source_id,
                    error = %failure.error,
                    "land refresh failed (non-fatal)"
                );
            }
        }
    }

    let preset = load_preset(&preset_path)?;
    let mirror = resolved_skadi_mirror_dir_for_project(&project_dir);
    std::fs::create_dir_all(&mirror)?;
    let dem_workers = resolved_dem_fetch_max_workers(&preset);
    let coverage_workers = resolved_coverage_max_workers(&preset);
    let session = Arc::new(Session::new(mirror, verbose, dem_workers));
    let events = crate::events::ServeEventHub::default();
    let state = AppState {
        session: session.clone(),
        events: events.clone(),
        warm: WarmHub::new(session.clone(), events, verbose, coverage_workers),
        link_solver: LinkSolverHub::new(Arc::clone(&session), verbose),
        alternates: AlternatesHub::new(Arc::clone(&session), verbose),
        fortify: FortifyHub::new(session, verbose),
        verbose,
        dem_tile_render: Arc::new(Semaphore::new(crate::state::DEM_TILE_RENDER_PERMITS)),
        project_dir: project_dir.clone(),
        slug,
    };

    let app = router(state);

    let addr: SocketAddr = format!("{host}:{port}").parse()?;
    tracing::info!("peaky serve listening on http://{addr}");
    if verbose {
        tracing::info!("DemMirror console log enabled (--verbose)");
    } else if std::env::var("PEAKY_DEM_MIRROR_LOG")
        .ok()
        .is_some_and(|v| matches!(v.trim(), "1" | "true" | "yes" | "on"))
    {
        tracing::info!("DemMirror console log enabled (PEAKY_DEM_MIRROR_LOG)");
    }
    let listener = tokio::net::TcpListener::bind(addr).await?;
    axum::serve(listener, app).await?;
    Ok(())
}
