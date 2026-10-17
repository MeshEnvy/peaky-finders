//! ``peaky peaks`` orchestration.

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::time::Instant;

use anyhow::{Context, Result};
use geo::Geometry;
use peaky_geo::{
    collect_role_geometry, eligible_land_dem_mask_dir, load_or_build_eligible_land_parts,
    LonLatBBox,
};
use peaky_preset::LandLayerRole;
use peaky_preset::{
    index_row_from_probe, list_stale_cache_dirs, load_peak_overrides, load_peaks_index,
    load_preset, near_denied, place_slugs, prefs_key_for_project, resolved_dem_fetch_max_workers,
    resolved_skadi_mirror_dir_for_project, write_peaks_index, PeakAccessRules, PeakKind,
    PeaksIndex, PeaksManifest, Preset, ACCESS_ALGO_VERSION, PEAK_ALGO_VERSION,
};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::Arc;
use std::thread;
use std::time::Duration;

use rayon::prelude::*;
use splatter::peaks::LandFilterIndex;
use splatter::Session;
use tracing::info;

use crate::access_probe::{filter_pass_from_probe, probe_drop_reason};
use crate::access_warm::{
    load_routing_for_bbox, project_pbf_fingerprint, resolve_probe, ProjectRouting,
};
use crate::catalog_filter::{FilterDrop, FilterPass};
use crate::corridor::{
    corridor_polygon, load_polygon_file, parse_corridor_coords, point_in_region,
    resolve_corridor_sites, ScanRegion, MI_TO_M,
};
use crate::gnis::load_gnis_candidates;
use crate::hike::{
    format_hike_report, haversine_m, neighbor_prominence_m, snap_to_dem_local_max_filtered,
    HikeSampleElev,
};
use crate::jeep::JeepLengthBudget;
use crate::osm::OsmRouting;
use crate::trail::TrailRouting;
use crate::universe::{dedup_nearby, seed_candidates_from_sites, slug_for_candidate, RawCandidate};
use peaky_preset::{AccessMeta, PeaksScanConfig};

/// Upsert each peak+access file every N newly freshened/inserted peaks during a build.
const PROGRESS_WRITE_EVERY: usize = 10;
const FILTER_PROGRESS_EVERY: usize = 25;
const FILTER_HEARTBEAT_SECS: u64 = 10;
const SURVIVOR_DEDUP_EVERY: usize = 500;

#[derive(Debug, Clone, Copy)]
pub(crate) struct PeaksFilterParams {
    pub candidate_dedup_m: f64,
    pub summit_snap_m: f64,
    pub min_prominence_m: f64,
}

impl From<&PeaksScanConfig> for PeaksFilterParams {
    fn from(p: &PeaksScanConfig) -> Self {
        Self {
            candidate_dedup_m: p.candidate_dedup_m,
            summit_snap_m: p.summit_snap_m,
            min_prominence_m: p.min_prominence_m,
        }
    }
}

fn peaks_filter_workers() -> usize {
    std::env::var("PEAKY_PEAKS_WORKERS")
        .ok()
        .and_then(|v| v.parse().ok())
        .filter(|n| *n >= 1)
        .unwrap_or_else(|| {
            std::thread::available_parallelism()
                .map(|p| p.get())
                .unwrap_or(4)
        })
}

struct SessionElev<'a>(&'a Session);

impl HikeSampleElev for SessionElev<'_> {
    fn sample_elev_m(&self, lat: f64, lon: f64) -> f64 {
        self.0.sample_elev_m(lat, lon)
    }
}

#[derive(Debug, Clone)]
pub struct PeaksBuildOptions {
    pub bbox: Option<(f64, f64, f64, f64)>,
    pub polygon: Option<PathBuf>,
    pub corridor: Option<(f64, f64, f64, f64)>,
    pub corridor_width_mi: f64,
    pub force_osm: bool,
    pub verbose: bool,
    /// Stop after N qualifying peaks (serial filter; prints hike profile for each).
    pub stop_after: Option<usize>,
    /// Wipe catalog before this scan (default: merge/freshen existing entries).
    pub clean: bool,
}

#[derive(Debug, Clone, Default)]
pub struct PeaksBuildSummary {
    pub kept: usize,
    pub denied: usize,
    pub dropped_land: usize,
    pub dropped_road: usize,
    pub dropped_hike: usize,
    pub dropped_slope: usize,
    pub dropped_jeep: usize,
    pub dropped_dedup: usize,
    pub dropped_region: usize,
    pub gnis: usize,
    pub site_seeds: usize,
    pub dem: usize,
    pub max_slope_deg: f64,
    pub entries: usize,
    pub output: PathBuf,
}

