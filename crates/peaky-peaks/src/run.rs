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
    clean_peaks_catalog, ensure_access_meta, load_peaks_catalog, load_preset, peak_row_compute_key,
    place_slugs, preserve_denied_entries, prune_orphan_access, resolved_dem_fetch_max_workers,
    resolved_skadi_mirror_dir_for_project, upsert_peak_with_access, write_peaks_catalog,
    write_peaks_list_disk_cache, PeakAccessRules, PeakCatalogEntry, PeakHikeProfile,
    PeakJeepProfile, PeaksCatalog, Preset,
};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;

use rayon::prelude::*;
use splatter::peaks::LandFilterIndex;
use splatter::Session;
use tracing::info;

use crate::corridor::{
    corridor_polygon, load_polygon_file, parse_corridor_coords, point_in_region,
    resolve_corridor_sites, ScanRegion, MI_TO_M,
};
use crate::gnis::load_gnis_candidates;
use crate::hike::{
    default_max_slope_deg, format_hike_report, haversine_m, neighbor_prominence_m, profile_hike,
    profile_passes, snap_to_dem_local_max_filtered, stored_peak_hike, HikeProfile,
    HikeProfileDetailed, HikeSampleElev, DEFAULT_MAX_HIKE_M, DEFAULT_MAX_SLOPE_GRADE_PCT,
    DEFAULT_SUMMIT_SNAP_M,
};
use crate::jeep::{profile_along_polyline, route_jeep_detailed, stored_peak_jeep};
use crate::osm::{build_osm_routing, ensure_osm_pbf, OsmRouting};
use crate::park::select_park_and_hike;
use crate::universe::{dedup_nearby, seed_candidates_from_sites, slug_for_candidate, RawCandidate};

const SITE_DEDUP_M: f64 = 300.0;
const CANDIDATE_DEDUP_M: f64 = 300.0;
const DEM_BIN_M: f64 = 1500.0;
/// Upsert each peak+access file every N newly freshened/inserted peaks during a build.
const PROGRESS_WRITE_EVERY: usize = 10;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum FilterDrop {
    SiteDedup,
    Land,
    Region,
    Road,
    Hike,
    Slope,
    Jeep,
}

