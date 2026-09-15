//! On-demand coverage-guided wedge search (gap overview → hard spans → DFS/backtrack).

use std::collections::{HashMap, HashSet};
use std::path::Path;
use std::sync::{Arc, Mutex};
use std::time::Instant;

use anyhow::{bail, Result};
use geo::{Geometry, MultiPolygon};
use peaky_geo::{eligible_land_dem_mask_dir, load_or_build_eligible_land_filter, LonLatBBox};
use peaky_preset::{load_preset, Preset, SiteEntry};
use peaky_serve::rf::{max_hop_range_m, rf_json_for_preset};
use peaky_serve::viewshed::{
    ensure_viewshed_progressive_for_coords_blocking, ensure_viewshed_progressive_for_site_blocking,
};
use peaky_serve::ViewshedSimOverrides;
use serde::{Deserialize, Serialize};
use serde_json::json;
use splatter::eligible_mask::MASK_ROW_SAMPLES;
use splatter::engine::{dem_tile_bounds, required_tile_names_for_bounds};
use splatter::peaks::{LandFilterIndex, Peak, RingSectorFilter, ring_sector_scan_bbox};
use splatter::propagate::haversine_m;
use splatter::session::Session;

use crate::cache::{digest_hex, digest_hex_stable, short_digest, FinderCache};
use crate::candidates::{corridor_bbox, Candidate, CandidateRegistry};
use crate::coverage::{
    covers_candidate_one_way, covers_waypoint, covering_indices, rf_digest, site_tx_height,
};
use crate::route::Waypoint;
use crate::telemetry::CacheLedger;

/// Ring shrink steps as fractions of max hop (outer → inner).
/// Long hops first; a hop past the goal waypoint is only allowed if it covers that waypoint.
pub const HOP_RING_FRACS: &[f64] = &[0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1, 0.0];

/// Along-track margin (m): peak is "past" the goal if projection exceeds goal range by this.
const PAST_GOAL_MARGIN_M: f64 = 250.0;

/// Initial wedge half-angle (5° total); widens in 2.5° steps to full circle.
pub const WEDGE_HALF_START_DEG: f64 = 2.5;
pub const WEDGE_HALF_STEP_DEG: f64 = 2.5;

const GAP_SAMPLE_M: f64 = 250.0;
const HARD_GAP_FRAC: f64 = 0.35;

pub fn bearing_deg(lat1: f64, lon1: f64, lat2: f64, lon2: f64) -> f64 {
    let phi1 = lat1.to_radians();
    let phi2 = lat2.to_radians();
    let dlambda = (lon2 - lon1).to_radians();
    let y = dlambda.sin() * phi2.cos();
    let x = phi1.cos() * phi2.sin() - phi1.sin() * phi2.cos() * dlambda.cos();
    (y.atan2(x).to_degrees() + 360.0) % 360.0
}

pub fn angle_diff_deg(a: f64, b: f64) -> f64 {
    let mut d = (a - b).abs() % 360.0;
    if d > 180.0 {
        d = 360.0 - d;
    }
    d
}

/// True when `peak` lies past the goal along the Pa→goal axis (beyond the waypoint).
/// Relays past the goal are forbidden unless they cover the goal waypoint.
pub fn peak_is_past_goal(
    pa_lat: f64,
    pa_lon: f64,
    goal_lat: f64,
    goal_lon: f64,
    peak_lat: f64,
    peak_lon: f64,
) -> bool {
    let d_goal = haversine_m(pa_lat, pa_lon, goal_lat, goal_lon);
    let d_peak = haversine_m(pa_lat, pa_lon, peak_lat, peak_lon);
    if d_goal < 1.0 {
        return d_peak > PAST_GOAL_MARGIN_M;
    }
    let goal_bearing = bearing_deg(pa_lat, pa_lon, goal_lat, goal_lon);
    let peak_bearing = bearing_deg(pa_lat, pa_lon, peak_lat, peak_lon);
    let delta = angle_diff_deg(peak_bearing, goal_bearing);
    if delta >= 90.0 {
        return false;
    }
    let along_m = d_peak * delta.to_radians().cos();
    along_m > d_goal + PAST_GOAL_MARGIN_M
}

/// Peak lies in annulus `(min_m, max_m]` and within `half_angle_deg` of goal bearing from `from`.
pub fn peak_in_ring_sector(
    from_lat: f64,
    from_lon: f64,
    goal_lat: f64,
    goal_lon: f64,
    peak_lat: f64,
    peak_lon: f64,
    min_m: f64,
    max_m: f64,
    half_angle_deg: f64,
) -> bool {
    let dist = haversine_m(from_lat, from_lon, peak_lat, peak_lon);
    if dist <= min_m || dist > max_m {
        return false;
    }
    let goal_bearing = bearing_deg(from_lat, from_lon, goal_lat, goal_lon);
    let peak_bearing = bearing_deg(from_lat, from_lon, peak_lat, peak_lon);
    angle_diff_deg(peak_bearing, goal_bearing) <= half_angle_deg
}

