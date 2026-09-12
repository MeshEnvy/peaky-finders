//! Peaky HTTP server — axum app, static assets, RF viewshed orchestration.

mod alternates;
mod api;
mod fortify;
mod app;
mod embed;
mod events;
mod html;
mod land;
mod peaks;
mod peaks_cache;
mod links;
pub mod rf;
mod seek;
mod seek_rank;
mod seek_plan;
mod seek_progress;
mod simulation;
mod site_prefetch;
mod state;
mod static_files;
pub mod viewshed;
pub mod viewshed_index;
mod viewshed_log;
mod viewshed_sim;
mod warm;

pub use app::{router, run_server};
pub use events::ServeEventHub;
pub use state::AppState;
pub use viewshed_sim::ViewshedSimOverrides;
pub use viewshed_index::load_bounds_from_manifest;