#[derive(Debug, Clone)]
struct FilterPass {
    cand: RawCandidate,
    peak_lat: f64,
    peak_lon: f64,
    peak_elev: f64,
    road_lat: f64,
    road_lon: f64,
    road_m: f64,
    hike_m_3d: f64,
    max_slope_deg: f64,
    hike: PeakHikeProfile,
    hike_detail: HikeProfileDetailed,
    paved_lat: f64,
    paved_lon: f64,
    jeep_m: f64,
    jeep: PeakJeepProfile,
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

fn filter_candidate(
    cand: &RawCandidate,
    session: &Session,
    land: &LandFilterIndex,
    routing: &OsmRouting,
    scan_region: &ScanRegion,
    site_locs: &[(f64, f64)],
    rules: &PeakAccessRules,
    profile_sample_m: f64,
    hike_path_max_m: f64,
) -> Result<FilterPass, FilterDrop> {
    if is_near_any_site(cand.lat, cand.lon, site_locs, SITE_DEDUP_M) {
        return Err(FilterDrop::SiteDedup);
    }
    let elev = SessionElev(session);
    let Some((peak_lat, peak_lon, peak_elev)) = snap_to_dem_local_max_filtered(
        &elev,
        cand.lat,
        cand.lon,
        DEFAULT_SUMMIT_SNAP_M,
        15.0,
        |lat, lon| land.contains(lon, lat),
    ) else {
        return Err(FilterDrop::Land);
    };
    if !point_in_region(scan_region, peak_lat, peak_lon) {
        return Err(FilterDrop::Region);
    }
    if cand.source == "dem"
        && neighbor_prominence_m(&elev, peak_lat, peak_lon, 30.0)
            < splatter::peaks::MIN_PEAK_PROMINENCE_M
    {
        return Err(FilterDrop::Land);
    }
    let jeep_near = routing
        .jeep_roads
        .nearest_within(peak_lat, peak_lon, rules.max_hike_m);
    let paved_near = routing
        .paved
        .nearest_within(peak_lat, peak_lon, rules.max_hike_m);
    if jeep_near.is_none() && paved_near.is_none() {
        return Err(FilterDrop::Road);
    }
    let Some(picked) = select_park_and_hike(
        &elev,
        &routing.jeep_roads,
        Some(&routing.paved),
        peak_lat,
        peak_lon,
        hike_path_max_m,
        rules.max_slope_deg,
        hike_path_max_m,
        profile_sample_m,
        true,
    ) else {
        return Err(FilterDrop::Slope);
    };
    let (road_lat, road_lon, road_m, detail) =
        (picked.road_lat, picked.road_lon, picked.road_m, picked.hike);
    let profile = HikeProfile {
        hike_m_3d: detail.hike_m_3d,
        max_slope_deg: detail.max_slope_deg,
        n_samples: detail.profile.len(),
    };
    if !profile_passes(&profile, rules.max_slope_deg) {
        return Err(FilterDrop::Slope);
    }
    let hike = stored_peak_hike(&detail);
    let jeep_route = route_jeep_detailed(
        &routing.graph,
        &routing.paved,
        road_lat,
        road_lon,
        rules.max_jeep_m,
    )
    .ok_or(FilterDrop::Jeep)?;
    let jeep_detail = profile_along_polyline(
        &elev,
        &jeep_route.coords,
        profile_sample_m,
        &jeep_route.road_segments,
    )
    .ok_or(FilterDrop::Jeep)?;
    let jeep = stored_peak_jeep(&jeep_detail);
    Ok(FilterPass {
        cand: cand.clone(),
        peak_lat,
        peak_lon,
        peak_elev,
        road_lat,
        road_lon,
        road_m,
        hike_m_3d: profile.hike_m_3d,
        max_slope_deg: profile.max_slope_deg,
        hike,
        hike_detail: detail,
        paved_lat: jeep_route.paved_lat,
        paved_lon: jeep_route.paved_lon,
        jeep_m: jeep_detail.horiz_m,
        jeep,
    })
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

    let pbf = ensure_osm_pbf(project_dir, opts.force_osm)?;
    let access_meta = ensure_access_meta(preset_path).context("access/_meta.yaml")?;
    let osm_opts = crate::osm::OsmRoutingOpts::from(&access_meta);
    let routing = Arc::new(build_osm_routing(
        &pbf, bbox.west, bbox.south, bbox.east, bbox.north, &osm_opts,
    )?);

    let mirror = resolved_skadi_mirror_dir_for_project(project_dir);
    let dem_workers = resolved_dem_fetch_max_workers(&preset);
    let session = Arc::new(Session::new(mirror, opts.verbose, dem_workers));
    session
        .ensure_tiles_for_bounds(bbox.west, bbox.south, bbox.east, bbox.north)
        .context("preload DEM for bbox")?;

    let mut rules = PeakAccessRules::default();
    rules.max_hike_m = DEFAULT_MAX_HIKE_M;
    rules.max_slope_grade_pct = DEFAULT_MAX_SLOPE_GRADE_PCT;
    rules.max_slope_deg = default_max_slope_deg();
    access_meta.apply_to_peak_rules(&mut rules);
    let profile_sample_m = access_meta.profile_sample_m;
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
    let site_seeds = seed_candidates_from_sites(&preset);
    info!(
        count = site_seeds.len(),
        "peaks: site seeds (eip/aw/installed)"
    );
    universe.extend(site_seeds.clone());

    let gnis = load_gnis_candidates(project_dir, bbox.west, bbox.south, bbox.east, bbox.north)?;
    info!(count = gnis.len(), "peaks: GNIS candidates");
    universe.extend(gnis.clone());

    let mask_dir = eligible_land_dem_mask_dir(preset_path, &land_parts.digest);
    let dem_peaks = load_dem_candidates(&session, land_index.clone(), bbox, &mask_dir)?;
    info!(
        count = dem_peaks.len(),
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
    universe = dedup_nearby(universe, CANDIDATE_DEDUP_M);
    universe.sort_by(|a, b| {
        b.elev_m
            .unwrap_or(0.0)
            .partial_cmp(&a.elev_m.unwrap_or(0.0))
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    info!(count = universe.len(), "peaks: universe after dedup");

    let site_locs: Vec<(f64, f64)> = preset.sites.values().map(|s| (s.lat(), s.lon())).collect();

    let previous = load_peaks_catalog(preset_path)?;
    let full_aoi = is_full_aoi_scan(&opts);
    let replace_catalog = opts.clean || full_aoi;
    let mut catalog = PeaksCatalog {
        generated_at: chrono_date(),
        rules: rules.clone(),
        entries: if replace_catalog {
            HashMap::new()
        } else {
            previous.entries.clone()
        },
    };
    let denied = if replace_catalog {
        preserve_denied_entries(&mut catalog, &previous)
    } else {
        catalog
            .entries
            .values()
            .filter(|e| e.deny.unwrap_or(false))
            .count()
    };
    if opts.clean {
        clean_peaks_catalog(preset_path, &catalog).context("clean peaks/access dirs")?;
    }
    info!(
        clean = opts.clean,
        full_aoi,
        replace_catalog,
        prior_entries = previous.entries.len(),
        starting_entries = catalog.entries.len(),
        denied,
        "peaks: catalog merge mode"
    );
    if !full_aoi || opts.clean {
        write_peaks_catalog(preset_path, &catalog).context("write peaks catalog (start)")?;
    }

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
            if (i + 1) % 500 == 0 {
                info!(
                    "[{}/{}] scanning… kept={} land={} road={} hike={} slope={} jeep={}",
                    i + 1,
                    universe.len(),
                    deduped.len(),
                    drops.land,
                    drops.road,
                    drops.hike,
                    drops.slope,
                    drops.jeep
                );
            }
            match filter_candidate(
                cand,
                session.as_ref(),
                land_index.as_ref(),
                routing.as_ref(),
                &scan_region,
                &site_locs,
                &rules,
                profile_sample_m,
                hike_path_max_m,
            ) {
                Ok(pass) => {
                    if kept_centroids.iter().any(|(lat, lon)| {
                        haversine_m(pass.peak_lat, pass.peak_lon, *lat, *lon) <= CANDIDATE_DEDUP_M
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
                Err(FilterDrop::SiteDedup) => drops.dedup += 1,
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
        let site_locs = Arc::new(site_locs);
        let processed = AtomicUsize::new(0);
        let drop_site = AtomicUsize::new(0);
        let drop_land = AtomicUsize::new(0);
        let drop_region = AtomicUsize::new(0);
        let drop_road = AtomicUsize::new(0);
        let drop_hike = AtomicUsize::new(0);
        let drop_slope = AtomicUsize::new(0);
        let drop_jeep = AtomicUsize::new(0);
        let universe_len = universe.len();

        let passes: Vec<FilterPass> = pool.install(|| {
            universe
                .par_iter()
                .filter_map(|cand| {
                    let n = processed.fetch_add(1, Ordering::Relaxed) + 1;
                    if n % 500 == 0 || n == universe_len {
                        info!(
                            "[{}/{}] filtering… land={} road={} hike={} slope={} jeep={}",
                            n,
                            universe_len,
                            drop_land.load(Ordering::Relaxed),
                            drop_road.load(Ordering::Relaxed),
                            drop_hike.load(Ordering::Relaxed),
                            drop_slope.load(Ordering::Relaxed),
                            drop_jeep.load(Ordering::Relaxed),
                        );
                    }

                    match filter_candidate(
                        cand,
                        session.as_ref(),
                        land_index.as_ref(),
                        routing.as_ref(),
                        scan_region.as_ref(),
                        site_locs.as_ref(),
                        rules.as_ref(),
                        profile_sample_m,
                        hike_path_max_m,
                    ) {
                        Ok(pass) => Some(pass),
                        Err(FilterDrop::SiteDedup) => {
                            drop_site.fetch_add(1, Ordering::Relaxed);
                            None
                        }
                        Err(FilterDrop::Land) => {
                            drop_land.fetch_add(1, Ordering::Relaxed);
                            None
                        }
                        Err(FilterDrop::Region) => {
                            drop_region.fetch_add(1, Ordering::Relaxed);
                            None
                        }
                        Err(FilterDrop::Road) => {
                            drop_road.fetch_add(1, Ordering::Relaxed);
                            None
                        }
                        Err(FilterDrop::Hike) => {
                            drop_hike.fetch_add(1, Ordering::Relaxed);
                            None
                        }
                        Err(FilterDrop::Slope) => {
                            drop_slope.fetch_add(1, Ordering::Relaxed);
                            None
                        }
                        Err(FilterDrop::Jeep) => {
                            drop_jeep.fetch_add(1, Ordering::Relaxed);
                            None
                        }
                    }
                })
                .collect()
        });

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
            dedup: drop_site.load(Ordering::Relaxed),
            ..DropCounts::default()
        };

        let mut kept_centroids: Vec<(f64, f64)> = Vec::new();
        let mut deduped: Vec<FilterPass> = Vec::new();
        for pass in survivors {
            if kept_centroids.iter().any(|(lat, lon)| {
                haversine_m(pass.peak_lat, pass.peak_lon, *lat, *lon) <= CANDIDATE_DEDUP_M
            }) {
                drops.dedup += 1;
                continue;
            }
            kept_centroids.push((pass.peak_lat, pass.peak_lon));
            deduped.push(pass);
        }
        (drops, deduped)
    };

    let mut existing_slugs: HashSet<String> = place_slugs(preset_path).unwrap_or_default();
    existing_slugs.extend(catalog.entries.keys().cloned());
    let mut kept = 0usize;
    let mut freshened = 0usize;
    let mut skipped_deny = 0usize;
    let mut pending_write: Vec<(String, PeakCatalogEntry)> = Vec::new();
    for pass in deduped {
        if near_denied(&catalog, pass.peak_lat, pass.peak_lon, CANDIDATE_DEDUP_M) {
            skipped_deny += 1;
            continue;
        }
        let elev_m = Some(pass.peak_elev);
        let mut snapped = pass.cand.clone();
        snapped.lat = pass.peak_lat;
        snapped.lon = pass.peak_lon;
        snapped.elev_m = elev_m;
        let (slug, is_freshen) =
            match near_entry_slug(&catalog, pass.peak_lat, pass.peak_lon, CANDIDATE_DEDUP_M) {
                Some(existing) => (existing, true),
                None => {
                    let slug = slug_for_candidate(&snapped, &existing_slugs);
                    existing_slugs.insert(slug.clone());
                    (slug, false)
                }
            };
        let entry = PeakCatalogEntry {
            name: pass.cand.name.clone(),
            loc: [pass.peak_lat, pass.peak_lon],
            elev_m,
            source: pass.cand.source.clone(),
            compute_key: Some(peak_row_compute_key(
                &catalog.rules,
                &access_meta,
                &land_parts.digest,
                DEFAULT_SUMMIT_SNAP_M,
            )),
            road_m: Some((pass.road_m * 10.0).round() / 10.0),
            road_loc: Some([pass.road_lat, pass.road_lon]),
            hike_m: Some((pass.hike_m_3d * 10.0).round() / 10.0),
            max_slope_deg: Some((pass.max_slope_deg * 10.0).round() / 10.0),
            hike: Some(pass.hike),
            paved_loc: Some([pass.paved_lat, pass.paved_lon]),
            jeep_m: Some((pass.jeep_m * 10.0).round() / 10.0),
            jeep: Some(pass.jeep),
            hike_difficulty: None,
            jeep_difficulty: None,
            deny: None,
        };
        catalog.entries.insert(slug.clone(), entry.clone());
        pending_write.push((slug, entry));
        kept += 1;
        if is_freshen {
            freshened += 1;
        }
        if pending_write.len() >= PROGRESS_WRITE_EVERY {
            for (slug, entry) in pending_write.drain(..) {
                upsert_peak_with_access(preset_path, &slug, &entry)
                    .context("upsert peak+access (progress)")?;
            }
            info!(
                kept,
                freshened,
                entries = catalog.entries.len(),
                "peaks: progress write"
            );
        }
    }
    for (slug, entry) in pending_write.drain(..) {
        upsert_peak_with_access(preset_path, &slug, &entry).context("upsert peak+access")?;
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
        entries = catalog.entries.len(),
        elapsed_secs = t0.elapsed().as_secs_f64(),
        "peaks: filter complete"
    );

    write_peaks_catalog(preset_path, &catalog).context("write peaks catalog")?;
    let pruned_access = prune_orphan_access(preset_path).context("prune orphan access")?;
    if let Err(err) = write_peaks_list_disk_cache(preset_path, &catalog) {
        info!(error = %err, "peaks: list cache prewarm failed");
    }
    let output = peaky_preset::peaks_catalog_path(preset_path)?;

    info!(
        path = %output.display(),
        entries = catalog.entries.len(),
        pruned_access,
        "peaks: wrote peaks/"
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
        entries: catalog.entries.len(),
        output,
    })
}

fn near_entry_slug(catalog: &PeaksCatalog, lat: f64, lon: f64, max_m: f64) -> Option<String> {
    catalog
        .entries
        .iter()
        .filter(|(_, e)| !e.deny.unwrap_or(false))
        .find(|(_, e)| haversine_m(lat, lon, e.lat(), e.lon()) <= max_m)
        .map(|(slug, _)| slug.clone())
}

fn near_denied(catalog: &PeaksCatalog, lat: f64, lon: f64, max_m: f64) -> bool {
    catalog
        .entries
        .values()
        .any(|e| e.deny.unwrap_or(false) && haversine_m(lat, lon, e.lat(), e.lon()) <= max_m)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::hike::HikeSampleElev;
    use crate::osm::test_index_from_points;

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
        let road = roads.nearest_within(peak_lat, peak_lon, DEFAULT_MAX_HIKE_M);
        assert!(road.is_none(), "0.6 mi from road should exceed 805 m cap");
    }

    #[test]
    fn steeper_than_fixed_ceiling_fails_slope_gate() {
        let roads = test_index_from_points(&[(38.0, -117.0)]);
        let peak_lat = 38.003;
        let peak_lon = -117.0;
        let (rlat, rlon, _) = roads
            .nearest_within(peak_lat, peak_lon, DEFAULT_MAX_HIKE_M)
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
            .nearest_within(peak_lat, peak_lon, DEFAULT_MAX_HIKE_M)
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

fn load_dem_candidates(
    session: &Session,
    land: Arc<LandFilterIndex>,
    bbox: LonLatBBox,
    mask_dir: &Path,
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
            DEM_BIN_M,
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

fn resolve_scan(
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

fn is_near_any_site(lat: f64, lon: f64, sites: &[(f64, f64)], min_m: f64) -> bool {
    sites
        .iter()
        .any(|(slat, slon)| crate::hike::haversine_m(lat, lon, *slat, *slon) <= min_m)
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