pub fn wedge_half_angles() -> Vec<f64> {
    let mut out = Vec::new();
    let mut half = WEDGE_HALF_START_DEG;
    while half <= 180.0 + 1e-6 {
        out.push(half);
        half += WEDGE_HALF_STEP_DEG;
    }
    out
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RouteGap {
    pub start_lat: f64,
    pub start_lon: f64,
    pub end_lat: f64,
    pub end_lon: f64,
    pub length_m: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct GapCacheIdentity {
    pub waypoints_digest: String,
    pub eligible_digest: String,
    pub hop_m: f64,
    pub sample_m: f64,
    pub hard_frac: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GapOverview {
    pub gaps: Vec<RouteGap>,
    /// Segment indices (0 = wp0→wp1) ordered hard-first then fill.
    pub segment_order: Vec<usize>,
    /// Per-segment hard flag (same length as route segments).
    pub hard_segments: Vec<bool>,
    /// Present on newly written caches; used to reclaim files after key-format churn.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub identity: Option<GapCacheIdentity>,
}

fn waypoints_digest(waypoints: &[Waypoint]) -> String {
    // Stable: gap overview must survive solver schema bumps.
    digest_hex_stable(&[
        "waypoints",
        &waypoints
            .iter()
            .map(|wp| format!("{:.6},{:.6}", wp.lat, wp.lon))
            .collect::<Vec<_>>()
            .join(";"),
    ])
}

fn gap_overview_cache_key(waypoints_digest: &str, eligible_digest: &str, hop_m: f64) -> String {
    // Stable digest: land gaps depend only on route + eligible land + sample knobs.
    digest_hex_stable(&[
        "gap_overview",
        "v1",
        waypoints_digest,
        eligible_digest,
        &format!("hop={hop_m:.3}"),
        &format!("sample={GAP_SAMPLE_M}"),
        &format!("hard_frac={HARD_GAP_FRAC}"),
    ])
}

fn publish_gap_overview_watch(ledger: &CacheLedger, overview: &GapOverview) {
    if let Some(hub) = ledger.watch() {
        hub.publish(
            "gaps",
            json!({
                "gaps": overview.gaps.iter().map(|g| json!({
                    "coordinates": [[g.start_lon, g.start_lat], [g.end_lon, g.end_lat]],
                    "length_m": g.length_m,
                })).collect::<Vec<_>>(),
                "hard_segments": overview.hard_segments,
            }),
        );
    }
}

fn interpolate_waypoints(a: &Waypoint, b: &Waypoint, step_m: f64) -> Vec<Waypoint> {
    let dist = haversine_m(a.lat, a.lon, b.lat, b.lon);
    if dist <= step_m {
        return vec![a.clone(), b.clone()];
    }
    let n = (dist / step_m).ceil() as usize;
    let mut pts = Vec::with_capacity(n + 1);
    for i in 0..=n {
        let t = i as f64 / n as f64;
        pts.push(Waypoint {
            lat: a.lat + t * (b.lat - a.lat),
            lon: a.lon + t * (b.lon - a.lon),
        });
    }
    pts
}

/// Walk route samples against eligible land; mark hard spans for solve order.
pub fn compute_gap_overview(
    waypoints: &[Waypoint],
    land: &LandFilterIndex,
    hop_m: f64,
    ledger: &CacheLedger,
) -> GapOverview {
    let hard_gap_m = (hop_m * HARD_GAP_FRAC).max(GAP_SAMPLE_M);
    let mut gaps = Vec::new();
    let segment_hard_len = waypoints.len().saturating_sub(1);
    let mut segment_hard = vec![false; segment_hard_len];

    ledger.search_step(&format!(
        "gap overview: sampling {segment_hard_len} route segments at {:.0}m…",
        GAP_SAMPLE_M
    ));

    for seg in 0..segment_hard_len {
        let wp_a = &waypoints[seg];
        let wp_b = &waypoints[seg + 1];
        ledger.watch_gap_overview(
            seg + 1,
            segment_hard_len,
            gaps.len(),
            wp_a.lon,
            wp_a.lat,
            wp_b.lon,
            wp_b.lat,
        );
        let samples = interpolate_waypoints(wp_a, wp_b, GAP_SAMPLE_M);
        let mut gap_start: Option<Waypoint> = None;
        let mut gap_len = 0.0_f64;
        let mut prev = samples[0].clone();

        for wp in samples.iter().skip(1) {
            let step = haversine_m(prev.lat, prev.lon, wp.lat, wp.lon);
            let eligible = land.contains(wp.lon, wp.lat);
            if !eligible {
                if gap_start.is_none() {
                    gap_start = Some(prev.clone());
                }
                gap_len += step;
            } else if let Some(start) = gap_start.take() {
                let g = RouteGap {
                    start_lat: start.lat,
                    start_lon: start.lon,
                    end_lat: prev.lat,
                    end_lon: prev.lon,
                    length_m: gap_len,
                };
                ledger.watch_gap_found(
                    g.start_lon,
                    g.start_lat,
                    g.end_lon,
                    g.end_lat,
                    g.length_m,
                );
                gaps.push(g);
                if gap_len >= hard_gap_m {
                    segment_hard[seg] = true;
                }
                gap_len = 0.0;
            }
            prev = wp.clone();
        }
        if let Some(start) = gap_start {
            let g = RouteGap {
                start_lat: start.lat,
                start_lon: start.lon,
                end_lat: prev.lat,
                end_lon: prev.lon,
                length_m: gap_len,
            };
            ledger.watch_gap_found(
                g.start_lon,
                g.start_lat,
                g.end_lon,
                g.end_lat,
                g.length_m,
            );
            gaps.push(g);
            if gap_len >= hard_gap_m {
                segment_hard[seg] = true;
            }
        }
        let seg_dist = haversine_m(
            waypoints[seg].lat,
            waypoints[seg].lon,
            waypoints[seg + 1].lat,
            waypoints[seg + 1].lon,
        );
        if seg_dist > hop_m * 0.95 {
            segment_hard[seg] = true;
        }
    }

    let mut hard: Vec<usize> = segment_hard
        .iter()
        .enumerate()
        .filter(|(_, h)| **h)
        .map(|(i, _)| i)
        .collect();
    let mut easy: Vec<usize> = segment_hard
        .iter()
        .enumerate()
        .filter(|(_, h)| !**h)
        .map(|(i, _)| i)
        .collect();
    hard.sort_unstable();
    easy.sort_unstable();
    let hard_count = hard.len();
    let mut segment_order = hard;
    segment_order.extend(easy);

    ledger.progress(&format!(
        "gap overview gaps={} hard_segments={}/{}",
        gaps.len(),
        hard_count,
        segment_hard.len()
    ));
    ledger.search_step(&format!(
        "gap overview done: {} gaps, {} hard segments",
        gaps.len(),
        hard_count
    ));

    GapOverview {
        gaps,
        segment_order,
        hard_segments: segment_hard,
        identity: None,
    }
}

fn gap_cache_identity(wp_digest: &str, eligible_digest: &str, hop_m: f64) -> GapCacheIdentity {
    GapCacheIdentity {
        waypoints_digest: wp_digest.to_string(),
        eligible_digest: eligible_digest.to_string(),
        hop_m,
        sample_m: GAP_SAMPLE_M,
        hard_frac: HARD_GAP_FRAC,
    }
}

/// Load gap overview from the primary key, or reclaim a prior file with the same identity
/// (or a legacy file with the same segment count — one-time migration after key-format churn).
fn load_gap_overview_from_disk(
    cache: &FinderCache,
    key: &str,
    identity: &GapCacheIdentity,
    segment_count: usize,
) -> Option<GapOverview> {
    let primary = cache.path_for("gaps", key, "json");
    if let Ok(text) = std::fs::read_to_string(&primary) {
        if let Ok(mut overview) = serde_json::from_str::<GapOverview>(&text) {
            if overview.identity.is_none() {
                overview.identity = Some(identity.clone());
            }
            return Some(overview);
        }
    }
    let dir = cache.root().join("gaps");
    let entries = std::fs::read_dir(&dir).ok()?;
    let mut legacy: Option<(std::path::PathBuf, GapOverview)> = None;
    for entry in entries.flatten() {
        let path = entry.path();
        if path.extension().and_then(|e| e.to_str()) != Some("json") {
            continue;
        }
        if path == primary {
            continue;
        }
        let Ok(text) = std::fs::read_to_string(&path) else {
            continue;
        };
        let Ok(overview) = serde_json::from_str::<GapOverview>(&text) else {
            continue;
        };
        if overview.identity.as_ref() == Some(identity) {
            let _ = std::fs::copy(&path, &primary);
            return Some(overview);
        }
        if overview.identity.is_none() && overview.hard_segments.len() == segment_count {
            legacy = Some((path, overview));
        }
    }
    if let Some((path, mut overview)) = legacy {
        overview.identity = Some(identity.clone());
        if let Ok(text) = serde_json::to_string_pretty(&overview) {
            let _ = std::fs::write(&primary, text);
        } else {
            let _ = std::fs::copy(&path, &primary);
        }
        return Some(overview);
    }
    None
}

pub fn gap_overview_cached(
    cache: &FinderCache,
    waypoints: &[Waypoint],
    land: &LandFilterIndex,
    eligible_digest: &str,
    hop_m: f64,
    ledger: &CacheLedger,
) -> Result<GapOverview> {
    let wp_digest = waypoints_digest(waypoints);
    let key = gap_overview_cache_key(&wp_digest, eligible_digest, hop_m);
    let identity = gap_cache_identity(&wp_digest, eligible_digest, hop_m);
    let segment_count = waypoints.len().saturating_sub(1);
    let detail = format!(
        "waypoints={} land={} hop={hop_m:.0} key={}",
        short_digest(&wp_digest),
        short_digest(eligible_digest),
        short_digest(&key),
    );

    if let Some(overview) =
        load_gap_overview_from_disk(cache, &key, &identity, segment_count)
    {
        ledger.log_hit("gap_overview", &detail);
        ledger.search_step(&format!(
            "gap overview cache hit (no land walk): {} gaps, {} hard segments",
            overview.gaps.len(),
            overview.hard_segments.iter().filter(|h| **h).count(),
        ));
        publish_gap_overview_watch(ledger, &overview);
        return Ok(overview);
    }

    let t0 = Instant::now();
    let mut overview = compute_gap_overview(waypoints, land, hop_m, ledger);
    overview.identity = Some(identity);
    ledger.log_miss("gap_overview", &detail, t0.elapsed());
    let path = cache.path_for("gaps", &key, "json");
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let text = serde_json::to_string_pretty(&overview)?;
    let tmp = path.with_extension("json.tmp");
    std::fs::write(&tmp, &text)?;
    std::fs::rename(&tmp, &path)?;
    ledger.search_step(&format!(
        "gap overview cached for next run: {} gaps (solver schema bumps do not invalidate this)",
        overview.gaps.len()
    ));
    publish_gap_overview_watch(ledger, &overview);
    Ok(overview)
}

/// Uncached gap overview (tests and direct callers).
pub fn gap_overview(
    waypoints: &[Waypoint],
    land: &LandFilterIndex,
    hop_m: f64,
    ledger: &CacheLedger,
) -> GapOverview {
    let overview = compute_gap_overview(waypoints, land, hop_m, ledger);
    publish_gap_overview_watch(ledger, &overview);
    overview
}

struct SearchCtx<'a> {
    preset_path: &'a Path,
    session: Arc<Session>,
    registry: &'a mut CandidateRegistry,
    cache: &'a FinderCache,
    ledger: &'a CacheLedger,
    preset: Preset,
    rf_json: String,
    rf_key: String,
    hop_m: f64,
    bin_m: f64,
    land_index: LandFilterIndex,
    eligible_digest: String,
    /// Sites confirmed in prior route segments (for cumulative watch chain).
    chain_prefix: Vec<usize>,
    viewshed_warmed: Mutex<HashSet<usize>>,
}

impl<'a> SearchCtx<'a> {
    fn new(
        preset_path: &'a Path,
        session: Arc<Session>,
        registry: &'a mut CandidateRegistry,
        cache: &'a FinderCache,
        ledger: &'a CacheLedger,
        waypoints: &[Waypoint],
    ) -> Result<Self> {
        let preset = load_preset(preset_path)?;
        let rf_json = rf_json_for_preset(&preset)?;
        let rf_key = rf_digest(&preset)?;
        let hop_m = max_hop_range_m(&preset);
        let bin_m = preset.scan.peak_bin_size_m;
        let clip = LonLatBBox::from_tuple(corridor_bbox(waypoints, hop_m)).padded(0.05);
        ledger.search_step("loading eligible land polygon + spatial index…");
        let t0 = Instant::now();
        let (eligible_digest, land_index, _eligible_mp) =
            load_or_build_eligible_land_filter(preset_path, Some(clip))?;
        ledger.search_step(&format!(
            "eligible land ready: {} polygons in {:.1}s",
            land_index.polygon_count(),
            t0.elapsed().as_secs_f64()
        ));
        ledger.log_hit(
            "eligible_land",
            &format!("digest={}", short_digest(&eligible_digest)),
        );
        Ok(Self {
            preset_path,
            session,
            registry,
            cache,
            ledger,
            preset,
            rf_json,
            rf_key,
            hop_m,
            bin_m,
            land_index,
            eligible_digest,
            chain_prefix: Vec::new(),
            viewshed_warmed: Mutex::new(HashSet::new()),
        })
    }

    fn candidate_viewshed_slug(idx: usize) -> String {
        format!("site-{idx}")
    }

    fn preset_site_entry(&self, cand: &Candidate) -> Option<SiteEntry> {
        cand.slug
            .as_ref()
            .and_then(|slug| self.preset.sites.get(slug).cloned())
    }

    fn schedule_viewshed_warm(&self, idx: usize) {
        let Some(hub) = self.ledger.watch() else {
            return;
        };
        let Some(cand) = self.registry.get(idx) else {
            return;
        };
        let slug = Self::candidate_viewshed_slug(idx);
        {
            let mut warmed = self.viewshed_warmed.lock().expect("viewshed_warmed");
            if !warmed.insert(idx) {
                return;
            }
        }
        let session = self.session.clone();
        let preset_path = self.preset_path.to_path_buf();
        let preset = self.preset.clone();
        let hub = hub.clone();
        let lat = cand.lat;
        let lon = cand.lon;
        let site_entry = self.preset_site_entry(cand);
        let name = cand.slug.as_deref().unwrap_or(&cand.id).to_string();
        std::thread::spawn(move || {
            let rewrite = |mut overlay: serde_json::Value| {
                if let Some(obj) = overlay.as_object_mut() {
                    obj.insert("slug".into(), json!(slug));
                    obj.insert("name".into(), json!(name));
                    if let Some(digest) = obj.get("digest").and_then(|d| d.as_str()) {
                        obj.insert(
                            "url".into(),
                            json!(format!("/viewshed/{digest}/splat.png")),
                        );
                    }
                }
                hub.publish("viewshed", overlay);
            };
            let sim = ViewshedSimOverrides::default();
            let project_dir = preset_path.parent().unwrap_or(&preset_path);
            let board_viewshed = peaky_preset::BoardViewshedResolver::load(project_dir)
                .map_err(|e| format!("boards.yaml: {e}"))?;
            let result = if let Some(ref site) = site_entry {
                ensure_viewshed_progressive_for_site_blocking(
                    session,
                    "finder",
                    &preset_path,
                    &preset,
                    &slug,
                    site,
                    Some(&board_viewshed),
                    false,
                    rewrite,
                )
            } else {
                ensure_viewshed_progressive_for_coords_blocking(
                    session,
                    "finder",
                    &preset_path,
                    &preset,
                    lat,
                    lon,
                    &sim,
                    false,
                    rewrite,
                )
            };
            if let Err(e) = result {
                tracing::warn!("chain viewshed warm failed for {slug}: {e}");
            }
        });
    }

    fn set_chain_prefix(&mut self, prefix: &[usize]) {
        self.chain_prefix = prefix.to_vec();
    }

    fn ensure_dem_bbox(&self, west: f64, south: f64, east: f64, north: f64) -> Result<()> {
        let before = self.session.loaded_tile_count();
        self.session
            .ensure_tiles_for_bounds(west, south, east, north)?;
        let loaded = self.session.loaded_tile_count().saturating_sub(before);
        self.ledger.record_dem_tiles(0, loaded as u64);
        self.publish_eligible_masks_for_bbox(west, south, east, north)?;
        Ok(())
    }

    /// Load/build `.elmk` for DEM tiles in bbox and publish watch overlay (green/gray).
    fn publish_eligible_masks_for_bbox(
        &self,
        west: f64,
        south: f64,
        east: f64,
        north: f64,
    ) -> Result<()> {
        let Some(hub) = self.ledger.watch() else {
            return Ok(());
        };
        let tiles = required_tile_names_for_bounds(west, south, east, north);
        if tiles.is_empty() {
            return Ok(());
        }
        let mut tile_meta = Vec::with_capacity(tiles.len());
        for name in &tiles {
            let Ok((w, s, e, n)) = dem_tile_bounds(name) else {
                continue;
            };
            tile_meta.push(json!({
                "name": name,
                "west": w,
                "south": s,
                "east": e,
                "north": n,
            }));
        }
        if !tile_meta.is_empty() {
            hub.publish("dem_tiles", json!({ "tiles": tile_meta, "merge": true }));
        }
        let mask_dir = eligible_land_dem_mask_dir(self.preset_path, &self.eligible_digest);
        let mut published = 0usize;
        for name in &tiles {
            if hub.has_mask_tile(name) {
                continue;
            }
            let Ok((w, s, e, n)) = dem_tile_bounds(name) else {
                continue;
            };
            let Some(samples) =
                self.session
                    .ensure_tile_mask_viz(&mask_dir, name, &self.land_index)?
            else {
                continue;
            };
            hub.publish(
                "mask_tile",
                json!({
                    "tile": name,
                    "w": MASK_ROW_SAMPLES,
                    "h": MASK_ROW_SAMPLES,
                    "west": w,
                    "south": s,
                    "east": e,
                    "north": n,
                    "samples": samples,
                }),
            );
            published += 1;
            self.ledger.watch_dem_touch(name);
        }
        if published > 0 {
            self.ledger.search_step(&format!(
                "eligible land mask: published {published} tile(s) for watch"
            ));
        }
        Ok(())
    }

    /// Scan binned peaks inside one ring-sector strip (never the full hop disc).
    fn scan_ring_sector_peaks(
        &self,
        pa_lat: f64,
        pa_lon: f64,
        goal: &Waypoint,
        min_m: f64,
        max_m: f64,
        half_exclude_deg: f64,
        half_include_deg: f64,
    ) -> Result<Vec<Peak>> {
        let scan_bbox = ring_sector_scan_bbox(
            pa_lat,
            pa_lon,
            goal.lat,
            goal.lon,
            min_m,
            max_m,
            half_include_deg,
        );
        self.ledger.search_step(&format!(
            "scan wedge ring {:.0}-{:.0}m ±{half_include_deg:.1}°{}",
            min_m,
            max_m,
            if half_exclude_deg > 0.0 {
                format!(" (flanks +{half_exclude_deg:.1}°)")
            } else {
                String::new()
            }
        ));
        self.ledger.watch_search_disc(pa_lon, pa_lat, max_m, 0);
        self.ensure_dem_bbox(scan_bbox.0, scan_bbox.1, scan_bbox.2, scan_bbox.3)?;
        let ring = RingSectorFilter {
            goal_lat: goal.lat,
            goal_lon: goal.lon,
            min_m,
            max_m,
            half_angle_exclude_deg: half_exclude_deg,
            half_angle_include_deg: half_include_deg,
        };
        let ledger = self.ledger;
        let on_progress = |p: splatter::peaks::HopDiscScanProgress| {
            ledger.watch_hop_disc_scan(
                p.rows_done,
                p.rows_total,
                p.tiles,
                pa_lon,
                pa_lat,
                max_m,
            );
        };
        let t0 = Instant::now();
        let mask_dir = eligible_land_dem_mask_dir(self.preset_path, &self.eligible_digest);
        let peaks = self.session.disc_binned_peaks(
            pa_lat,
            pa_lon,
            max_m,
            None,
            Some(scan_bbox),
            None,
            Some(ring),
            self.bin_m,
            Some(&self.land_index),
            Some(&mask_dir),
            Some(&on_progress),
        )?;
        self.ledger.search_step(&format!(
            "wedge scan peaks={} in {:.1}s",
            peaks.len(),
            t0.elapsed().as_secs_f64()
        ));
        Ok(peaks)
    }

    fn publish_search_focus(&self, pa: &Candidate, goal: &Waypoint) {
        if let Some(hub) = self.ledger.watch() {
            hub.publish(
                "search_focus",
                json!({
                    "pa": [pa.lon, pa.lat],
                    "goal": [goal.lon, goal.lat],
                }),
            );
        }
    }

    fn publish_wedge(
        &self,
        pa: &Candidate,
        goal: &Waypoint,
        min_m: f64,
        max_m: f64,
        half_angle_deg: f64,
    ) {
        let ring = annulus_sector_ring(pa.lat, pa.lon, goal.lat, goal.lon, min_m, max_m, half_angle_deg);
        if let Some(hub) = self.ledger.watch() {
            hub.publish(
                "search_wedge",
                json!({
                    "ring": ring,
                    "min_m": min_m,
                    "max_m": max_m,
                    "half_angle_deg": half_angle_deg,
                    "pa": [pa.lon, pa.lat],
                    "goal": [goal.lon, goal.lat],
                }),
            );
        }
    }

    fn publish_chain_partial(&self, segment_path: &[usize]) {
        if let Some(hub) = self.ledger.watch() {
            let mut combined = self.chain_prefix.clone();
            for &idx in segment_path {
                if combined.last() == Some(&idx) {
                    continue;
                }
                combined.push(idx);
            }
            let sites: Vec<_> = combined
                .iter()
                .filter_map(|&idx| {
                    self.registry.get(idx).map(|c| {
                        json!({
                            "lat": c.lat,
                            "lon": c.lon,
                            "name": c.slug.as_deref().unwrap_or(&c.id),
                            "kind": if c.kind == crate::candidates::CandidateKind::Existing {
                                "existing"
                            } else {
                                "peak"
                            },
                        })
                    })
                })
                .collect();
            hub.publish(
                "chain_partial",
                json!({
                    "sites": sites,
                    "hop": sites.len(),
                }),
            );
            for &idx in &combined {
                self.schedule_viewshed_warm(idx);
            }
        }
    }

    fn publish_chain_pop(&self) {
        if let Some(hub) = self.ledger.watch() {
            hub.publish("chain_pop", json!({}));
        }
    }

    fn covers_wp(&self, idx: usize, wp: &Waypoint) -> Result<bool> {
        let cand = self
            .registry
            .get(idx)
            .ok_or_else(|| anyhow::anyhow!("missing candidate {idx}"))?;
        let tx_h = site_tx_height(&self.preset, cand);
        covers_waypoint(
            self.session.as_ref(),
            &self.preset,
            &self.rf_json,
            cand,
            tx_h,
            wp,
            self.cache,
            self.ledger,
            &self.rf_key,
        )
    }

    fn mutual_link(&self, a_idx: usize, b_idx: usize) -> Result<bool> {
        let a = self.registry.get(a_idx).unwrap();
        let b = self.registry.get(b_idx).unwrap();
        let tx_a = site_tx_height(&self.preset, a);
        let tx_b = site_tx_height(&self.preset, b);
        let pair_key = digest_hex(&[
            self.rf_key.as_str(),
            &format!("{:.6},{:.6}", a.lat, a.lon),
            &format!("{:.6},{:.6}", b.lat, b.lon),
            &format!("{tx_a},{tx_b}"),
        ]);
        let detail = format!(
            "rf={} a={} b={}",
            short_digest(&self.rf_key),
            short_digest(&a.id),
            short_digest(&b.id)
        );
        let mutual = self.cache.get_or_insert_bool(
            self.ledger,
            "link",
            "links",
            &pair_key,
            &detail,
            || {
                Ok(self.session.seek_repeater_link_batch(
                    a.lat,
                    a.lon,
                    tx_a,
                    &[(b.lat, b.lon, tx_b)],
                    &self.rf_json,
                )?[0])
            },
        )?;
        self.ledger
            .watch_link_probe(a.lon, a.lat, b.lon, b.lat, mutual);
        Ok(mutual)
    }

    /// Keep only peaks inside Pa's one-way viewshed; score by goal coverage.
    /// Returns `(idx, score, covers_goal)` — never peaks outside Pa coverage.
    fn score_wedge_peaks(
        &mut self,
        pa: &Candidate,
        goal: &Waypoint,
        peaks: &[Peak],
    ) -> Result<Vec<(usize, f64, bool)>> {
        let mut scored = Vec::new();
        let mut watch_peaks = Vec::new();
        let mut watch_published = 0usize;
        let mut in_viewshed_n = 0usize;
        let mut goal_cover_n = 0usize;

        for (i, peak) in peaks.iter().enumerate() {
            let idx = self.registry.register_peak(peak);
            let next = self.registry.get(idx).unwrap().clone();
            let in_viewshed = covers_candidate_one_way(
                self.session.as_ref(),
                &self.preset,
                &self.rf_json,
                pa,
                &next,
                self.cache,
                self.ledger,
                &self.rf_key,
            )?;
            let covers_goal = if in_viewshed {
                self.covers_wp(idx, goal)?
            } else {
                false
            };
            if in_viewshed {
                in_viewshed_n += 1;
                if covers_goal {
                    goal_cover_n += 1;
                }
                let score = score_peak(pa.lat, pa.lon, goal, peak, covers_goal);
                scored.push((idx, score, covers_goal));
            }
            watch_peaks.push(json!({
                "lat": peak.lat,
                "lon": peak.lon,
                "elev_m": peak.elev_m,
                "covered": in_viewshed,
                "covers_goal": covers_goal,
            }));

            if (i + 1) % 16 == 0 || i + 1 == peaks.len() {
                self.ledger
                    .watch_coverage_progress(i + 1, in_viewshed_n, peaks.len());
                if watch_published < watch_peaks.len() {
                    if let Some(hub) = self.ledger.watch() {
                        hub.publish(
                            "peaks_batch",
                            json!({
                                "tile": "search",
                                "peaks": &watch_peaks[watch_published..],
                            }),
                        );
                    }
                    watch_published = watch_peaks.len();
                }
            }
        }

        self.ledger.search_step(&format!(
            "wedge coverage: peaks={} in_viewshed={} goal_cover={}",
            peaks.len(),
            in_viewshed_n,
            goal_cover_n
        ));
        Ok(scored)
    }

    /// Scan the goal wedge, widening until a peak lies in Pa's viewshed.
    /// Returns viewshed-gated `(idx, score, covers_goal)` sorted best-first.
    fn discover_peaks_in_wedge(
        &mut self,
        pa_idx: usize,
        goal: &Waypoint,
        min_m: f64,
        max_m: f64,
    ) -> Result<Vec<(usize, f64, bool)>> {
        let pa = self.registry.get(pa_idx).unwrap().clone();
        let pa_name = pa.slug.as_deref().unwrap_or(&pa.id);
        self.publish_search_focus(&pa, goal);

        let mut half_exclude = 0.0;
        let mut half_include = WEDGE_HALF_START_DEG;
        let mut last_scored: Vec<(usize, f64, bool)> = Vec::new();

        while half_include <= 180.0 + 1e-6 {
            self.publish_wedge(&pa, goal, min_m, max_m, half_include);
            self.ledger.search_step(&format!(
                "wedge from {pa_name}: ring {:.0}-{:.0}m ±{half_include:.1}°{}",
                min_m,
                max_m,
                if half_exclude > 0.0 {
                    format!(" (flanks +{half_exclude:.1}°)")
                } else {
                    String::new()
                }
            ));

            let peaks = self.scan_ring_sector_peaks(
                pa.lat,
                pa.lon,
                goal,
                min_m,
                max_m,
                half_exclude,
                half_include,
            )?;

            if !peaks.is_empty() {
                let mut scored = self.score_wedge_peaks(&pa, goal, &peaks)?;
                scored.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
                if !scored.is_empty() {
                    // In-viewshed peaks found — stop widening this ring.
                    return Ok(scored);
                }
                last_scored = scored;
            }

            half_exclude = half_include;
            half_include += WEDGE_HALF_STEP_DEG;
        }

        Ok(last_scored)
    }

    fn try_expand(
        &mut self,
        pa_idx: usize,
        goal: &Waypoint,
        visited: &HashSet<usize>,
        wedge_tried: &mut HashSet<(usize, usize, usize)>,
        link_failed: &HashSet<(usize, usize)>,
    ) -> Result<Option<usize>> {
        let pa = self.registry.get(pa_idx).unwrap().clone();
        let pa_label = pa.slug.as_deref().unwrap_or(&pa.id).to_string();
        let mut relay_idxs = Vec::new();
        let mut band_outer = self.hop_m;
        for &frac in HOP_RING_FRACS {
            let min_m = self.hop_m * frac;
            let max_m = band_outer;
            band_outer = min_m;
            if max_m <= min_m + 1.0 {
                continue;
            }
            let key = (
                pa_idx,
                (min_m / 100.0) as usize,
                (max_m / 100.0) as usize,
            );
            if wedge_tried.contains(&key) {
                continue;
            }
            wedge_tried.insert(key);
            self.ledger.search_step(&format!(
                "expand ring {min_m:.0}-{max_m:.0}m toward goal (outer→inner)"
            ));
            let candidates = self.discover_peaks_in_wedge(pa_idx, goal, min_m, max_m)?;
            let cover_n = candidates.iter().filter(|(_, _, c)| *c).count();
            self.ledger.search_step(&format!(
                "try {cover_n} goal-covering in-viewshed peak(s); relays must not pass uncovered goal"
            ));
            for (next_idx, _score, covers_goal) in candidates {
                if next_idx == pa_idx || visited.contains(&next_idx) {
                    continue;
                }
                if link_failed.contains(&(pa_idx, next_idx)) {
                    continue;
                }
                let next = self.registry.get(next_idx).unwrap().clone();
                let past_goal = peak_is_past_goal(
                    pa.lat, pa.lon, goal.lat, goal.lon, next.lat, next.lon,
                );
                // Past the waypoint only if this hop covers it (releases the goal).
                if past_goal && !covers_goal {
                    continue;
                }
                if !covers_goal {
                    relay_idxs.push(next_idx);
                    continue;
                }
                self.ledger.search_step(&format!(
                    "link check (covers goal{}) {pa_label} → {}",
                    if past_goal { ", past waypoint" } else { "" },
                    next.slug.as_deref().unwrap_or(&next.id)
                ));
                if self.mutual_link(pa_idx, next_idx)? {
                    return Ok(Some(next_idx));
                }
            }
        }
        // Relays toward (not past) the still-locked goal waypoint.
        self.ledger.search_step(&format!(
            "goal still unlocked — try {} in-viewshed relay(s) short of waypoint",
            relay_idxs.len()
        ));
        for next_idx in relay_idxs {
            if next_idx == pa_idx || visited.contains(&next_idx) {
                continue;
            }
            if link_failed.contains(&(pa_idx, next_idx)) {
                continue;
            }
            let next = self.registry.get(next_idx).unwrap();
            self.ledger.search_step(&format!(
                "link check (relay short of goal) {pa_label} → {}",
                next.slug.as_deref().unwrap_or(&next.id)
            ));
            if self.mutual_link(pa_idx, next_idx)? {
                return Ok(Some(next_idx));
            }
        }
        Ok(None)
    }

    fn find_seed_for_waypoint(&mut self, wp: &Waypoint) -> Result<usize> {
        self.ledger.search_step(&format!(
            "seed for wp ({:.4}, {:.4}) — check existing sites",
            wp.lat,
            wp.lon
        ));
        let existing = covering_indices(
            self.preset_path,
            self.session.as_ref(),
            self.registry.as_slice(),
            wp,
            self.cache,
            self.ledger,
        )?;
        if let Some(&idx) = existing.first() {
            let name = self
                .registry
                .get(idx)
                .map(|c| c.slug.as_deref().unwrap_or(&c.id))
                .unwrap_or("?");
            self.ledger.search_step(&format!("seed hit: existing site {name}"));
            return Ok(idx);
        }
        self.ledger.search_step("no existing cover — scan ring bands near waypoint");
        let seed_goal = Waypoint {
            lat: wp.lat + 0.01,
            lon: wp.lon,
        };
        let mut band_outer = self.hop_m;
        for &frac in HOP_RING_FRACS {
            let min_m = self.hop_m * frac;
            let max_m = band_outer;
            band_outer = min_m;
            if max_m <= min_m + 1.0 {
                continue;
            }
            let peaks = self.scan_ring_sector_peaks(
                wp.lat,
                wp.lon,
                &seed_goal,
                min_m,
                max_m,
                0.0,
                180.0,
            )?;
            let mut ranked = peaks;
            ranked.sort_by(|a, b| {
                b.elev_m
                    .partial_cmp(&a.elev_m)
                    .unwrap_or(std::cmp::Ordering::Equal)
            });
            for peak in &ranked {
                let idx = self.registry.register_peak(peak);
                if self.covers_wp(idx, wp)? {
                    self.ledger.search_step(&format!(
                        "seed peak at ({:.4}, {:.4}) elev={:.0}m",
                        peak.lat,
                        peak.lon,
                        peak.elev_m
                    ));
                    return Ok(idx);
                }
            }
        }
        bail!(
            "no seed covers waypoint ({:.5}, {:.5})",
            wp.lat,
            wp.lon
        )
    }

    /// Hold the goal waypoint locked until some chain site covers it; only then
    /// is the segment released and the solver may advance to the next waypoint.
    fn dfs_to_goal(
        &mut self,
        path: &mut Vec<usize>,
        goal: &Waypoint,
        visited: &mut HashSet<usize>,
        wedge_tried: &mut HashSet<(usize, usize, usize)>,
        link_failed: &mut HashSet<(usize, usize)>,
        depth: usize,
    ) -> Result<bool> {
        let pa_idx = *path.last().unwrap();
        if self.covers_wp(pa_idx, goal)? {
            self.ledger.search_step(&format!(
                "goal waypoint covered by {} — release segment",
                self.registry
                    .get(pa_idx)
                    .map(|c| c.slug.as_deref().unwrap_or(&c.id).to_string())
                    .unwrap_or_else(|| format!("site-{pa_idx}"))
            ));
            return Ok(true);
        }
        if depth >= 32 {
            return Ok(false);
        }
        while let Some(next) =
            self.try_expand(pa_idx, goal, visited, wedge_tried, link_failed)?
        {
            visited.insert(next);
            path.push(next);
            self.publish_chain_partial(path);
            if self.dfs_to_goal(path, goal, visited, wedge_tried, link_failed, depth + 1)? {
                return Ok(true);
            }
            link_failed.insert((pa_idx, next));
            path.pop();
            visited.remove(&next);
            self.publish_chain_pop();
        }
        Ok(false)
    }

    fn solve_segment(&mut self, seg: usize, waypoints: &[Waypoint], start_idx: usize) -> Result<Vec<usize>> {
        let goal = &waypoints[seg + 1];
        let mut path = vec![start_idx];
        let mut visited = HashSet::from([start_idx]);
        let mut wedge_tried = HashSet::new();
        let mut link_failed = HashSet::new();
        self.publish_chain_partial(&path);
        if self.dfs_to_goal(
            &mut path,
            goal,
            &mut visited,
            &mut wedge_tried,
            &mut link_failed,
            0,
        )? {
            if let Some(hub) = self.ledger.watch() {
                hub.publish(
                    "segment_done",
                    json!({
                        "segment": seg + 1,
                        "total": waypoints.len() - 1,
                        "hops": path.len().saturating_sub(1),
                    }),
                );
            }
            return Ok(path);
        }
        bail!(
            "segment {seg}: no RF chain from site {} to waypoint {}",
            start_idx,
            seg + 1
        )
    }
}

fn score_peak(
    from_lat: f64,
    from_lon: f64,
    goal: &Waypoint,
    peak: &Peak,
    covers_goal: bool,
) -> f64 {
    let goal_bearing = bearing_deg(from_lat, from_lon, goal.lat, goal.lon);
    let peak_bearing = bearing_deg(from_lat, from_lon, peak.lat, peak.lon);
    let align = 180.0 - angle_diff_deg(peak_bearing, goal_bearing);
    let base = peak.elev_m * 0.01 + align;
    if covers_goal {
        1_000_000.0 + base
    } else {
        base
    }
}

fn annulus_sector_ring(
    lat: f64,
    lon: f64,
    goal_lat: f64,
    goal_lon: f64,
    min_m: f64,
    max_m: f64,
    half_angle_deg: f64,
) -> Vec<[f64; 2]> {
    let goal_bearing = bearing_deg(lat, lon, goal_lat, goal_lon);
    let start_b = goal_bearing - half_angle_deg;
    let end_b = goal_bearing + half_angle_deg;
    let steps = 24usize;
    let mut ring = Vec::new();
    for i in 0..=steps {
        let t = i as f64 / steps as f64;
        let b = start_b + t * (end_b - start_b);
        let (la, lo) = destination_point(lat, lon, b, max_m);
        ring.push([lo, la]);
    }
    for i in (0..=steps).rev() {
        let t = i as f64 / steps as f64;
        let b = start_b + t * (end_b - start_b);
        let (la, lo) = destination_point(lat, lon, b, min_m.max(1.0));
        ring.push([lo, la]);
    }
    if !ring.is_empty() {
        ring.push(ring[0]);
    }
    ring
}

fn destination_point(lat: f64, lon: f64, bearing_deg: f64, distance_m: f64) -> (f64, f64) {
    let r = 6_371_000.0;
    let bearing = bearing_deg.to_radians();
    let lat1 = lat.to_radians();
    let lon1 = lon.to_radians();
    let ang = distance_m / r;
    let lat2 = (lat1.sin() * ang.cos() + lat1.cos() * ang.sin() * bearing.cos()).asin();
    let lon2 = lon1
        + (bearing.sin() * ang.sin() * lat1.cos())
            .atan2(ang.cos() - lat1.sin() * lat2.sin());
    (lat2.to_degrees(), lon2.to_degrees())
}

fn geometry_to_multipolygon(geom: Geometry<f64>) -> MultiPolygon<f64> {
    match geom {
        Geometry::MultiPolygon(mp) => mp,
        Geometry::Polygon(p) => MultiPolygon(vec![p]),
        _ => MultiPolygon(vec![]),
    }
}

fn merge_segment_into_path(path: &mut Vec<usize>, seg_path: &[usize]) {
    if seg_path.is_empty() {
        return;
    }
    if path.is_empty() {
        path.extend_from_slice(seg_path);
    } else if path.last() == seg_path.first() {
        path.extend(seg_path.iter().skip(1).copied());
    } else {
        path.extend_from_slice(seg_path);
    }
}

/// Prefix from route segment 0 .. `seg` (stops at first unsolved segment).
fn route_chain_prefix(segment_paths: &HashMap<usize, Vec<usize>>, seg: usize) -> Vec<usize> {
    let mut prefix = Vec::new();
    for s in 0..seg {
        let Some(seg_path) = segment_paths.get(&s) else {
            break;
        };
        merge_segment_into_path(&mut prefix, seg_path);
    }
    dedupe_consecutive_indices(&prefix)
}

fn assemble_route_chain(
    segment_paths: &HashMap<usize, Vec<usize>>,
    segment_count: usize,
) -> Result<Vec<usize>> {
    let mut full_path = Vec::new();
    for seg in 0..segment_count {
        let seg_path = segment_paths
            .get(&seg)
            .ok_or_else(|| anyhow::anyhow!("segment {seg} was not solved"))?;
        merge_segment_into_path(&mut full_path, seg_path);
    }
    Ok(dedupe_consecutive_indices(&full_path))
}

fn dedupe_consecutive_indices(path: &[usize]) -> Vec<usize> {
    let mut out = Vec::new();
    for &idx in path {
        if out.last() == Some(&idx) {
            continue;
        }
        out.push(idx);
    }
    out
}

pub fn search_route(
    preset_path: &Path,
    session: Arc<Session>,
    registry: &mut CandidateRegistry,
    waypoints: &[Waypoint],
    cache: &FinderCache,
    ledger: &CacheLedger,
) -> Result<(Vec<usize>, usize)> {
    ledger.phase_start("search");
    ledger.search_step("initializing solver…");
    let mut ctx = SearchCtx::new(preset_path, session, registry, cache, ledger, waypoints)?;
    let overview = gap_overview_cached(
        cache,
        waypoints,
        &ctx.land_index,
        &ctx.eligible_digest,
        ctx.hop_m,
        ledger,
    )?;
    let segment_count = waypoints.len().saturating_sub(1);
    let mut segment_paths: HashMap<usize, Vec<usize>> = HashMap::new();

    for &seg in &overview.segment_order {
        if let Some(hub) = ledger.watch() {
            hub.publish(
                "segment_active",
                json!({
                    "segment": seg + 1,
                    "total": segment_count,
                    "from": [waypoints[seg].lon, waypoints[seg].lat],
                    "to": [waypoints[seg + 1].lon, waypoints[seg + 1].lat],
                }),
            );
        }
        ledger.search_step(&format!(
            "segment {}/{} wp {}→{}",
            seg + 1,
            segment_count,
            seg,
            seg + 1
        ));
        let start_idx = if seg == 0 {
            ctx.find_seed_for_waypoint(&waypoints[0])?
        } else if let Some(prev_path) = segment_paths.get(&(seg - 1)) {
            if let Some(&last) = prev_path.last() {
                if ctx.covers_wp(last, &waypoints[seg])? {
                    last
                } else {
                    ctx.find_seed_for_waypoint(&waypoints[seg])?
                }
            } else {
                ctx.find_seed_for_waypoint(&waypoints[seg])?
            }
        } else {
            ctx.find_seed_for_waypoint(&waypoints[seg])?
        };
        ctx.set_chain_prefix(&route_chain_prefix(&segment_paths, seg));
        let seg_path = ctx.solve_segment(seg, waypoints, start_idx)?;
        segment_paths.insert(seg, seg_path);
        let partial = route_chain_prefix(&segment_paths, segment_count);
        ctx.publish_chain_partial(&partial);
    }

    let full_path = assemble_route_chain(&segment_paths, segment_count)?;
    ctx.publish_chain_partial(&full_path);
    ledger.phase_end(
        "search",
        &format!(
            "sites={} segments={} candidates={}",
            full_path.len(),
            segment_count,
            registry.len()
        ),
    );
    Ok((full_path, segment_count))
}

#[cfg(test)]
mod tests {
    use super::*;
    use geo::{LineString, Polygon};

    #[test]
    fn peak_in_ring_sector_accepts_in_band_and_bearing() {
        let from_lat = 39.0;
        let from_lon = -116.0;
        let goal_lat = 39.05;
        let goal_lon = -116.0;
        let peak_lat = 39.045;
        let peak_lon = -116.0;
        let dist = haversine_m(from_lat, from_lon, peak_lat, peak_lon);
        assert!(peak_in_ring_sector(
            from_lat,
            from_lon,
            goal_lat,
            goal_lon,
            peak_lat,
            peak_lon,
            dist * 0.8,
            dist * 1.05,
            10.0
        ));
    }

    #[test]
    fn peak_in_ring_sector_rejects_wrong_bearing() {
        let from_lat = 39.0;
        let from_lon = -116.0;
        let goal_lat = 39.05;
        let goal_lon = -116.0;
        let peak_lat = 39.0;
        let peak_lon = -115.95;
        let dist = haversine_m(from_lat, from_lon, peak_lat, peak_lon);
        assert!(!peak_in_ring_sector(
            from_lat,
            from_lon,
            goal_lat,
            goal_lon,
            peak_lat,
            peak_lon,
            dist * 0.5,
            dist * 1.05,
            5.0
        ));
    }

    #[test]
    fn wedge_half_angles_start_at_five_deg_total() {
        let halves = wedge_half_angles();
        assert!((halves[0] - 2.5).abs() < 1e-6);
        assert!(halves.last().copied().unwrap_or(0.0) >= 180.0);
    }

    #[test]
    fn gap_overview_marks_ineligible_run() {
        let square = Polygon::new(
            LineString::from(vec![
                (-116.01, 38.99),
                (-115.99, 38.99),
                (-115.99, 39.01),
                (-116.01, 39.01),
                (-116.01, 38.99),
            ]),
            vec![],
        );
        let mp = MultiPolygon(vec![square]);
        let land = LandFilterIndex::from_multipolygon(&mp);
        let wps = vec![
            Waypoint {
                lat: 39.0,
                lon: -116.005,
            },
            Waypoint {
                lat: 39.0,
                lon: -115.995,
            },
        ];
        let ledger = CacheLedger::new(true);
        let overview = gap_overview(&wps, &land, 50_000.0, &ledger);
        assert!(!overview.gaps.is_empty() || overview.segment_order.contains(&0));
    }

    #[test]
    fn assemble_route_chain_orders_by_segment_not_solve_order() {
        let mut segment_paths = HashMap::new();
        segment_paths.insert(5, vec![50, 51]);
        segment_paths.insert(0, vec![1, 2]);
        segment_paths.insert(1, vec![2, 3]);
        let assembled = assemble_route_chain(&segment_paths, 2).unwrap();
        assert_eq!(assembled, vec![1, 2, 3]);
    }

    #[test]
    fn gap_overview_cache_hit_on_second_run() {
        let dir = tempfile::tempdir().unwrap();
        let preset = dir.path().join("config.yaml");
        std::fs::write(&preset, "sites: {}\nlinks: []\n").unwrap();
        let cache = FinderCache::new(&preset).unwrap();
        let square = Polygon::new(
            LineString::from(vec![
                (-116.01, 38.99),
                (-115.99, 38.99),
                (-115.99, 39.01),
                (-116.01, 39.01),
                (-116.01, 38.99),
            ]),
            vec![],
        );
        let land = LandFilterIndex::from_multipolygon(&MultiPolygon(vec![square]));
        let wps = vec![
            Waypoint {
                lat: 39.0,
                lon: -116.005,
            },
            Waypoint {
                lat: 39.0,
                lon: -115.995,
            },
        ];
        let ledger1 = CacheLedger::new(true);
        let a = gap_overview_cached(&cache, &wps, &land, "land-test", 50_000.0, &ledger1).unwrap();
        let ledger2 = CacheLedger::new(true);
        let b = gap_overview_cached(&cache, &wps, &land, "land-test", 50_000.0, &ledger2).unwrap();
        assert_eq!(a.gaps.len(), b.gaps.len());
        assert_eq!(a.segment_order, b.segment_order);
        let stats = ledger2.stats().get("gap_overview").cloned().unwrap_or_default();
        assert_eq!(stats.hits, 1);
        assert_eq!(stats.misses, 0);
    }

    #[test]
    fn score_peak_prefers_goal_covering() {
        let goal = Waypoint {
            lat: 39.05,
            lon: -116.0,
        };
        let peak = Peak {
            lat: 39.045,
            lon: -116.0,
            elev_m: 2000.0,
        };
        let relay = score_peak(39.0, -116.0, &goal, &peak, false);
        let covers = score_peak(39.0, -116.0, &goal, &peak, true);
        assert!(relay > 0.0);
        assert!(covers > relay);
        assert!(covers > 1_000_000.0);
    }

    #[test]
    fn schema_v8_digest_differs_from_v7() {
        use crate::CACHE_SCHEMA_VERSION;
        let v8 = digest_hex(&["run", "route", "installed", &format!("schema={CACHE_SCHEMA_VERSION}")]);
        let v7 = digest_hex(&["run", "route", "installed", "schema=7"]);
        assert_eq!(CACHE_SCHEMA_VERSION, 8);
        assert_ne!(v8, v7);
    }

    #[test]
    fn peak_past_goal_detects_overshoot() {
        let pa_lat = 39.0;
        let pa_lon = -116.0;
        let goal_lat = 39.05;
        let goal_lon = -116.0;
        // Between Pa and goal — not past.
        assert!(!peak_is_past_goal(
            pa_lat, pa_lon, goal_lat, goal_lon, 39.03, -116.0
        ));
        // Beyond goal along the same bearing — past.
        assert!(peak_is_past_goal(
            pa_lat, pa_lon, goal_lat, goal_lon, 39.10, -116.0
        ));
    }
}