#[derive(Default)]
struct DropCounts {
    land: usize,
    road: usize,
    hike: usize,
    slope: usize,
    jeep: usize,
    dedup: usize,
    region: usize,
}

fn park_within_hike_reach(
    project_routing: &ProjectRouting,
    rules: &PeakAccessRules,
    meta: &AccessMeta,
    jeep_budget: &JeepLengthBudget,
    lat: f64,
    lon: f64,
) -> bool {
    let reach_m = meta.hike_path_max_m + 50.0;
    let trees = project_routing.trees(rules.max_jeep_m, jeep_budget);
    let osm = project_routing.osm();
    if osm
        .graph_nodes
        .nodes_within(lat, lon, reach_m)
        .into_iter()
        .any(|n| trees.budgeted.contains(n))
    {
        return true;
    }
    project_routing
        .trails_opt()
        .is_some_and(|t| !t.samples.points_within(lat, lon, reach_m).is_empty())
}

pub(crate) fn filter_candidate(
    cand: &RawCandidate,
    project_dir: &Path,
    session: &Session,
    land: &LandFilterIndex,
    project_routing: &ProjectRouting,
    scan_region: &ScanRegion,
    rules: &PeakAccessRules,
    meta: &AccessMeta,
    scan: &PeaksScanConfig,
    filter: PeaksFilterParams,
) -> Result<FilterPass, FilterDrop> {
    let elev = SessionElev(session);
    let Some((peak_lat, peak_lon, peak_elev)) = snap_to_dem_local_max_filtered(
        &elev,
        cand.lat,
        cand.lon,
        filter.summit_snap_m,
        15.0,
        |lat, lon| land.contains(lon, lat),
    ) else {
        return Err(FilterDrop::Land);
    };
    if !point_in_region(scan_region, peak_lat, peak_lon) {
        return Err(FilterDrop::Region);
    }
    if cand.source == "dem"
        && neighbor_prominence_m(&elev, peak_lat, peak_lon, 30.0) < filter.min_prominence_m
    {
        return Err(FilterDrop::Land);
    }
    let jeep_budget = JeepLengthBudget::from_extreme(&rules.difficulty.jeep.extreme);
    // Cheap catalog pre-gate: eligibility needs a hike ≤ hike_path_max_m, and any
    // hike is at least the crow distance from its trailhead. If no paved-reachable
    // jeep node and no foot trail lies within that radius, the peak cannot be
    // eligible; skip the full probe (whose ungated recommended leg runs a
    // multi-km bushwhack A* for every far candidate).
    if !park_within_hike_reach(
        project_routing,
        rules,
        meta,
        &jeep_budget,
        peak_lat,
        peak_lon,
    ) {
        return Err(FilterDrop::Jeep);
    }
    let Some(probe_result) = resolve_probe(
        project_dir,
        &elev,
        project_routing,
        meta,
        scan,
        peak_lat,
        peak_lon,
        rules.max_jeep_m,
        rules.max_slope_deg,
        jeep_budget,
    ) else {
        return Err(FilterDrop::Jeep);
    };
    if !probe_result.eligible {
        return Err(probe_drop_reason(&probe_result));
    }
    filter_pass_from_probe(cand, peak_lat, peak_lon, peak_elev, &probe_result)
}

