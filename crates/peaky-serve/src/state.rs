//! Shared application state.

use std::sync::Arc;

use splatter::Session;
use tokio::sync::Semaphore;

use crate::events::ServeEventHub;
use crate::seek::SeekHub;
use crate::warm::WarmHub;

/// Cap concurrent Skadi map-tile renders so pan/zoom cannot starve the server.
pub const DEM_TILE_RENDER_PERMITS: usize = 4;

#[derive(Clone)]
pub struct AppState {
    pub session: Arc<Session>,
    pub events: ServeEventHub,
    pub warm: WarmHub,
    pub seek: SeekHub,
    pub verbose: bool,
    pub dem_tile_render: Arc<Semaphore>,
}
