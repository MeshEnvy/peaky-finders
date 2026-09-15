//! On-demand coverage-guided route solver.

use std::sync::Arc;
use std::path::Path;

use anyhow::Result;
use splatter::session::Session;

use crate::cache::FinderCache;
use crate::candidates::CandidateRegistry;
use crate::route::Waypoint;
use crate::search::search_route;
use crate::telemetry::CacheLedger;
use crate::viewshed_gain::path_viewshed_gain;

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct SolveResult {
    pub path_indices: Vec<usize>,
    pub segment_count: usize,
    pub viewshed_popcount: u32,
}

pub fn solve_route(
    preset_path: &Path,
    session: Arc<Session>,
    registry: &mut CandidateRegistry,
    waypoints: &[Waypoint],
    allow_tags: &[String],
    cache: &FinderCache,
    ledger: &CacheLedger,
) -> Result<SolveResult> {
    let (path_indices, segment_count) = search_route(
        preset_path,
        session.clone(),
        registry,
        waypoints,
        allow_tags,
        cache,
        ledger,
    )?;
    let viewshed_popcount = path_viewshed_gain(
        preset_path,
        session.as_ref(),
        registry.as_slice(),
        &path_indices,
        cache,
        ledger,
    )?;
    Ok(SolveResult {
        path_indices,
        segment_count,
        viewshed_popcount,
    })
}