pub fn build_peaks_catalog(
    preset_path: &Path,
    opts: PeaksBuildOptions,
) -> Result<PeaksBuildSummary> {
    let t0 = Instant::now();
    let preset = load_preset(preset_path).context("load preset")?;
    let project_dir = preset_path.parent().context("preset path has no parent")?;

    let (bbox, scan_region) = resolve_scan(preset_path, &preset, &opts)?;
    match &scan_region {
        ScanRegion::BboxOnly => info!(
            west = bbox.west,
            south = bbox.south,
            east = bbox.east,
            north = bbox.north,
            "peaks: scan bbox"
        ),
        ScanRegion::Clip(_) => info!(
            west = bbox.west,
            south = bbox.south,
            east = bbox.east,
            north = bbox.north,
            "peaks: scan corridor polygon (tile bbox above)"
        ),
    }

    let land_parts =
        load_or_build_eligible_land_parts(preset_path, Some(bbox)).context("eligible land")?;
    let land_index = Arc::new(land_parts.index());
    info!(
        digest = %land_parts.digest,
        empty = land_parts.is_empty(),
        "peaks: eligible land ready"
    );

    let peaks_cfg = preset.scan.peaks.clone();
    let filter_params = PeaksFilterParams::from(&peaks_cfg);
    let peak_bin_m = preset.scan.peak_bin_size_m;

    let access_meta = preset.resolved_access_meta();
    let osm_opts = crate::osm::OsmRoutingOpts::from(&access_meta);
    info!("peaks: loading OSM + GTLF routing");
    let loaded = load_routing_for_bbox(
        project_dir,
        bbox.west,
        bbox.south,
        bbox.east,
        bbox.north,
        opts.force_osm,
        &osm_opts,
    )?;
    let project_routing = Arc::new(loaded);
    let gtlf_fingerprint = project_routing.gtlf_fingerprint.clone();
    let pbf_fingerprint = project_pbf_fingerprint(project_dir);
    let prefs_key = prefs_key_for_project(preset_path, &gtlf_fingerprint, &pbf_fingerprint)?;
    info!(
        gtlf = %gtlf_fingerprint,
        pbf = %pbf_fingerprint,
        prefs_key = %prefs_key,
        "peaks: OSM + GTLF routing ready"
    );

    let mirror = resolved_skadi_mirror_dir_for_project(project_dir);
    let dem_workers = resolved_dem_fetch_max_workers(&preset);
    let session = Arc::new(Session::new(mirror, opts.verbose, dem_workers));
    let missing_dem = session
        .missing_tiles_for_bounds(bbox.west, bbox.south, bbox.east, bbox.north)
        .context("count DEM tiles for bbox")?;
    info!(
        missing_tiles = missing_dem.len(),
        workers = dem_workers,
        "peaks: preloading DEM tiles"
    );
    let t_dem = Instant::now();
    session
        .ensure_tiles_for_bounds(bbox.west, bbox.south, bbox.east, bbox.north)
        .context("preload DEM for bbox")?;
    info!(
        elapsed_secs = t_dem.elapsed().as_secs_f64(),
        "peaks: DEM preload done"
    );

    let rules = preset.peak_access_rules();
    let hike_path_max_m = access_meta.hike_path_max_m;
    info!(
        max_hike_m = rules.max_hike_m,
        hike_path_max_m,
        max_slope_grade_pct = rules.max_slope_grade_pct,
        max_slope_deg = rules.max_slope_deg,
        max_jeep_m = rules.max_jeep_m,
        algo_version = access_meta.algo_version,
        "peaks: access rules"
    );

    let mut universe: Vec<RawCandidate> = Vec::new();
    let site_seeds = seed_candidates_from_sites(&preset, &preset.scan.peaks.seeds.site_tags);
    info!(
        count = site_seeds.len(),
        tags = ?peaks_cfg.seeds.site_tags,
        "peaks: site seeds"
    );
    universe.extend(site_seeds.clone());

    let gnis = if peaks_cfg.seeds.gnis {
        load_gnis_candidates(
            project_dir,
            &preset.sources.gnis,
            bbox.west,
            bbox.south,
            bbox.east,
            bbox.north,
        )?
    } else {
        info!("peaks: GNIS seeds disabled in config");
        Vec::new()
    };
    info!(count = gnis.len(), "peaks: GNIS candidates");
    universe.extend(gnis.clone());

    let mask_dir = eligible_land_dem_mask_dir(preset_path, &land_parts.digest);
    info!(
        bin_size_m = peak_bin_m,
        min_prominence_m = peaks_cfg.min_prominence_m,
        "peaks: scanning DEM peaks on eligible land"
    );
    let t_dem_scan = Instant::now();
    let dem_peaks = load_dem_candidates(
        &session,
        land_index.clone(),
        bbox,
        &mask_dir,
        peak_bin_m,
        peaks_cfg.min_prominence_m,
    )?;
    info!(
        count = dem_peaks.len(),
        elapsed_secs = t_dem_scan.elapsed().as_secs_f64(),
        "peaks: DEM candidates on eligible land"
    );
    universe.extend(dem_peaks.clone());

    let pre_region = universe.len();
    universe.retain(|c| point_in_region(&scan_region, c.lat, c.lon));
    info!(
        count = universe.len(),
        dropped = pre_region.saturating_sub(universe.len()),
        "peaks: universe after region clip"
    );
    universe = dedup_nearby(universe, filter_params.candidate_dedup_m);
    universe.sort_by(|a, b| {
        b.elev_m
            .unwrap_or(0.0)
            .partial_cmp(&a.elev_m.unwrap_or(0.0))
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    info!(count = universe.len(), "peaks: universe after dedup");

    let overrides = load_peak_overrides(preset_path)?;
    let denied = overrides.overrides.iter().filter(|o| o.deny).count();
    let previous = load_peaks_index(project_dir, &prefs_key)?;
    let full_aoi = is_full_aoi_scan(&opts);
    let replace_catalog = opts.clean || full_aoi;
    let mut index = PeaksIndex {
        manifest: PeaksManifest {
            generated_at: chrono_date(),
            peak_algo_version: PEAK_ALGO_VERSION,
            access_algo_version: ACCESS_ALGO_VERSION,
            prefs_key: prefs_key.clone(),
            pbf_fingerprint: pbf_fingerprint.clone(),
            gtlf_fingerprint: gtlf_fingerprint.clone(),
            land_digest: land_parts.digest.clone(),
            scanned_bbox: Some([bbox.west, bbox.south, bbox.east, bbox.north]),
            rules: rules.clone(),
        },
        peaks: if replace_catalog {
            HashMap::new()
        } else {
            previous.peaks.clone()
        },
    };
    info!(
        clean = opts.clean,
        full_aoi,
        replace_catalog,
        prior_entries = previous.peaks.len(),
        starting_entries = index.peaks.len(),
        denied,
        "peaks: index merge mode"
    );

    let max_slope_deg = rules.max_slope_deg;

    let (drops, deduped) = if let Some(limit) = opts.stop_after {
        info!(
            limit,
            candidates = universe.len(),
            "peaks: stop-after serial filter"
        );
        let mut drops = DropCounts::default();
        let mut kept_centroids: Vec<(f64, f64)> = Vec::new();
        let mut deduped = Vec::new();
        for (i, cand) in universe.iter().enumerate() {
            if deduped.len() >= limit {
                break;
            }
            if (i + 1) % FILTER_PROGRESS_EVERY == 0 || i + 1 == universe.len() {
                info!(
                    processed = i + 1,
                    total = universe.len(),
                    pct = ((i + 1) * 100 / universe.len().max(1)) as u32,
                    kept = deduped.len(),
                    land = drops.land,
                    road = drops.road,
                    hike = drops.hike,
                    slope = drops.slope,
                    jeep = drops.jeep,
                    "peaks: serial filter progress"
                );
            }
            match filter_candidate(
                cand,
                project_dir,
                session.as_ref(),
                land_index.as_ref(),
                project_routing.as_ref(),
                &scan_region,
                &rules,
                &access_meta,
                &peaks_cfg,
                filter_params,
            ) {
                Ok(pass) => {
                    if kept_centroids.iter().any(|(lat, lon)| {
                        haversine_m(pass.peak_lat, pass.peak_lon, *lat, *lon)
                            <= filter_params.candidate_dedup_m
                    }) {
                        drops.dedup += 1;
                        continue;
                    }
                    kept_centroids.push((pass.peak_lat, pass.peak_lon));
                    let slug_preview = pass.cand.name.as_deref().unwrap_or("peak");
                    eprintln!(
                        "{}",
                        format_hike_report(
                            pass.cand.name.as_deref(),
                            slug_preview,
                            &pass.hike_detail,
                        )
                    );
                    deduped.push(pass);
                }
                Err(FilterDrop::Land) => drops.land += 1,
                Err(FilterDrop::Region) => drops.region += 1,
                Err(FilterDrop::Road) => drops.road += 1,
                Err(FilterDrop::Hike) => drops.hike += 1,
                Err(FilterDrop::Slope) => drops.slope += 1,
                Err(FilterDrop::Jeep) => drops.jeep += 1,
            }
        }
        (drops, deduped)
    } else {
        let filter_workers = peaks_filter_workers();
        info!(
            workers = filter_workers,
            candidates = universe.len(),
            "peaks: parallel filter"
        );

        let pool = rayon::ThreadPoolBuilder::new()
            .num_threads(filter_workers)
            .build()
            .context("build peaks filter thread pool")?;

        let scan_region = Arc::new(scan_region);
        let rules = Arc::new(rules);
        let access_meta = Arc::new(access_meta.clone());
        let peaks_cfg = Arc::new(peaks_cfg.clone());
        let project_routing = Arc::clone(&project_routing);
        let processed = Arc::new(AtomicUsize::new(0));
        let drop_land = Arc::new(AtomicUsize::new(0));
        let drop_region = Arc::new(AtomicUsize::new(0));
        let drop_road = Arc::new(AtomicUsize::new(0));
        let drop_hike = Arc::new(AtomicUsize::new(0));
        let drop_slope = Arc::new(AtomicUsize::new(0));
        let drop_jeep = Arc::new(AtomicUsize::new(0));
        let universe_len = universe.len();
        let filter_started = Instant::now();
        let filter_done = Arc::new(AtomicBool::new(false));
        let hb_done = Arc::clone(&filter_done);
        let hb_processed = Arc::clone(&processed);
        let hb_drop_land = Arc::clone(&drop_land);
        let hb_drop_road = Arc::clone(&drop_road);
        let hb_drop_hike = Arc::clone(&drop_hike);
        let hb_drop_slope = Arc::clone(&drop_slope);
        let hb_drop_jeep = Arc::clone(&drop_jeep);
        let hb_total = universe_len;
        let hb_started = filter_started;
        let heartbeat = thread::spawn(move || {
            while !hb_done.load(Ordering::Relaxed) {
                thread::sleep(Duration::from_secs(FILTER_HEARTBEAT_SECS));
                if hb_done.load(Ordering::Relaxed) {
                    break;
                }
                let n = hb_processed.load(Ordering::Relaxed);
                if n >= hb_total {
                    break;
                }
                info!(
                    processed = n,
                    total = hb_total,
                    pct = (n * 100 / hb_total.max(1)) as u32,
                    land = hb_drop_land.load(Ordering::Relaxed),
                    road = hb_drop_road.load(Ordering::Relaxed),
                    hike = hb_drop_hike.load(Ordering::Relaxed),
                    slope = hb_drop_slope.load(Ordering::Relaxed),
                    jeep = hb_drop_jeep.load(Ordering::Relaxed),
                    elapsed_secs = hb_started.elapsed().as_secs_f64(),
                    "peaks: parallel filter heartbeat"
                );
            }
        });

        let processed_w = Arc::clone(&processed);
        let drop_land_w = Arc::clone(&drop_land);
        let drop_region_w = Arc::clone(&drop_region);
        let drop_road_w = Arc::clone(&drop_road);
        let drop_hike_w = Arc::clone(&drop_hike);
        let drop_slope_w = Arc::clone(&drop_slope);
        let drop_jeep_w = Arc::clone(&drop_jeep);

        let passes: Vec<FilterPass> = pool.install(|| {
            universe
                .par_iter()
                .filter_map(|cand| {
                    let n = processed_w.fetch_add(1, Ordering::Relaxed) + 1;
                    if n == 1 || n % FILTER_PROGRESS_EVERY == 0 || n == universe_len {
                        info!(
                            processed = n,
                            total = universe_len,
                            pct = (n * 100 / universe_len.max(1)) as u32,
                            land = drop_land_w.load(Ordering::Relaxed),
                            road = drop_road_w.load(Ordering::Relaxed),
                            hike = drop_hike_w.load(Ordering::Relaxed),
                            slope = drop_slope_w.load(Ordering::Relaxed),
                            jeep = drop_jeep_w.load(Ordering::Relaxed),
                            elapsed_secs = filter_started.elapsed().as_secs_f64(),
                            "peaks: parallel filter progress"
                        );
                    }

                    match filter_candidate(
                        cand,
                        project_dir,
                        session.as_ref(),
                        land_index.as_ref(),
                        project_routing.as_ref(),
                        scan_region.as_ref(),
                        rules.as_ref(),
                        access_meta.as_ref(),
                        peaks_cfg.as_ref(),
                        filter_params,
                    ) {
                        Ok(pass) => Some(pass),
                        Err(FilterDrop::Land) => {
                            drop_land_w.fetch_add(1, Ordering::Relaxed);
                            None
                        }
                        Err(FilterDrop::Region) => {
                            drop_region_w.fetch_add(1, Ordering::Relaxed);
                            None
                        }
                        Err(FilterDrop::Road) => {
                            drop_road_w.fetch_add(1, Ordering::Relaxed);
                            None
                        }
                        Err(FilterDrop::Hike) => {
                            drop_hike_w.fetch_add(1, Ordering::Relaxed);
                            None
                        }
                        Err(FilterDrop::Slope) => {
                            drop_slope_w.fetch_add(1, Ordering::Relaxed);
                            None
                        }
                        Err(FilterDrop::Jeep) => {
                            drop_jeep_w.fetch_add(1, Ordering::Relaxed);
                            None
                        }
                    }
                })
                .collect()
        });

        filter_done.store(true, Ordering::Relaxed);
        let _ = heartbeat.join();

        let mut survivors = passes;
        survivors.sort_by(|a, b| {
            b.peak_elev
                .partial_cmp(&a.peak_elev)
                .unwrap_or(std::cmp::Ordering::Equal)
        });

        let mut drops = DropCounts {
            land: drop_land.load(Ordering::Relaxed),
            road: drop_road.load(Ordering::Relaxed),
            hike: drop_hike.load(Ordering::Relaxed),
            slope: drop_slope.load(Ordering::Relaxed),
            jeep: drop_jeep.load(Ordering::Relaxed),
            region: drop_region.load(Ordering::Relaxed),
            ..DropCounts::default()
        };

        let survivor_total = survivors.len();
        info!(survivors = survivor_total, "peaks: survivor dedup start");
        let dedup_started = Instant::now();
        let mut dedup_last_log = dedup_started;
        let mut kept_centroids: Vec<(f64, f64)> = Vec::new();
        let mut deduped: Vec<FilterPass> = Vec::new();
        for (i, pass) in survivors.into_iter().enumerate() {
            let heartbeat = dedup_last_log.elapsed().as_secs() >= FILTER_HEARTBEAT_SECS;
            if i == 0 || i % SURVIVOR_DEDUP_EVERY == 0 || i + 1 == survivor_total || heartbeat {
                info!(
                    processed = i,
                    total = survivor_total,
                    kept = deduped.len(),
                    dropped = drops.dedup,
                    elapsed_secs = dedup_started.elapsed().as_secs_f64(),
                    "peaks: survivor dedup progress"
                );
                dedup_last_log = Instant::now();
            }
            if kept_centroids.iter().any(|(lat, lon)| {
                haversine_m(pass.peak_lat, pass.peak_lon, *lat, *lon)
                    <= filter_params.candidate_dedup_m
            }) {
                drops.dedup += 1;
                continue;
            }
            kept_centroids.push((pass.peak_lat, pass.peak_lon));
            deduped.push(pass);
        }
        info!(
            survivors = survivor_total,
            kept = deduped.len(),
            dropped = drops.dedup,
            elapsed_secs = dedup_started.elapsed().as_secs_f64(),
            "peaks: survivor dedup done"
        );
        (drops, deduped)
    };

    info!(survivors = deduped.len(), "peaks: writing index rows");
    let mut existing_slugs: HashSet<String> = place_slugs(preset_path).unwrap_or_default();
    existing_slugs.extend(index.peaks.keys().cloned());
    let mut kept = 0usize;
    let mut freshened = 0usize;
    let mut skipped_deny = 0usize;
    let write_total = deduped.len();
    let write_started = Instant::now();
    let mut write_last_log = write_started;
    for (write_i, pass) in deduped.into_iter().enumerate() {
        let heartbeat = write_last_log.elapsed().as_secs() >= FILTER_HEARTBEAT_SECS;
        if write_i == 0
            || write_i % PROGRESS_WRITE_EVERY == 0
            || write_i + 1 == write_total
            || heartbeat
        {
            info!(
                processed = write_i,
                total = write_total,
                kept,
                freshened,
                entries = index.peaks.len(),
                elapsed_secs = write_started.elapsed().as_secs_f64(),
                "peaks: index write progress"
            );
            write_last_log = Instant::now();
        }
        if near_denied(
            &overrides.overrides,
            pass.peak_lat,
            pass.peak_lon,
            filter_params.candidate_dedup_m,
        ) {
            skipped_deny += 1;
            continue;
        }
        let elev_m = Some(pass.peak_elev);
        let mut snapped = pass.cand.clone();
        snapped.lat = pass.peak_lat;
        snapped.lon = pass.peak_lon;
        snapped.elev_m = elev_m;
        let kind = if pass.cand.source == "site" {
            PeakKind::Site
        } else {
            PeakKind::Peak
        };
        let (slug, is_freshen) = match near_entry_slug(
            &index,
            pass.peak_lat,
            pass.peak_lon,
            filter_params.candidate_dedup_m,
        ) {
            Some(existing) => (existing, true),
            None => {
                let slug = slug_for_candidate(&snapped, &existing_slugs);
                existing_slugs.insert(slug.clone());
                (slug, false)
            }
        };
        let road_m = Some((pass.road_m * 10.0).round() / 10.0);
        let row = index_row_from_probe(
            pass.cand.name.clone(),
            [pass.peak_lat, pass.peak_lon],
            elev_m,
            pass.cand.source.clone(),
            kind,
            &pass.probe,
            &index.manifest.rules.difficulty,
            &access_meta,
            &peaks_cfg,
            &gtlf_fingerprint,
            &pbf_fingerprint,
            road_m,
        );
        index.peaks.insert(slug, row);
        kept += 1;
        if is_freshen {
            freshened += 1;
        }
        if (write_i + 1) % PROGRESS_WRITE_EVERY == 0 {
            write_peaks_index(project_dir, &prefs_key, &index)
                .context("write peaks index (progress)")?;
        }
    }

    index.manifest.generated_at = chrono_date();
    write_peaks_index(project_dir, &prefs_key, &index).context("write peaks index")?;
    let layout = peaky_preset::project::ProjectLayout::from_config_path(preset_path)?;
    let output = layout.peaks_index_path(&prefs_key);

    let stale = list_stale_cache_dirs(project_dir, &prefs_key, 1)?;
    if !stale.is_empty() {
        info!(
            count = stale.len(),
            "peaks: stale cache dirs remain (run peaky peaks gc)"
        );
    }

    info!(
        kept,
        freshened,
        skipped_deny,
        denied,
        dropped_land = drops.land,
        dropped_road = drops.road,
        dropped_hike = drops.hike,
        dropped_slope = drops.slope,
        dropped_jeep = drops.jeep,
        dropped_dedup = drops.dedup,
        dropped_region = drops.region,
        entries = index.peaks.len(),
        prefs_key = %prefs_key,
        elapsed_secs = t0.elapsed().as_secs_f64(),
        "peaks: filter complete"
    );

    info!(
        path = %output.display(),
        entries = index.peaks.len(),
        prefs_key = %prefs_key,
        "peaks: wrote index.json"
    );

    Ok(PeaksBuildSummary {
        kept,
        denied,
        dropped_land: drops.land,
        dropped_road: drops.road,
        dropped_hike: drops.hike,
        dropped_slope: drops.slope,
        dropped_jeep: drops.jeep,
        dropped_dedup: drops.dedup,
        dropped_region: drops.region,
        gnis: gnis.len(),
        site_seeds: site_seeds.len(),
        dem: dem_peaks.len(),
        max_slope_deg,
        entries: index.peaks.len(),
        output,
    })
}

fn near_entry_slug(index: &PeaksIndex, lat: f64, lon: f64, max_m: f64) -> Option<String> {
    index
        .peaks
        .iter()
        .filter(|(_, e)| e.eligible)
        .find(|(_, e)| haversine_m(lat, lon, e.lat(), e.lon()) <= max_m)
        .map(|(slug, _)| slug.clone())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::hike::{default_max_slope_deg, profile_hike, profile_passes, HikeSampleElev};
    use crate::osm::test_index_from_points;
    const ROAD_SEARCH_M: f64 = 805.0;

    struct FlatElev(f64);

    impl HikeSampleElev for FlatElev {
        fn sample_elev_m(&self, _lat: f64, _lon: f64) -> f64 {
            self.0
        }
    }

    #[test]
    fn peak_beyond_half_mile_road_fails_hike_gate() {
        let roads = test_index_from_points(&[(38.0, -117.0)]);
        let peak_lat = 38.0087;
        let peak_lon = -117.0;
        let road = roads.nearest_within(peak_lat, peak_lon, ROAD_SEARCH_M);
        assert!(road.is_none(), "0.6 mi from road should exceed 805 m cap");
    }

    #[test]
    fn steeper_than_fixed_ceiling_fails_slope_gate() {
        let roads = test_index_from_points(&[(38.0, -117.0)]);
        let peak_lat = 38.003;
        let peak_lon = -117.0;
        let (rlat, rlon, _) = roads
            .nearest_within(peak_lat, peak_lon, ROAD_SEARCH_M)
            .unwrap();
        struct SteepRamp {
            base_lat: f64,
        }
        impl HikeSampleElev for SteepRamp {
            fn sample_elev_m(&self, lat: f64, _lon: f64) -> f64 {
                (lat - self.base_lat) * 45_000.0
            }
        }
        let ramp = SteepRamp { base_lat: 38.0 };
        let profile = profile_hike(&ramp, rlat, rlon, peak_lat, peak_lon, 30.0).unwrap();
        let ceiling = default_max_slope_deg();
        assert!(profile.max_slope_deg > ceiling + 0.5);
        assert!(!profile_passes(&profile, ceiling));
    }

    #[test]
    fn flat_near_road_passes_both_gates() {
        let roads = test_index_from_points(&[(38.0, -117.0)]);
        let peak_lat = 38.0003;
        let peak_lon = -117.0;
        let (rlat, rlon, road_m) = roads
            .nearest_within(peak_lat, peak_lon, ROAD_SEARCH_M)
            .unwrap();
        assert!(road_m < 100.0);
        let flat = FlatElev(2000.0);
        let profile = profile_hike(&flat, rlat, rlon, peak_lat, peak_lon, 30.0).unwrap();
        assert!(profile_passes(&profile, 45.0));
    }
}

fn is_full_aoi_scan(opts: &PeaksBuildOptions) -> bool {
    opts.bbox.is_none()
        && opts.polygon.is_none()
        && opts.corridor.is_none()
        && opts.stop_after.is_none()
}

pub(crate) fn load_dem_candidates(
    session: &Session,
    land: Arc<LandFilterIndex>,
    bbox: LonLatBBox,
    mask_dir: &Path,
    bin_size_m: f64,
    min_prominence_m: f64,
) -> Result<Vec<RawCandidate>> {
    let _ = std::fs::create_dir_all(mask_dir);
    let peaks = session
        .binned_peaks_in_bounds(
            bbox.west,
            bbox.south,
            bbox.east,
            bbox.north,
            land,
            Some(mask_dir),
            bin_size_m,
            min_prominence_m,
            None,
        )
        .context("DEM peak scan")?;
    Ok(peaks
        .into_iter()
        .map(|p| RawCandidate {
            name: None,
            lat: p.lat,
            lon: p.lon,
            elev_m: Some(p.elev_m),
            source: "dem".into(),
            seed_slug: None,
        })
        .collect())
}

pub(crate) fn resolve_scan(
    preset_path: &Path,
    _preset: &Preset,
    opts: &PeaksBuildOptions,
) -> Result<(LonLatBBox, ScanRegion)> {
    if let Some(path) = &opts.polygon {
        let poly = load_polygon_file(path)?;
        let bbox = polygon_bbox(&poly);
        return Ok((bbox, ScanRegion::Clip(poly)));
    }
    if let Some((from_lat, from_lon, to_lat, to_lon)) = opts.corridor {
        let half_width_m = (opts.corridor_width_mi * MI_TO_M / 2.0).max(1.0);
        let poly = corridor_polygon(from_lat, from_lon, to_lat, to_lon, half_width_m);
        let bbox = polygon_bbox(&poly);
        info!(
            from_lat,
            from_lon,
            to_lat,
            to_lon,
            width_mi = opts.corridor_width_mi,
            "peaks: corridor clip"
        );
        return Ok((bbox, ScanRegion::Clip(poly)));
    }
    if let Some(b) = opts.bbox {
        return Ok((LonLatBBox::from_tuple(b), ScanRegion::BboxOnly));
    }
    let aoi = collect_role_geometry(preset_path, LandLayerRole::Aoi, None)
        .context("load AOI geometry")?;
    if let Some(b) = LonLatBBox::from_geometry(&aoi) {
        return Ok((b.padded(0.02), ScanRegion::BboxOnly));
    }
    Ok((
        LonLatBBox::new(-120.0, 35.0, -114.0, 42.0),
        ScanRegion::BboxOnly,
    ))
}

fn polygon_bbox(poly: &geo::Polygon<f64>) -> LonLatBBox {
    LonLatBBox::from_geometry(&Geometry::Polygon(poly.clone()))
        .unwrap_or(LonLatBBox::new(-120.0, 35.0, -114.0, 42.0))
        .padded(0.02)
}

pub fn resolve_corridor_from_opts(
    preset: &Preset,
    corridor: Option<&str>,
    corridor_sites: Option<&str>,
) -> Result<Option<(f64, f64, f64, f64)>> {
    if let Some(raw) = corridor_sites {
        return Ok(Some(resolve_corridor_sites(preset, raw)?));
    }
    if let Some(raw) = corridor {
        return Ok(Some(parse_corridor_coords(raw)?));
    }
    Ok(None)
}

fn chrono_date() -> String {
    use std::time::SystemTime;
    let dur = SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .unwrap_or_default();
    let days = dur.as_secs() / 86_400;
    let (y, m, d) = days_to_ymd(days as i64);
    format!("{y:04}-{m:02}-{d:02}")
}

fn days_to_ymd(days: i64) -> (i64, u32, u32) {
    let z = days + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = mp + if mp < 10 { 3 } else { -9 };
    let y = y + if m <= 2 { 1 } else { 0 };
    (y, m as u32, d as u32)
}
