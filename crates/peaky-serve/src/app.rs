//! Axum HTTP application.

use std::net::SocketAddr;
use std::sync::Arc;

use anyhow::Result;
use axum::Router;
use peaky_preset::resolved_skadi_mirror_dir;
use splatter::Session;
use tokio::sync::Semaphore;
use tower_http::trace::TraceLayer;

use crate::api;
use crate::state::AppState;
use crate::seek::SeekHub;
use crate::warm::WarmHub;
use crate::static_files;

pub fn router(state: AppState) -> Router {
    Router::new()
        .merge(static_files::router())
        .merge(api::router())
        .layer(TraceLayer::new_for_http())
        .with_state(state)
}

pub async fn run_server(host: &str, port: u16, verbose: bool) -> Result<()> {
    let mirror = resolved_skadi_mirror_dir();
    std::fs::create_dir_all(&mirror)?;
    let session = Arc::new(Session::new(mirror, verbose));
    let events = crate::events::ServeEventHub::default();
    let state = AppState {
        session: session.clone(),
        events: events.clone(),
        warm: WarmHub::new(session.clone(), events, verbose),
        seek: SeekHub::new(session, verbose),
        verbose,
        dem_tile_render: Arc::new(Semaphore::new(crate::state::DEM_TILE_RENDER_PERMITS)),
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

pub fn default_projects_dir() -> std::path::PathBuf {
    peaky_preset::peaky_projects_dir()
}
