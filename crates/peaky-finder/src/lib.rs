//! Auto-finder: minimum-site RF chain covering ordered route waypoints.

pub mod cache;
pub mod candidates;
pub mod coverage;
pub mod job;
pub mod patch;
pub mod route;
pub mod run;
pub mod search;
pub mod solve;
pub mod telemetry;
pub mod viewshed_gain;
pub mod watch;

pub use job::FindPathJob;
pub use run::{run_find_path, FindPathOutcome, new_session};
pub use watch::{FinderWatchHub, FinderWatchServer};

pub const CACHE_SCHEMA_VERSION: u32 = 8;
pub const PEAK_ALGO_VERSION: u32 = 1;
pub const ROUTE_DEDUPE_M: f64 = 50.0;
pub const SITE_PEAK_DEDUP_M: f64 = 500.0;
