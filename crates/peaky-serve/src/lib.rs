//! Peaky HTTP server — axum app, static assets, RF viewshed orchestration.

mod alternates;
mod api;
mod app;
mod embed;
mod events;
mod html;
mod land;
mod peaks;
mod links;
pub mod rf;
mod seek;
mod seek_path;
mod seek_rank;
mod seek_plan;
mod seek_progress;
mod simulation;
mod site_prefetch;
mod state;
mod static_files;
pub mod viewshed;
mod viewshed_index;
mod viewshed_log;
mod viewshed_sim;
mod warm;

pub use app::{router, run_server};
pub use events::ServeEventHub;
pub use state::AppState;
pub use viewshed_sim::ViewshedSimOverrides;
