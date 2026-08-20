//! `peaky find path` subcommand.

use std::path::Path;
use std::sync::Arc;

use anyhow::Result;
use peaky_finder::{new_session, run_find_path, FindPathJob, FinderWatchHub, FinderWatchServer};

pub async fn run_path(
    project: &str,
    route: &Path,
    name_prefix: &str,
    tags: &[String],
    allow_tags: &[String],
    dry_run: bool,
    quiet: bool,
    simplify_m: f64,
    watch: bool,
    watch_port: Option<u16>,
) -> Result<()> {
    let job = FindPathJob::from_cli(
        project,
        route,
        name_prefix,
        tags,
        allow_tags,
        dry_run,
        quiet,
        simplify_m,
    )?;
    let session = new_session(!quiet);
    let watch_hub = if watch {
        Some(Arc::new(FinderWatchHub::new()))
    } else {
        None
    };
    let server = if let Some(ref hub) = watch_hub {
        let s = FinderWatchServer::start(
            hub.clone(),
            watch_port,
            session.clone(),
            job.preset_path.clone(),
        )
        .await?;
        eprintln!(
            "Finder watch: {} (open now for live map; safe to connect mid-run)",
            s.url()
        );
        Some(s)
    } else {
        None
    };
    let outcome = run_find_path(&job, watch_hub, session)?;
    if !quiet {
        eprintln!(
            "finder done: route={} waypoints={} chain_sites={} new_sites={} cached={}",
            outcome.route_name,
            outcome.waypoint_count,
            outcome.plan.chain_slugs.len(),
            outcome.plan.new_sites.len(),
            outcome.from_cache
        );
    }

    if let Some(server) = server {
        eprintln!("Finder complete. Watch map stays open until you exit.");
        server.wait_shutdown().await?;
    }
    Ok(())
}
