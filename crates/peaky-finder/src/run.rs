//! Orchestrate a full find-path run.

use std::sync::Arc;

use anyhow::{Context, Result};
use peaky_preset::{load_preset, resolved_skadi_mirror_dir};
use serde::{Deserialize, Serialize};
use serde_json::json;
use splatter::session::Session;

use crate::cache::{digest_hex, short_digest, FinderCache};
use crate::candidates::{build_candidates, path_candidates, restore_path_from_cached, sites_version_digest, Candidate, CandidateKind};
use crate::job::FindPathJob;
use crate::patch::{apply_patch, build_patch_plan, print_patch_diff, PatchPlan};
use crate::route::{load_route, route_kml_digest};
use crate::solve::{solve_route, SolveResult};
use crate::telemetry::CacheLedger;
use crate::watch::FinderWatchHub;
use crate::CACHE_SCHEMA_VERSION;

#[derive(Debug, Clone, Serialize, Deserialize)]
struct RunCachePayload {
    /// Chain sites (stable coords/slugs — not registry indices).
    path: Vec<Candidate>,
    segment_count: usize,
    viewshed_popcount: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FindPathOutcome {
    pub route_name: String,
    pub waypoint_count: usize,
    pub plan: PatchPlan,
    pub solve: SolveResult,
    pub run_digest: String,
    pub from_cache: bool,
}

/// Skadi analysis session (shared by finder + watch map when `--watch`).
pub fn new_session(verbose: bool) -> Arc<Session> {
    Arc::new(Session::new(resolved_skadi_mirror_dir(), verbose))
}

pub fn run_find_path(
    job: &FindPathJob,
    watch_hub: Option<Arc<FinderWatchHub>>,
    session: Arc<Session>,
) -> Result<FindPathOutcome> {
    let mut ledger = CacheLedger::new(job.quiet);
    if let Some(hub) = watch_hub {
        ledger.attach_watch(hub);
    }
    ledger.phase_start("find path");

    let route = load_route(&job.route_path, job.simplify_m)?;
    ledger.progress(&format!(
        "route \"{}\" waypoints={}",
        route.name,
        route.waypoints.len()
    ));
    if let Some(hub) = ledger.watch() {
        hub.publish(
            "route",
            json!({
                "name": route.name,
                "coordinates": route.waypoints.iter().map(|wp| json!([wp.lon, wp.lat])).collect::<Vec<_>>(),
            }),
        );
    }
    let route_digest = route_kml_digest(&job.route_path)?;
    let preset = load_preset(&job.preset_path).context("load preset")?;
    let sites_ver = sites_version_digest(&preset);

    let run_digest = digest_hex(&[
        "run",
        &route_digest,
        &job.allow_tags.join(","),
        &job.name_prefix,
        &job.new_tags.join(","),
        &sites_ver,
        &format!("simplify={}", job.simplify_m),
        &format!("schema={CACHE_SCHEMA_VERSION}"),
    ]);

    let cache = FinderCache::new(&job.preset_path)?;

    let mut registry = build_candidates(
        &job.preset_path,
        &route.waypoints,
        &job.allow_tags,
        &cache,
        &ledger,
    )?;

    let run_key = digest_hex(&["run_result", &run_digest]);
    let run_cache_path = cache.path_for("runs", &run_key, "json");
    let cached: Option<RunCachePayload> = if run_cache_path.is_file() {
        match std::fs::read_to_string(&run_cache_path)
            .ok()
            .and_then(|text| serde_json::from_str(&text).ok())
        {
            Some(hit) => {
                ledger.log_hit(
                    "run",
                    &format!(
                        "job={} sites_ver={} → skip solve",
                        short_digest(&run_digest),
                        short_digest(&sites_ver)
                    ),
                );
                Some(hit)
            }
            None => {
                ledger.progress("run cache stale (legacy format) — re-solving");
                None
            }
        }
    } else {
        None
    };

    let (solve, from_cache) = if let Some(hit) = cached {
        let path_indices = restore_path_from_cached(&mut registry, &hit.path)?;
        (
            SolveResult {
                path_indices,
                segment_count: hit.segment_count,
                viewshed_popcount: hit.viewshed_popcount,
            },
            true,
        )
    } else {
        let solve = solve_route(
            &job.preset_path,
            session.clone(),
            &mut registry,
            &route.waypoints,
            &cache,
            &ledger,
        )?;
        let payload = RunCachePayload {
            path: path_candidates(&registry, &solve.path_indices)?,
            segment_count: solve.segment_count,
            viewshed_popcount: solve.viewshed_popcount,
        };
        if let Some(parent) = run_cache_path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        std::fs::write(&run_cache_path, serde_json::to_string_pretty(&payload)?)?;
        ledger.log_miss(
            "run",
            &format!("job={}", short_digest(&run_digest)),
            std::time::Duration::ZERO,
        );
        (solve, false)
    };

    let preset = load_preset(&job.preset_path)?;
    let plan = build_patch_plan(job, &preset, registry.as_slice(), &solve);

    if let Some(hub) = ledger.watch() {
        let chain_sites: Vec<_> = solve
            .path_indices
            .iter()
            .map(|&idx| {
                let cand = registry.get(idx).expect("path index");
                json!({
                    "lat": cand.lat,
                    "lon": cand.lon,
                    "name": cand.slug.as_deref().unwrap_or(&cand.id),
                    "kind": match cand.kind {
                        CandidateKind::Existing => "existing",
                        CandidateKind::Peak => "peak",
                    },
                })
            })
            .collect();
        hub.publish("chain", json!({ "sites": chain_sites }));
        hub.publish(
            "done",
            json!({
                "chain_sites": plan.chain_slugs.len(),
                "new_sites": plan.new_sites.len(),
                "from_cache": from_cache,
            }),
        );
    }

    if job.dry_run {
        print_patch_diff(&plan);
    } else {
        apply_patch(
            &job.preset_path,
            &plan,
            &run_digest,
            solve.viewshed_popcount,
        )?;
    }

    cache.write_ledger(&run_digest, &ledger)?;
    ledger.print_summary();
    ledger.phase_end(
        "find path",
        &format!(
            "sites={} new={} cached={from_cache}",
            plan.chain_slugs.len(),
            plan.new_sites.len()
        ),
    );

    Ok(FindPathOutcome {
        route_name: route.name,
        waypoint_count: route.waypoints.len(),
        plan,
        solve,
        run_digest,
        from_cache,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    use crate::route::load_route;

    #[test]
    fn run_digest_stable() {
        let a = digest_hex(&["run", "route", "installed", "Site"]);
        let b = digest_hex(&["run", "route", "installed", "Site"]);
        assert_eq!(a, b);
    }

    #[test]
    fn silver_triangle_fixture_parses() {
        let fixture = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../../tests/fixtures/routes/onx-silver-triangle.kml");
        if !fixture.is_file() {
            return;
        }
        let route = load_route(&fixture, 0.0).expect("parse route");
        assert_eq!(route.waypoints.len(), 17);
        assert_eq!(route.waypoints.len().saturating_sub(1), 16);
    }
}
