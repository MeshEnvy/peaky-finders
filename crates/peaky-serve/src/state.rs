//! Shared application state.

use std::path::PathBuf;
use std::sync::Arc;

use peaky_preset::BoardViewshedResolver;
use splatter::Session;
use tokio::sync::Semaphore;

use crate::events::ServeEventHub;
use crate::link_solver::LinkSolverHub;
use crate::warm::WarmHub;

/// Cap concurrent Skadi map-tile renders so pan/zoom cannot starve the server.
pub const DEM_TILE_RENDER_PERMITS: usize = 4;

#[derive(Clone)]
pub struct AppState {
    pub session: Arc<Session>,
    pub events: ServeEventHub,
    pub warm: WarmHub,
    pub link_solver: LinkSolverHub,
    pub alternates: crate::alternates::AlternatesHub,
    pub fortify: crate::fortify::FortifyHub,
    pub verbose: bool,
    pub dem_tile_render: Arc<Semaphore>,
    /// ``nodes.yaml`` + ``boards.yaml`` for per-board viewshed radius.
    pub board_viewshed: Arc<BoardViewshedResolver>,
    /// Directory that contains `config.yaml`.
    pub project_dir: PathBuf,
    /// Display / cache key (directory name).
    pub slug: String,
}

impl AppState {
    pub fn preset_path(&self) -> PathBuf {
        self.project_dir.join("config.yaml")
    }
}
