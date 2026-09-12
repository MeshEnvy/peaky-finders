//! Goal-seek candidates from ``peaks.yaml`` plus preset sites (P2P RF via splatter).

use std::collections::{HashMap, HashSet};
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::path::PathBuf;
use std::sync::{Arc, Condvar, Mutex};
use std::thread;
use std::time::Instant;

use anyhow::Result;
use geo::{BooleanOps, BoundingRect, Coord, Geometry, HasDimensions, Intersects, LineString, Point, Polygon, Rect};
use peaky_geo::{load_or_build_eligible_land_parts, EligibleLandError, LonLatBBox};
use peaky_preset::{load_peaks_catalog, load_preset, Preset, SeekConfig};
use serde_json::{json, Value};
use splatter::peaks::LandFilterIndex;
use splatter::Session;

use crate::rf::{pair_within_hop_range, preset_to_request, default_repeater_tx_height_m, resolved_site_tx_height_m, rf_json_for_preset};
use crate::seek_progress::SeekProgressHub;
use crate::seek_rank::{
    angle_diff_deg, bearing_deg, cmp_seek_peak_rank, forward_reach_m, haversine_m,
    hop_makes_goal_progress, peak_is_past_goal, SeekRankScore,
};

const SEEK_EXCLUDE_PROXIMITY_M: f64 = 100.0;
const SEEK_GOAL_DEDUP_M: f64 = 1500.0;
const SEEK_SITE_PEAK_DEDUP_M: f64 = 500.0;
const SEEK_CANDIDATE_SEP_M: f64 = 800.0;
const SEEK_MESH_NEIGHBOR_CAP: usize = 32;

#[derive(Debug, thiserror::Error)]
#[error("{0}")]
pub struct SeekError(pub String);

#[derive(Debug, Clone)]
pub struct SeekPoint {
    pub lat: f64,
    pub lon: f64,
}

#[derive(Debug, Clone)]
pub struct SeekRequest {
    pub slug: String,
    pub preset_path: PathBuf,
    pub from_lat: f64,
    pub from_lon: f64,
    pub goal_lat: f64,
    pub goal_lon: f64,
    pub west: f64,
    pub south: f64,
    pub east: f64,
    pub north: f64,
    pub exclude: Vec<SeekPoint>,
    pub exclude_slugs: HashSet<String>,
    pub goal_elev_m: Option<f64>,
    pub peak_bin_size_m: Option<f64>,
}

struct SeekJob {
    slug: String,
    gen: u64,
    request: SeekRequest,
}

struct QueueInner {
    pending: HashMap<String, SeekJob>,
}

struct SeekJobQueue {
    inner: Mutex<QueueInner>,
    cv: Condvar,
}

impl SeekJobQueue {
    fn new() -> Self {
        Self {
            inner: Mutex::new(QueueInner {
                pending: HashMap::new(),
            }),
            cv: Condvar::new(),
        }
    }

    fn submit(&self, job: SeekJob) {
        let mut inner = self.inner.lock().unwrap();
        inner.pending.insert(job.slug.clone(), job);
        self.cv.notify_one();
    }

    fn take(&self) -> SeekJob {
        let mut inner = self.inner.lock().unwrap();
        loop {
            if let Some((_, job)) = inner.pending.drain().next() {
                return job;
            }
            inner = self.cv.wait(inner).unwrap();
        }
    }
}

#[derive(Clone)]
pub struct SeekHub {
    progress: SeekProgressHub,
    queue: Arc<SeekJobQueue>,
}

impl SeekHub {
    pub fn new(session: Arc<Session>, verbose: bool) -> Self {
        let queue = Arc::new(SeekJobQueue::new());
        let progress = SeekProgressHub::default();
        for _ in 0..seek_workers() {
            let queue = Arc::clone(&queue);
            let progress = progress.clone();
            let session = Arc::clone(&session);
            thread::spawn(move || {
                loop {
                    let job = queue.take();
                    let slug = job.slug.clone();
                    let gen = job.gen;
                    let result = catch_unwind(AssertUnwindSafe(|| {
                        run_seek_job(&session, verbose, &progress, job);
                    }));
                    if result.is_err() {
                        progress.finish(
                            &slug,
                            gen,
                            "error",
                            None,
                            Some("Seek worker crashed during geometry or RF scan"),
                            Some(500),
                        );
                    }
                }
            });
        }
        Self { progress, queue }
    }

    pub fn enqueue(&self, request: SeekRequest, verbose: bool) -> Result<u64, SeekError> {
        let gen = self.progress.begin(&request.slug);
        let job = SeekJob {
            slug: request.slug.clone(),
            gen,
            request,
        };
        self.queue.submit(job);
        let _ = verbose;
        Ok(gen)
    }

    pub fn poll(&self, slug: &str) -> Value {
        self.progress.poll(slug, slug)
    }
}

fn seek_workers() -> usize {
    std::env::var("PEAKY_SERVE_SEEK_WORKERS")
        .ok()
        .and_then(|v| v.parse().ok())
        .filter(|n| *n >= 1)
        .unwrap_or(1)
}

fn run_seek_job(session: &Session, verbose: bool, progress: &SeekProgressHub, job: SeekJob) {
    let slug = job.slug.clone();
    let gen = job.gen;
    match load_seek_candidates_body(session, verbose, progress, &job.request, gen) {
        Ok(result) => {
            progress.finish(&slug, gen, "done", Some(result), None, None);
        }
        Err(SeekRunError::Cancelled) => {
            progress.finish(&slug, gen, "cancelled", None, None, None);
        }
        Err(SeekRunError::User(msg, status)) => {
            progress.finish(&slug, gen, "error", None, Some(&msg), Some(status));
        }
        Err(SeekRunError::Internal(msg)) => {
            progress.finish(&slug, gen, "error", None, Some(&msg), Some(500));
        }
    }
}

enum SeekRunError {
    Cancelled,
    User(String, u16),
    Internal(String),
}

impl From<anyhow::Error> for SeekRunError {
    fn from(value: anyhow::Error) -> Self {
        SeekRunError::Internal(value.to_string())
    }
}

pub fn parse_seek_request(
    slug: &str,
    preset_path: std::path::PathBuf,
    params: &HashMap<String, String>,
) -> Result<SeekRequest, SeekError> {
    let from_lat: f64 = params
        .get("from_lat")
        .ok_or_else(|| SeekError("from_lat required".into()))?
        .parse()
        .map_err(|_| SeekError("from_lat must be a number".into()))?;
    let from_lon: f64 = params
        .get("from_lon")
        .ok_or_else(|| SeekError("from_lon required".into()))?
        .parse()
        .map_err(|_| SeekError("from_lon must be a number".into()))?;
    let goal_lat: f64 = params
        .get("goal_lat")
        .ok_or_else(|| SeekError("goal_lat required".into()))?
        .parse()
        .map_err(|_| SeekError("goal_lat must be a number".into()))?;
    let goal_lon: f64 = params
        .get("goal_lon")
        .ok_or_else(|| SeekError("goal_lon required".into()))?
        .parse()
        .map_err(|_| SeekError("goal_lon must be a number".into()))?;
    let bbox = params
        .get("bbox")
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .ok_or_else(|| SeekError("bbox required".into()))?;
    let (west, south, east, north) = parse_bbox(&bbox)?;

    if !(-90.0..=90.0).contains(&from_lat) || !(-180.0..=180.0).contains(&from_lon) {
        return Err(SeekError(format!(
            "from coordinates out of bounds: ({from_lat}, {from_lon})"
        )));
    }
    if !(-90.0..=90.0).contains(&goal_lat) || !(-180.0..=180.0).contains(&goal_lon) {
        return Err(SeekError(format!(
            "goal coordinates out of bounds: ({goal_lat}, {goal_lon})"
        )));
    }

    let goal_elev_m = match params.get("goal_elev_m").map(|s| s.trim()).filter(|s| !s.is_empty()) {
        None => None,
        Some(raw) => Some(
            raw.parse()
                .map_err(|_| SeekError("goal_elev_m must be a number".into()))?,
        ),
    };
    let peak_bin_size_m = match params.get("peak_bin_size_m").map(|s| s.trim()).filter(|s| !s.is_empty()) {
        None => None,
        Some(raw) => Some(
            raw.parse()
                .map_err(|_| SeekError("peak_bin_size_m must be a number".into()))?,
        ),
    };

    Ok(SeekRequest {
        slug: slug.to_string(),
        preset_path,
        from_lat,
        from_lon,
        goal_lat,
        goal_lon,
        west,
        south,
        east,
        north,
        exclude: parse_exclude_points(params.get("exclude").map(String::as_str))?,
        exclude_slugs: parse_exclude_slugs(params.get("exclude_slugs").map(String::as_str)),
        goal_elev_m,
        peak_bin_size_m,
    })
}

fn parse_bbox(raw: &str) -> Result<(f64, f64, f64, f64), SeekError> {
    let parts: Vec<&str> = raw.split(',').map(str::trim).collect();
    if parts.len() != 4 {
        return Err(SeekError("bbox must be west,south,east,north".into()));
    }
    let west: f64 = parts[0]
        .parse()
        .map_err(|_| SeekError("bbox must be west,south,east,north".into()))?;
    let south: f64 = parts[1]
        .parse()
        .map_err(|_| SeekError("bbox must be west,south,east,north".into()))?;
    let east: f64 = parts[2]
        .parse()
        .map_err(|_| SeekError("bbox must be west,south,east,north".into()))?;
    let north: f64 = parts[3]
        .parse()
        .map_err(|_| SeekError("bbox must be west,south,east,north".into()))?;
    if west >= east || south >= north {
        return Err(SeekError("bbox west<east and south<north required".into()));
    }
    Ok((west, south, east, north))
}

fn parse_exclude_points(raw: Option<&str>) -> Result<Vec<SeekPoint>, SeekError> {
    let Some(raw) = raw.map(str::trim).filter(|s| !s.is_empty()) else {
        return Ok(Vec::new());
    };
    let mut out = Vec::new();
    for chunk in raw.split(';') {
        let chunk = chunk.trim();
        if chunk.is_empty() {
            continue;
        }
        let parts: Vec<&str> = chunk.split(',').map(str::trim).collect();
        if parts.len() != 2 {
            return Err(SeekError(
                "exclude must be lat,lon pairs separated by semicolons".into(),
            ));
        }
        out.push(SeekPoint {
            lat: parts[0]
                .parse()
                .map_err(|_| SeekError("exclude must be lat,lon pairs".into()))?,
            lon: parts[1]
                .parse()
                .map_err(|_| SeekError("exclude must be lat,lon pairs".into()))?,
        });
    }
    Ok(out)
}

fn parse_exclude_slugs(raw: Option<&str>) -> HashSet<String> {
    raw.map(str::trim)
        .filter(|s| !s.is_empty())
        .map(|s| {
            s.split(';')
                .map(str::trim)
                .filter(|p| !p.is_empty())
                .map(str::to_string)
                .collect()
        })
        .unwrap_or_default()
}

pub fn resolve_seek_peak_bin_size_m(seek_cfg: &SeekConfig, requested: Option<f64>) -> Result<f64, SeekError> {
    const MIN_M: f64 = 500.0;
    let ceiling = MIN_M.max(seek_cfg.peak_bin_size_m);
    let Some(req) = requested else {
        return Ok(ceiling);
    };
    if !req.is_finite() || req <= 0.0 {
        return Err(SeekError("peak_bin_size_m must be a positive number".into()));
    }
    Ok(MIN_M.max(ceiling.min(req)))
}

/// Load ``peaks/`` rows that pass ``keep(lat, lon)`` (deny rows omitted).
pub fn catalog_peaks_filtered(
    preset_path: &std::path::Path,
    keep: impl Fn(f64, f64) -> bool,
) -> Result<(Vec<(f64, f64, f64)>, usize), String> {
    let catalog = load_peaks_catalog(preset_path).map_err(|e| format!("load peaks catalog: {e}"))?;
    let n_catalog = catalog.entries.len();
    if n_catalog == 0 {
        return Err("peaks catalog is empty; run peaky peaks to build peaks/".into());
    }
    let peaks = catalog
        .entries
        .values()
        .filter(|entry| !entry.deny.unwrap_or(false))
        .filter(|entry| keep(entry.lat(), entry.lon()))
        .map(|entry| (entry.lon(), entry.lat(), entry.elev_m.unwrap_or(0.0)))
        .collect();
    Ok((peaks, n_catalog))
}

fn catalog_peaks_for_hop(
    preset_path: &std::path::Path,
    from_lat: f64,
    from_lon: f64,
    goal_lat: f64,
    goal_lon: f64,
    hop_m: f64,
    exclude: &[SeekPoint],
) -> Result<(Vec<(f64, f64, f64)>, usize), SeekRunError> {
    catalog_peaks_filtered(preset_path, |lat, lon| {
        peak_in_progress_lens(from_lat, from_lon, goal_lat, goal_lon, lat, lon, hop_m)
            && !near_excluded(lat, lon, exclude)
    })
    .map_err(|e| SeekRunError::User(e, 422))
}

/// Hop disc ∩ closer-to-goal than start.
fn peak_in_progress_lens(
    from_lat: f64,
    from_lon: f64,
    goal_lat: f64,
    goal_lon: f64,
    peak_lat: f64,
    peak_lon: f64,
    hop_radius_m: f64,
) -> bool {
    hop_makes_goal_progress(
        from_lat,
        from_lon,
        goal_lat,
        goal_lon,
        peak_lat,
        peak_lon,
        hop_radius_m,
    )
}

fn filter_peaks_in_progress_lens(
    peaks: Vec<(f64, f64, f64)>,
    from_lat: f64,
    from_lon: f64,
    goal_lat: f64,
    goal_lon: f64,
    hop_radius_m: f64,
) -> Vec<(f64, f64, f64)> {
    peaks
        .into_iter()
        .filter(|(lon, lat, _)| {
            peak_in_progress_lens(
                from_lat,
                from_lon,
                goal_lat,
                goal_lon,
                *lat,
                *lon,
                hop_radius_m,
            )
        })
        .collect()
}

fn progress_lens_half_angle_deg(hop_m: f64, goal_dist_m: f64) -> f64 {
    if goal_dist_m <= 1.0 || hop_m >= 2.0 * goal_dist_m {
        return 180.0;
    }
    (hop_m / (2.0 * goal_dist_m)).clamp(0.0, 1.0).acos().to_degrees()
}

fn signed_bearing_delta(from_deg: f64, to_deg: f64) -> f64 {
    let mut d = (to_deg - from_deg) % 360.0;
    if d > 180.0 {
        d -= 360.0;
    } else if d < -180.0 {
        d += 360.0;
    }
    d
}

fn append_bearing_arc(
    coords: &mut Vec<Coord<f64>>,
    center_lat: f64,
    center_lon: f64,
    radius_m: f64,
    from_bearing: f64,
    to_bearing: f64,
    steps: usize,
) {
    let delta = signed_bearing_delta(from_bearing, to_bearing);
    for i in 1..=steps {
        let t = i as f64 / steps as f64;
        let bearing = from_bearing + delta * t;
        let (lat, lon) = destination_point(center_lat, center_lon, bearing, radius_m);
        coords.push(Coord { x: lon, y: lat });
    }
}

/// Preset sites already inside the start hop disc (local mesh). Not lens-clipped.
fn local_mesh_neighbors(
    preset: &Preset,
    from_lat: f64,
    from_lon: f64,
    goal_lat: f64,
    goal_lon: f64,
    exclude: &[SeekPoint],
    exclude_slugs: &HashSet<String>,
) -> Vec<(f64, f64, f64)> {
    let mut rows: Vec<(f64, f64, f64)> = preset
        .sites
        .iter()
        .filter_map(|(slug, site)| {
            if exclude_slugs.contains(slug) {
                return None;
            }
            let lat = site.loc[0];
            let lon = site.loc[1];
            if haversine_m(from_lat, from_lon, lat, lon) <= 50.0 {
                return None;
            }
            if haversine_m(goal_lat, goal_lon, lat, lon) <= SEEK_GOAL_DEDUP_M {
                return None;
            }
            if !pair_within_hop_range(preset, from_lat, from_lon, lat, lon) {
                return None;
            }
            if near_excluded(lat, lon, exclude) {
                return None;
            }
            let h = resolved_site_tx_height_m(preset, site);
            Some((lat, lon, h))
        })
        .collect();
    rows.sort_by(|a, b| {
        haversine_m(from_lat, from_lon, a.0, a.1)
            .partial_cmp(&haversine_m(from_lat, from_lon, b.0, b.1))
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    rows.truncate(SEEK_MESH_NEIGHBOR_CAP);
    rows
}

fn count_candidate_mesh_links(
    session: &Session,
    mesh: &[(f64, f64, f64)],
    candidates: &[(f64, f64, f64)],
    hop_m: f64,
    rf_json: &str,
    ensure_active: &mut impl FnMut() -> Result<(), SeekRunError>,
) -> Result<Vec<u32>, SeekRunError> {
    let mut counts = vec![0u32; candidates.len()];
    if mesh.is_empty() || candidates.is_empty() {
        return Ok(counts);
    }
    let points: Vec<(f64, f64)> = mesh
        .iter()
        .map(|(lat, lon, _)| (*lat, *lon))
        .chain(candidates.iter().map(|(lat, lon, _)| (*lat, *lon)))
        .collect();
    session
        .ensure_tiles_for_points(&points, hop_m)
        .map_err(|e| SeekRunError::User(e.to_string(), 503))?;
    for &(site_lat, site_lon, site_h) in mesh {
        ensure_active()?;
        let flags = session
            .seek_repeater_link_batch(site_lat, site_lon, site_h, candidates, rf_json)
            .map_err(|e| SeekRunError::User(e.to_string(), 503))?;
        for (i, ok) in flags.into_iter().enumerate() {
            if ok {
                counts[i] += 1;
            }
        }
    }
    Ok(counts)
}

fn take_spatially_diverse(
    ranked: Vec<((f64, f64, f64), bool)>,
    min_sep_m: f64,
    cap: usize,
) -> Vec<((f64, f64, f64), bool)> {
    let mut taken = Vec::new();
    for item in ranked {
        if taken.len() >= cap {
            break;
        }
        let (lon, lat, _) = item.0;
        let too_close = taken.iter().any(|((olon, olat, _), _)| {
            haversine_m(lat, lon, *olat, *olon) <= min_sep_m
        });
        if !too_close {
            taken.push(item);
        }
    }
    taken
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

fn hop_disc_wgs84(lat: f64, lon: f64, radius_m: f64) -> Polygon<f64> {
    let n = 64usize;
    let mut coords = Vec::with_capacity(n + 1);
    for i in 0..=n {
        let bearing = 360.0 * i as f64 / n as f64;
        let (dest_lat, dest_lon) = destination_point(lat, lon, bearing, radius_m);
        coords.push(Coord {
            x: dest_lon,
            y: dest_lat,
        });
    }
    Polygon::new(LineString::from(coords), vec![])
}

/// Hop disc ∩ closer-to-goal disc (same rule as [`filter_peaks_in_progress_lens`]).
fn progress_lens_polygon(
    from_lat: f64,
    from_lon: f64,
    goal_lat: f64,
    goal_lon: f64,
    hop_radius_m: f64,
) -> Polygon<f64> {
    let goal_dist = haversine_m(from_lat, from_lon, goal_lat, goal_lon);
    let goal_bearing = bearing_deg(from_lat, from_lon, goal_lat, goal_lon);
    let half = progress_lens_half_angle_deg(hop_radius_m, goal_dist);
    let n = 32usize;
    let mut coords = vec![Coord {
        x: from_lon,
        y: from_lat,
    }];
    for i in 0..=n {
        let t = i as f64 / n as f64;
        let bearing = goal_bearing - half + t * (2.0 * half);
        let (lat, lon) = destination_point(from_lat, from_lon, bearing, hop_radius_m);
        coords.push(Coord { x: lon, y: lat });
    }
    let (plus_lat, plus_lon) = destination_point(from_lat, from_lon, goal_bearing + half, hop_radius_m);
    let (minus_lat, minus_lon) = destination_point(from_lat, from_lon, goal_bearing - half, hop_radius_m);
    let b_plus = bearing_deg(goal_lat, goal_lon, plus_lat, plus_lon);
    let b_minus = bearing_deg(goal_lat, goal_lon, minus_lat, minus_lon);
    let b_near = bearing_deg(goal_lat, goal_lon, from_lat, from_lon);
    append_bearing_arc(&mut coords, goal_lat, goal_lon, goal_dist, b_plus, b_near, n);
    append_bearing_arc(&mut coords, goal_lat, goal_lon, goal_dist, b_near, b_minus, n);
    coords.push(Coord {
        x: from_lon,
        y: from_lat,
    });
    Polygon::new(LineString::from(coords), vec![])
}

fn bbox_intersection(
    a: (f64, f64, f64, f64),
    b: (f64, f64, f64, f64),
) -> (f64, f64, f64, f64) {
    (
        a.0.max(b.0),
        a.1.max(b.1),
        a.2.min(b.2),
        a.3.min(b.3),
    )
}

fn hop_disc_bbox(lat: f64, lon: f64, hop_m: f64) -> (f64, f64, f64, f64) {
    hop_disc_wgs84(lat, lon, hop_m)
        .bounding_rect()
        .map(|r| (r.min().x, r.min().y, r.max().x, r.max().y))
        .unwrap_or((lon, lat, lon, lat))
}

/// Land/DEM clip for landing: start hop disc ∩ goal hop disc.
fn landing_rf_lens_bbox(
    from_lat: f64,
    from_lon: f64,
    goal_lat: f64,
    goal_lon: f64,
    hop_m: f64,
) -> (f64, f64, f64, f64) {
    bbox_intersection(
        hop_disc_bbox(from_lat, from_lon, hop_m),
        hop_disc_bbox(goal_lat, goal_lon, hop_m),
    )
}

/// Bounding box for peak DEM scan: hop disc ∩ closer-to-goal disc (not map viewport).
fn seek_progress_lens_scan_bbox(
    from_lat: f64,
    from_lon: f64,
    goal_lat: f64,
    goal_lon: f64,
    hop_m: f64,
) -> (f64, f64, f64, f64) {
    let lens = progress_lens_polygon(from_lat, from_lon, goal_lat, goal_lon, hop_m);
    if let Some(rect) = lens.bounding_rect() {
        return (rect.min().x, rect.min().y, rect.max().x, rect.max().y);
    }
    let goal_dist = haversine_m(from_lat, from_lon, goal_lat, goal_lon);
    bbox_intersection(
        hop_disc_bbox(from_lat, from_lon, hop_m),
        hop_disc_bbox(goal_lat, goal_lon, goal_dist),
    )
}

fn geometry_to_multi(geom: &Geometry<f64>) -> geo::MultiPolygon<f64> {
    match geom {
        Geometry::Polygon(p) => geo::MultiPolygon(vec![p.clone()]),
        Geometry::MultiPolygon(mp) => mp.clone(),
        Geometry::GeometryCollection(gc) => {
            let mut polys = Vec::new();
            for g in &gc.0 {
                polys.extend(geometry_to_multi(g).0);
            }
            geo::MultiPolygon(polys)
        }
        _ => geo::MultiPolygon(vec![]),
    }
}

fn geom_intersection(a: Geometry<f64>, b: &Geometry<f64>) -> Geometry<f64> {
    match catch_unwind(AssertUnwindSafe(|| geom_intersection_inner(a, b))) {
        Ok(g) => g,
        Err(_) => Geometry::Polygon(Polygon::new(LineString::empty(), vec![])),
    }
}

fn geom_intersection_inner(a: Geometry<f64>, b: &Geometry<f64>) -> Geometry<f64> {
    let ma = geometry_to_multi(&a);
    let mb = geometry_to_multi(b);
    if ma.is_empty() || mb.is_empty() {
        return Geometry::Polygon(Polygon::new(LineString::empty(), vec![]));
    }
    let mp = ma.intersection(&mb);
    if mp.0.is_empty() {
        Geometry::Polygon(Polygon::new(LineString::empty(), vec![]))
    } else if mp.0.len() == 1 {
        Geometry::Polygon(mp.0[0].clone())
    } else {
        Geometry::MultiPolygon(mp)
    }
}

fn bbox_polygon(west: f64, south: f64, east: f64, north: f64) -> Polygon<f64> {
    Rect::new(
        Coord { x: west, y: south },
        Coord { x: east, y: north },
    )
    .to_polygon()
}

/// Hop disc ∩ map viewport (UI trim hints only; peak scan uses progress-lens bbox).
fn seek_viewport_hop_region(
    from_lat: f64,
    from_lon: f64,
    radius_km: f64,
    west: f64,
    south: f64,
    east: f64,
    north: f64,
) -> Geometry<f64> {
    let hop_m = radius_km * 1000.0;
    let hop = Geometry::Polygon(hop_disc_wgs84(from_lat, from_lon, hop_m));
    let bbox = Geometry::Polygon(bbox_polygon(west, south, east, north));
    geom_intersection(hop, &bbox)
}

fn point_on_eligible(eligible: &Geometry<f64>, lat: f64, lon: f64) -> bool {
    if eligible.is_empty() {
        return false;
    }
    match catch_unwind(AssertUnwindSafe(|| {
        eligible.intersects(&Point::new(lon, lat))
    })) {
        Ok(ok) => ok,
        Err(_) => false,
    }
}

fn near_excluded(lat: f64, lon: f64, exclude: &[SeekPoint]) -> bool {
    exclude
        .iter()
        .any(|pt| haversine_m(lat, lon, pt.lat, pt.lon) <= SEEK_EXCLUDE_PROXIMITY_M)
}

/// Match seek ``from`` coords to a preset site so RF uses that site's antenna height.
fn resolve_seek_from_tx_height_m(preset: &Preset, from_lat: f64, from_lon: f64) -> f64 {
    const MATCH_M: f64 = 50.0;
    let mut best: Option<(f64, f64)> = None;
    for site in preset.sites.values() {
        let dist = haversine_m(from_lat, from_lon, site.loc[0], site.loc[1]);
        if dist <= MATCH_M {
            let h = resolved_site_tx_height_m(preset, site);
            match best {
                Some((best_dist, _)) if dist >= best_dist => {}
                _ => best = Some((dist, h)),
            }
        }
    }
    best.map(|(_, h)| h).unwrap_or_else(|| {
        preset_to_request(preset, from_lat, from_lon, None)
            .map(|req| req.tx_height)
            .unwrap_or(2.0)
    })
}

struct RfCandidate {
    lon: f64,
    lat: f64,
    elev_m: f64,
    candidate_id: String,
    is_goal: bool,
    is_site: bool,
    site_slug: Option<String>,
    site_name: Option<String>,
    completes_goal: bool,
}

fn collect_reachable_site_rows(
    preset: &Preset,
    land: &LandFilterIndex,
    from_lat: f64,
    from_lon: f64,
    goal_lat: f64,
    goal_lon: f64,
    hop_radius_m: f64,
    exclude: &[SeekPoint],
    exclude_slugs: &HashSet<String>,
) -> Vec<(String, String, f64, f64, f64)> {
    let rows: Vec<(String, String, f64, f64, f64)> = preset
        .sites
        .iter()
        .filter_map(|(slug, site)| {
            if exclude_slugs.contains(slug) {
                return None;
            }
            let lat = site.loc[0];
            let lon = site.loc[1];
            if !pair_within_hop_range(preset, from_lat, from_lon, lat, lon) {
                return None;
            }
            if !hop_makes_goal_progress(
                from_lat,
                from_lon,
                goal_lat,
                goal_lon,
                lat,
                lon,
                hop_radius_m,
            ) {
                return None;
            }
            if near_excluded(lat, lon, exclude) {
                return None;
            }
            if !land.contains(lon, lat) {
                return None;
            }
            if pair_within_hop_range(preset, from_lat, from_lon, goal_lat, goal_lon) {
                if !pair_within_hop_range(preset, goal_lat, goal_lon, lat, lon) {
                    return None;
                }
            }
            let elev = site.height_m.unwrap_or(0.0);
            Some((slug.clone(), site.name.clone(), lon, lat, elev))
        })
        .collect();
    rows
}

fn rank_seek_candidates(
    items: Vec<(f64, f64, f64)>,
    scores: Vec<SeekRankScore>,
    from_lat: f64,
    from_lon: f64,
    goal_lat: f64,
    goal_lon: f64,
) -> Vec<((f64, f64, f64), bool)> {
    let mut zipped: Vec<_> = items.into_iter().zip(scores).collect();
    zipped.sort_by(|a, b| {
        cmp_seek_peak_rank(
            from_lat,
            from_lon,
            goal_lat,
            goal_lon,
            a.0,
            b.0,
            a.1,
            b.1,
        )
    });
    zipped
        .into_iter()
        .map(|(peak, score)| (peak, score.completes))
        .collect()
}

fn batch_goal_margins(
    session: &Session,
    goal_lat: f64,
    goal_lon: f64,
    goal_tx_h: f64,
    hop_m: f64,
    endpoints: &[(f64, f64, f64)],
    rf_json: &str,
) -> Result<Vec<Option<f64>>, SeekRunError> {
    let mut margins = vec![None; endpoints.len()];
    let mut batch_idx = Vec::new();
    let mut batch_eps = Vec::new();
    for (i, &(lat, lon, h)) in endpoints.iter().enumerate() {
        if haversine_m(lat, lon, goal_lat, goal_lon) <= hop_m {
            batch_idx.push(i);
            batch_eps.push((lat, lon, h));
        }
    }
    if batch_eps.is_empty() {
        return Ok(margins);
    }
    let points: Vec<(f64, f64)> = std::iter::once((goal_lat, goal_lon))
        .chain(batch_eps.iter().map(|(lat, lon, _)| (*lat, *lon)))
        .collect();
    session
        .ensure_tiles_for_points(&points, hop_m)
        .map_err(|e| SeekRunError::User(e.to_string(), 503))?;
    let rf = session
        .seek_repeater_link_margins(goal_lat, goal_lon, goal_tx_h, &batch_eps, rf_json)
        .map_err(|e| SeekRunError::User(e.to_string(), 503))?;
    for (j, i) in batch_idx.iter().enumerate() {
        margins[*i] = rf.get(j).copied().flatten();
    }
    Ok(margins)
}

fn load_seek_candidates_body(
    session: &Session,
    verbose: bool,
    progress: &SeekProgressHub,
    req: &SeekRequest,
    scan_gen: u64,
) -> Result<Value, SeekRunError> {
    let scan_t0 = Instant::now();
    let mut ensure_active = || {
        if !progress.active(&req.slug, scan_gen) {
            return Err(SeekRunError::Cancelled);
        }
        Ok(())
    };

    let preset = load_preset(&req.preset_path)?;
    let seek_cfg = preset.seek.clone();

    let hop_km = match &preset.simulation.radius_km {
        serde_yaml::Value::Number(n) => n.as_f64().unwrap_or(50.0),
        serde_yaml::Value::String(s) => s.parse().unwrap_or(50.0),
        _ => 50.0,
    };
    let hop_m = hop_km * 1000.0;
    let goal_in_hop_range = pair_within_hop_range(
        &preset,
        req.from_lat,
        req.from_lon,
        req.goal_lat,
        req.goal_lon,
    );
    let clip_tuple = if goal_in_hop_range {
        landing_rf_lens_bbox(
            req.from_lat,
            req.from_lon,
            req.goal_lat,
            req.goal_lon,
            hop_m,
        )
    } else {
        hop_disc_bbox(req.from_lat, req.from_lon, hop_m)
    };
    let clip = LonLatBBox::from_tuple(clip_tuple).padded(0.05);

    ensure_active()?;
    progress.update(
        &req.slug,
        scan_gen,
        "eligible_land",
        0,
        0,
        "Building eligible land…",
    );
    let land_parts = load_or_build_eligible_land_parts(&req.preset_path, Some(clip)).map_err(|e| {
        match e.downcast_ref::<EligibleLandError>() {
            Some(el) => SeekRunError::User(el.0.clone(), 422),
            None => SeekRunError::User(e.to_string(), 422),
        }
    })?;
    let eligible_digest = land_parts.digest.clone();
    let land_index = land_parts.index();

    let goal_tx_h = default_repeater_tx_height_m(&preset);
    let tx_height = resolve_seek_from_tx_height_m(&preset, req.from_lat, req.from_lon);
    let rf_json = rf_json_for_preset(&preset).map_err(|e| SeekRunError::User(e.to_string(), 422))?;
    let mut goal_rf_from = false;
    if goal_in_hop_range {
        ensure_active()?;
        session
            .ensure_tiles_for_points(
                &[(req.from_lat, req.from_lon), (req.goal_lat, req.goal_lon)],
                hop_m,
            )
            .map_err(|e| SeekRunError::User(e.to_string(), 503))?;
        goal_rf_from = session
            .seek_repeater_link_batch(
                req.from_lat,
                req.from_lon,
                tx_height,
                &[(req.goal_lat, req.goal_lon, goal_tx_h)],
                &rf_json,
            )
            .map_err(|e| SeekRunError::User(e.to_string(), 503))?
            .first()
            .copied()
            .unwrap_or(false);
    }

    ensure_active()?;
    progress.update(
        &req.slug,
        scan_gen,
        "catalog",
        0,
        0,
        "Loading peaks catalog…",
    );
    let (mut filtered, n_catalog_peaks) = catalog_peaks_for_hop(
        &req.preset_path,
        req.from_lat,
        req.from_lon,
        req.goal_lat,
        req.goal_lon,
        hop_m,
        &req.exclude,
    )?;
    if goal_in_hop_range {
        filtered.retain(|(lon, lat, _)| {
            !peak_is_past_goal(
                req.from_lat,
                req.from_lon,
                req.goal_lat,
                req.goal_lon,
                *lat,
                *lon,
            )
        });
    }
    let n_peaks_in_lens = filtered.len();

    progress.update(
        &req.slug,
        scan_gen,
        "peak_links",
        filtered.len() as i32,
        n_catalog_peaks.max(1) as i32,
        &format!("Found {} catalog peak(s) in hop lens", filtered.len()),
    );

    let cap = seek_cfg.max_candidates as usize;
    let mut capped: Vec<(f64, f64, f64)> = Vec::new();
    let mut peak_completes_flags: Vec<bool> = Vec::new();
    let mut n_peak_rf_viable_in_lens = 0usize;

    let goal_on_eligible = goal_in_hop_range && land_index.contains(req.goal_lon, req.goal_lat);
    let goal_near_prior_hop =
        goal_in_hop_range && near_excluded(req.goal_lat, req.goal_lon, &req.exclude);
    let goal_hop_eligible = goal_in_hop_range && goal_on_eligible && !goal_near_prior_hop;
    let goal_finish_eligible = goal_in_hop_range;

    let mut site_rows = collect_reachable_site_rows(
        &preset,
        &land_index,
        req.from_lat,
        req.from_lon,
        req.goal_lat,
        req.goal_lon,
        hop_m,
        &req.exclude,
        &req.exclude_slugs,
    );
    if goal_in_hop_range {
        site_rows.retain(|(_, _, lon, lat, _)| {
            !peak_is_past_goal(
                req.from_lat,
                req.from_lon,
                req.goal_lat,
                req.goal_lon,
                *lat,
                *lon,
            )
        });
    }

    if !filtered.is_empty() {
        ensure_active()?;
        progress.update(
            &req.slug,
            scan_gen,
            "rf",
            0,
            filtered.len().max(1) as i32,
            &format!("Checking RF on {} candidate(s)…", filtered.len()),
        );
        let points: Vec<(f64, f64)> = std::iter::once((req.from_lat, req.from_lon))
            .chain(filtered.iter().map(|(lon, lat, _)| (*lat, *lon)))
            .collect();
        session
            .ensure_tiles_for_points(&points, hop_km * 1000.0)
            .map_err(|e| SeekRunError::User(e.to_string(), 503))?;
        let pairs: Vec<(f64, f64, f64)> = filtered
            .iter()
            .map(|(lon, lat, _)| (*lat, *lon, goal_tx_h))
            .collect();
        const RF_CHUNK: usize = 512;
        let mut from_margins: Vec<Option<f64>> = Vec::with_capacity(filtered.len());
        for chunk in pairs.chunks(RF_CHUNK) {
            ensure_active()?;
            from_margins.extend(
                session
                    .seek_repeater_link_margins(
                        req.from_lat,
                        req.from_lon,
                        tx_height,
                        chunk,
                        &rf_json,
                    )
                    .map_err(|e| SeekRunError::User(e.to_string(), 503))?,
            );
        }
        n_peak_rf_viable_in_lens = from_margins.iter().filter(|v| v.is_some()).count();
        let mut viable: Vec<(f64, f64, f64)> = Vec::new();
        let mut viable_from_margin: Vec<f64> = Vec::new();
        for (peak, margin) in filtered.iter().copied().zip(from_margins) {
            let Some(from_db) = margin else {
                continue;
            };
            if goal_in_hop_range
                && peak_is_past_goal(
                    req.from_lat,
                    req.from_lon,
                    req.goal_lat,
                    req.goal_lon,
                    peak.1,
                    peak.0,
                )
            {
                continue;
            }
            viable.push(peak);
            viable_from_margin.push(from_db);
        }
        let mut peak_eps: Vec<(f64, f64, f64)> = viable
            .iter()
            .map(|(lon, lat, _)| (*lat, *lon, goal_tx_h))
            .collect();
        ensure_active()?;
        progress.update(
            &req.slug,
            scan_gen,
            "rf_goal",
            0,
            viable.len().max(1) as i32,
            "Checking which candidates reach the goal…",
        );
        let goal_margins = batch_goal_margins(
            session,
            req.goal_lat,
            req.goal_lon,
            goal_tx_h,
            hop_m,
            &peak_eps,
            &rf_json,
        )?;
        peak_eps = viable
            .iter()
            .map(|(lon, lat, _)| (*lat, *lon, goal_tx_h))
            .collect();
        let mesh = local_mesh_neighbors(
            &preset,
            req.from_lat,
            req.from_lon,
            req.goal_lat,
            req.goal_lon,
            &req.exclude,
            &req.exclude_slugs,
        );
        progress.update(
            &req.slug,
            scan_gen,
            "rf_mesh",
            0,
            viable.len().max(1) as i32,
            "Scoring extra links into the local mesh…",
        );
        let mesh_links = count_candidate_mesh_links(
            session,
            &mesh,
            &peak_eps,
            hop_m,
            &rf_json,
            &mut ensure_active,
        )?;
        let scores: Vec<SeekRankScore> = viable
            .iter()
            .enumerate()
            .map(|(i, _)| {
                let goal_m = goal_margins.get(i).copied().flatten();
                let from_m = viable_from_margin[i];
                SeekRankScore {
                    completes: goal_m.is_some(),
                    mesh_links: mesh_links.get(i).copied().unwrap_or(0),
                    margin_db: match goal_m {
                        Some(g) => from_m.min(g),
                        None => from_m,
                    },
                }
            })
            .collect();
        let mut ranked = rank_seek_candidates(
            viable,
            scores,
            req.from_lat,
            req.from_lon,
            req.goal_lat,
            req.goal_lon,
        );
        if goal_in_hop_range && ranked.iter().any(|(_, completes)| *completes) {
            ranked.retain(|(_, completes)| *completes);
        }
        let taken = take_spatially_diverse(ranked, SEEK_CANDIDATE_SEP_M, cap);
        capped = taken.iter().map(|(peak, _)| *peak).collect();
        peak_completes_flags = taken.iter().map(|(_, completes)| *completes).collect();
    }

    let site_eps: Vec<(f64, f64, f64)> = site_rows
        .iter()
        .map(|(slug, _, lon, lat, _)| {
            let tx_h = preset
                .sites
                .get(slug)
                .map(|site| resolved_site_tx_height_m(&preset, site))
                .unwrap_or(goal_tx_h);
            (*lat, *lon, tx_h)
        })
        .collect();
    let site_goal_margins = if site_eps.is_empty() {
        Vec::new()
    } else {
        ensure_active()?;
        batch_goal_margins(
            session,
            req.goal_lat,
            req.goal_lon,
            goal_tx_h,
            hop_m,
            &site_eps,
            &rf_json,
        )?
    };
    let mut site_completes: Vec<bool> = site_goal_margins.iter().map(|m| m.is_some()).collect();
    if !site_rows.is_empty() {
        let mut order: Vec<usize> = (0..site_rows.len()).collect();
        order.sort_by(|&i, &j| {
            let a = &site_rows[i];
            let b = &site_rows[j];
            cmp_seek_peak_rank(
                req.from_lat,
                req.from_lon,
                req.goal_lat,
                req.goal_lon,
                (a.2, a.3, a.4),
                (b.2, b.3, b.4),
                SeekRankScore {
                    completes: site_completes.get(i).copied().unwrap_or(false),
                    mesh_links: 0,
                    margin_db: site_goal_margins.get(i).copied().flatten().unwrap_or(0.0),
                },
                SeekRankScore {
                    completes: site_completes.get(j).copied().unwrap_or(false),
                    mesh_links: 0,
                    margin_db: site_goal_margins.get(j).copied().flatten().unwrap_or(0.0),
                },
            )
        });
        site_rows = order.iter().map(|&i| site_rows[i].clone()).collect();
        site_completes = order
            .iter()
            .map(|&i| site_completes.get(i).copied().unwrap_or(false))
            .collect();
        if goal_in_hop_range && site_completes.iter().any(|c| *c) {
            let keep: Vec<bool> = site_completes.clone();
            let mut next_rows = Vec::new();
            let mut next_flags = Vec::new();
            for (i, row) in site_rows.into_iter().enumerate() {
                if keep.get(i).copied().unwrap_or(false) {
                    next_rows.push(row);
                    next_flags.push(true);
                }
            }
            site_rows = next_rows;
            site_completes = next_flags;
        }
    }

    let mut peak_pairs: Vec<((f64, f64, f64), bool)> = capped
        .into_iter()
        .zip(peak_completes_flags)
        .collect();
    peak_pairs.retain(|((lon, lat, _), _)| {
        !site_rows.iter().any(|(_, _, site_lon, site_lat, _)| {
            haversine_m(*lat, *lon, *site_lat, *site_lon) <= SEEK_SITE_PEAK_DEDUP_M
        })
    });
    if goal_finish_eligible {
        peak_pairs.retain(|((lon, lat, _), _)| {
            haversine_m(req.goal_lat, req.goal_lon, *lat, *lon) > SEEK_GOAL_DEDUP_M
        });
        peak_pairs.truncate(cap);
    }
    capped = peak_pairs.iter().map(|(peak, _)| *peak).collect();
    peak_completes_flags = peak_pairs.iter().map(|(_, completes)| *completes).collect();
    let peak_rf_viable_flags = vec![true; capped.len()];

    let goal_elev = req.goal_elev_m.unwrap_or(0.0);
    let goal_row = if goal_finish_eligible {
        Some((req.goal_lon, req.goal_lat, goal_elev))
    } else {
        None
    };

    let mut rf_candidates: Vec<RfCandidate> = Vec::new();
    if let Some((lon, lat, elev_m)) = goal_row {
        rf_candidates.push(RfCandidate {
            lon,
            lat,
            elev_m,
            candidate_id: "goal".into(),
            is_goal: true,
            is_site: false,
            site_slug: None,
            site_name: None,
            completes_goal: goal_rf_from,
        });
    }
    for (idx, (slug, name, lon, lat, elev_m)) in site_rows.iter().enumerate() {
        rf_candidates.push(RfCandidate {
            lon: *lon,
            lat: *lat,
            elev_m: *elev_m,
            candidate_id: format!("site:{slug}"),
            is_goal: false,
            is_site: true,
            site_slug: Some(slug.clone()),
            site_name: Some(name.clone()),
            completes_goal: site_completes.get(idx).copied().unwrap_or(false),
        });
    }
    let peak_count = capped.len();
    for (idx, (lon, lat, elev_m)) in capped.into_iter().enumerate() {
        rf_candidates.push(RfCandidate {
            lon,
            lat,
            elev_m,
            candidate_id: format!("c{idx}"),
            is_goal: false,
            is_site: false,
            site_slug: None,
            site_name: None,
            completes_goal: peak_completes_flags.get(idx).copied().unwrap_or(false),
        });
    }

    let peak_candidate_start = rf_candidates.len().saturating_sub(peak_count);
    let mut rf_viable: Vec<bool> = Vec::new();
    if peak_candidate_start > 0 {
        ensure_active()?;
        let site_endpoints: Vec<(f64, f64, f64)> = rf_candidates[..peak_candidate_start]
            .iter()
            .map(|row| {
                let tx_h = row
                    .site_slug
                    .as_ref()
                    .and_then(|slug| preset.sites.get(slug))
                    .map(|site| resolved_site_tx_height_m(&preset, site))
                    .unwrap_or(default_repeater_tx_height_m(&preset));
                (row.lat, row.lon, tx_h)
            })
            .collect();
        let points: Vec<(f64, f64)> = std::iter::once((req.from_lat, req.from_lon))
            .chain(site_endpoints.iter().map(|(lat, lon, _)| (*lat, *lon)))
            .collect();
        session
            .ensure_tiles_for_points(&points, hop_km * 1000.0)
            .map_err(|e| SeekRunError::User(e.to_string(), 503))?;
        rf_viable = session
            .seek_repeater_link_batch(
                req.from_lat,
                req.from_lon,
                tx_height,
                &site_endpoints,
                &rf_json,
            )
            .map_err(|e| SeekRunError::User(e.to_string(), 503))?;
    }
    rf_viable.extend(peak_rf_viable_flags);
    let n_peak_rf_viable = n_peak_rf_viable_in_lens.min(peak_count);

    let mut candidate_features = Vec::new();
    let mut line_features = Vec::new();
    let mut site_candidate_slugs = Vec::new();
    let mut goal_rf_viable = false;

    for (idx, row) in rf_candidates.iter().enumerate() {
        let viable = rf_viable.get(idx).copied().unwrap_or(false);
        if row.is_goal {
            goal_rf_viable = viable;
            if !viable {
                continue;
            }
        }
        if row.is_site && !viable {
            continue;
        }
        let dist_km = haversine_m(req.from_lat, req.from_lon, row.lat, row.lon) / 1000.0;
        let bearing = bearing_deg(req.from_lat, req.from_lon, row.lat, row.lon);
        let forward_km =
            forward_reach_m(
                req.from_lat,
                req.from_lon,
                req.goal_lat,
                req.goal_lon,
                row.lat,
                row.lon,
            ) / 1000.0;
        let goal_bearing = bearing_deg(req.from_lat, req.from_lon, req.goal_lat, req.goal_lon);
        let bearing_delta = angle_diff_deg(bearing, goal_bearing);
        let mut props = json!({
            "candidate_id": row.candidate_id,
            "lat": row.lat,
            "lon": row.lon,
            "elev_m": (row.elev_m * 10.0).round() / 10.0,
            "distance_km": (dist_km * 10.0).round() / 10.0,
            "bearing_deg": bearing.round(),
            "forward_reach_km": (forward_km * 10.0).round() / 10.0,
            "bearing_delta_deg": bearing_delta.round(),
            "rf_viable": viable,
            "completes_goal": row.completes_goal,
            "goal_path_exists": true,
            "goal_distance_km": (haversine_m(row.lat, row.lon, req.goal_lat, req.goal_lon) / 1000.0 * 10.0).round() / 10.0,
        });
        if row.is_goal {
            props["is_goal"] = json!(true);
        }
        if row.is_site {
            props["is_site"] = json!(true);
            if let Some(slug) = &row.site_slug {
                props["site_slug"] = json!(slug);
                site_candidate_slugs.push(slug.clone());
            }
            if let Some(name) = &row.site_name {
                props["site_name"] = json!(name);
            }
        }
        candidate_features.push(json!({
            "type": "Feature",
            "geometry": { "type": "Point", "coordinates": [row.lon, row.lat] },
            "properties": props,
        }));
        if row.is_goal {
            continue;
        }
        let mut line_props = json!({
            "candidate_id": row.candidate_id,
            "distance_km": (dist_km * 10.0).round() / 10.0,
            "bearing_deg": bearing.round(),
            "rf_viable": viable,
            "completes_goal": row.completes_goal,
            "elev_m": (row.elev_m * 10.0).round() / 10.0,
        });
        if row.is_goal {
            line_props["is_goal"] = json!(true);
        }
        if row.is_site {
            line_props["is_site"] = json!(true);
            if let Some(slug) = &row.site_slug {
                line_props["site_slug"] = json!(slug);
            }
            if let Some(name) = &row.site_name {
                line_props["site_name"] = json!(name);
            }
        }
        line_features.push(json!({
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [[req.from_lon, req.from_lat], [row.lon, row.lat]],
            },
            "properties": line_props,
        }));
    }

    let goal_line_dist = haversine_m(req.from_lat, req.from_lon, req.goal_lat, req.goal_lon) / 1000.0;
    let goal_line_bearing = bearing_deg(req.from_lat, req.from_lon, req.goal_lat, req.goal_lon);

    let n_peak_candidates = candidate_features
        .iter()
        .filter(|f| {
            let p = &f["properties"];
            !p["is_goal"].as_bool().unwrap_or(false) && !p["is_site"].as_bool().unwrap_or(false)
        })
        .count();

    let _ = verbose;
    Ok(json!({
        "from": { "lat": req.from_lat, "lon": req.from_lon },
        "goal": { "lat": req.goal_lat, "lon": req.goal_lon },
        "candidates": { "type": "FeatureCollection", "features": candidate_features },
        "lines": { "type": "FeatureCollection", "features": line_features },
        "goal_line": {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [[req.from_lon, req.from_lat], [req.goal_lon, req.goal_lat]],
            },
            "properties": {
                "distance_km": (goal_line_dist * 10.0).round() / 10.0,
                "bearing_deg": goal_line_bearing.round(),
                "kind": "goal",
            },
        },
        "meta": {
            "n_peaks_linkable": n_peak_rf_viable,
            "n_catalog_peaks": n_catalog_peaks,
            "n_peaks_in_lens": n_peaks_in_lens,
            "n_candidates": n_peak_candidates,
            "n_site_candidates": site_candidate_slugs.len(),
            "site_candidate_slugs": site_candidate_slugs,
            "eligible_digest": eligible_digest,
            "goal_in_hop_range": goal_in_hop_range,
            "goal_on_eligible": goal_on_eligible,
            "goal_near_prior_hop": goal_near_prior_hop,
            "goal_hop_eligible": goal_hop_eligible,
            "goal_finish_eligible": goal_finish_eligible,
            "goal_reachable": goal_hop_eligible,
            "goal_rf_viable": goal_rf_viable,
            "goal_distance_km": (goal_line_dist * 10.0).round() / 10.0,
            "hop_range_km": hop_km,
            "scan_ms": scan_t0.elapsed().as_millis(),
        },
    }))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{Duration, Instant};

    /// Seek hot paths must stay sub-second on simple geometry; regressions here hung for minutes in prod.
    const SEEK_TRIM_BUDGET: Duration = Duration::from_millis(500);

    #[test]
    fn viewport_hop_region_is_fast_and_nonempty() {
        let t0 = Instant::now();
        let region = seek_viewport_hop_region(
            38.38638860397961,
            -114.80749905109406,
            72.0,
            -115.5,
            37.5,
            -114.0,
            38.8,
        );
        assert!(t0.elapsed() < SEEK_TRIM_BUDGET, "trim took {:?}", t0.elapsed());
        assert!(!region.is_empty());
    }

    #[test]
    fn viewport_hop_region_empty_when_bbox_misses_hop() {
        let region = seek_viewport_hop_region(
            38.0,
            -114.0,
            10.0,
            -120.0,
            35.0,
            -119.0,
            36.0,
        );
        assert!(region.is_empty());
    }

    #[test]
    fn lens_accepts_sideways_peak_when_closer_to_goal() {
        let from_lat = 38.0;
        let from_lon = -117.0;
        let goal_lat = 40.0;
        let goal_lon = -117.0;
        let hop_m = 72_000.0;
        assert!(peak_in_progress_lens(
            from_lat,
            from_lon,
            goal_lat,
            goal_lon,
            from_lat + 0.4,
            from_lon,
            hop_m,
        ));
        assert!(!peak_in_progress_lens(
            from_lat,
            from_lon,
            goal_lat,
            goal_lon,
            from_lat - 0.4,
            from_lon,
            hop_m,
        ));
    }

    #[test]
    fn lens_rejects_backward_peak() {
        let from_lat = 39.75567;
        let from_lon = -119.46126;
        let goal_lat = 39.778464;
        let goal_lon = -119.049911;
        let hop_m = 72_000.0;
        let behind = (-119.65, 39.75567, 1708.0);
        let out = filter_peaks_in_progress_lens(
            vec![behind],
            from_lat,
            from_lon,
            goal_lat,
            goal_lon,
            hop_m,
        );
        assert!(out.is_empty(), "backward peak must not survive progress lens");
    }

    #[test]
    fn progress_lens_scan_bbox_covers_intersection() {
        let from_lat = 39.75567;
        let from_lon = -119.46126;
        let goal_lat = 39.778464;
        let goal_lon = -119.049911;
        let hop_m = 72_000.0;
        let bbox = seek_progress_lens_scan_bbox(from_lat, from_lon, goal_lat, goal_lon, hop_m);
        let lens = progress_lens_polygon(from_lat, from_lon, goal_lat, goal_lon, hop_m);
        let rect = lens.bounding_rect().expect("lens bbox");
        assert!(rect.min().x >= bbox.0 - 1e-6);
        assert!(rect.min().y >= bbox.1 - 1e-6);
        assert!(rect.max().x <= bbox.2 + 1e-6);
        assert!(rect.max().y <= bbox.3 + 1e-6);
    }

    #[test]
    fn spatial_diverse_keeps_spread_completers() {
        let a = ((-119.17, 39.77, 1600.0), true);
        let near_a = ((-119.172, 39.771, 1610.0), true);
        let b = ((-119.10, 39.78, 1500.0), true);
        let taken = take_spatially_diverse(vec![a, near_a, b], 800.0, 8);
        assert_eq!(taken.len(), 2);
        assert!((taken[0].0.0 - a.0.0).abs() < 1e-6);
        assert!((taken[1].0.0 - b.0.0).abs() < 1e-6);
    }

    #[test]
    fn point_on_eligible_simple_polygon() {
        let poly = Polygon::new(
            LineString::from(vec![
                Coord { x: -115.0, y: 38.0 },
                Coord { x: -114.0, y: 38.0 },
                Coord { x: -114.0, y: 39.0 },
                Coord { x: -115.0, y: 39.0 },
                Coord { x: -115.0, y: 38.0 },
            ]),
            vec![],
        );
        let geom = Geometry::Polygon(poly);
        assert!(point_on_eligible(&geom, 38.5, -114.5));
        assert!(!point_on_eligible(&geom, 37.0, -114.5));
    }

    /// Live Skadi mirror required: `PEAKY_HOME=ops/peaky_home cargo test -p peaky-serve schader_ridge_rf -- --ignored --nocapture`
    #[test]
    #[ignore]
    fn schader_ridge_rf() {
        use peaky_preset::paths::resolved_skadi_mirror_dir;
        use splatter::propagate::link_context_from_json;
        use splatter::ray_cache::destination_point;

        let preset_path = peaky_preset::resolve_preset_path("nevada");
        let preset = load_preset(&preset_path).expect("nevada preset");
        let rf_json = rf_json_for_preset(&preset).expect("rf json");
        let session = Session::new(resolved_skadi_mirror_dir(), false, 4);
        let from_lat = 36.4625;
        let from_lon = -116.06;
        let hop_m = 72_000.0;
        session
            .ensure_tiles_for_hop_disc(from_lat, from_lon, hop_m)
            .expect("hop disc tiles");
        let tx_h = resolve_seek_from_tx_height_m(&preset, from_lat, from_lon);
        let rx_h = default_repeater_tx_height_m(&preset);
        let base = link_context_from_json(&rf_json).expect("ctx");

        let mesh_neighbor = preset.sites.get("eip-us1051467").expect("neighbor");
        let nlat = mesh_neighbor.loc[0];
        let nlon = mesh_neighbor.loc[1];
        let nviable = session
            .seek_repeater_link_batch(from_lat, from_lon, tx_h, &[(nlat, nlon, rx_h)], &rf_json)
            .expect("neighbor batch")[0];
        eprintln!("schader→eip-us1051467 (39km mesh strong): viable={nviable}");

        for (bearing_deg, dist_km) in [(297.0_f64, 68.0), (306.0, 67.6), (292.0, 70.0)] {
            let (lat, lon) =
                destination_point(from_lat, from_lon, bearing_deg.to_radians(), dist_km * 1000.0);
            let viable = session
                .seek_repeater_link_batch(from_lat, from_lon, tx_h, &[(lat, lon, rx_h)], &rf_json)
                .expect("batch")[0];
            let ab = session
                .link_eval(from_lat, from_lon, lat, lon, &rf_json)
                .expect("ab");
            eprintln!(
                "schader→{dist_km}km {bearing_deg}° viable={viable} ab={} pr={:.1} excess={:.1}",
                ab.viable, ab.pr_dbm, ab.excess_loss_db
            );
        }
        eprintln!("threshold={:.1} tx_h={tx_h}", base.threshold_dbm);
    }

    /// Live Skadi mirror required: `PEAKY_HOME=ops/peaky_home cargo test -p peaky-serve rockland_singatse_rf -- --ignored --nocapture`
    #[test]
    #[ignore]
    fn rockland_singatse_rf() {
        use peaky_preset::paths::resolved_skadi_mirror_dir;
        use splatter::propagate::link_context_from_json;

        let preset_path = peaky_preset::resolve_preset_path("nevada");
        let preset = load_preset(&preset_path).expect("nevada preset");
        let rf_json = rf_json_for_preset(&preset).expect("rf json");
        let session = Session::new(resolved_skadi_mirror_dir(), false, 4);
        let from_lat = 38.648998;
        let from_lon = -119.100998;
        let sing_lat = 38.986095;
        let sing_lon = -119.244278;
        let hop_m = 72_000.0;
        session
            .ensure_tiles_for_hop_disc(from_lat, from_lon, hop_m)
            .expect("hop disc tiles");
        let tx_h = resolve_seek_from_tx_height_m(&preset, from_lat, from_lon);
        let rx_h = default_repeater_tx_height_m(&preset);
        let viable = session
            .seek_repeater_link_batch(
                from_lat,
                from_lon,
                tx_h,
                &[(sing_lat, sing_lon, rx_h)],
                &rf_json,
            )
            .expect("batch");
        let base = link_context_from_json(&rf_json).expect("ctx");
        let ab = session
            .link_eval(from_lat, from_lon, sing_lat, sing_lon, &rf_json)
            .expect("ab");
        let ba = session
            .link_eval(sing_lat, sing_lon, from_lat, from_lon, &rf_json)
            .expect("ba");
        eprintln!(
            "rockland→singatse batch_viable={} ab={:?} ba={:?} tx_h={tx_h} rx_h={rx_h} threshold={:.1}",
            viable[0], ab.viable, ba.viable, base.threshold_dbm
        );
    }

    mod seek_e2e {
        use super::*;
        use std::path::{Path, PathBuf};
        use std::sync::Arc;
        use std::thread;
        use std::time::{Duration, Instant};

        use splatter::dem::{DemMosaic, DemTile};
        use splatter::Session;
        use tempfile::TempDir;

        const SEEK_BUDGET: Duration = Duration::from_secs(10);

        fn fixtures_home() -> PathBuf {
            PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../tests/fixtures/peaky_home")
        }

        fn write_seek_fixture_project(home: &Path) -> PathBuf {
            let slug = "seek-sample";
            let project_dir = home.join("projects").join(slug);
            std::fs::create_dir_all(project_dir.join("data")).expect("data dir");
            let config = r#"modem_presets:
  fixture-modem:
    frequency_mhz: 915.0
    bandwidth_khz: 125.0
    spreading_factor: 10
    coding_rate: 5
    implementation_margin_db: 3.0
    power_dbm: 22.0
    sensitivity_dbm: -132.0
environment_presets:
  fixture-desert:
    climate: desert
    polarization: vertical
    clutter_height_m: 1.0
    fresnel_clearance_fraction: 0.25
    coverage_pessimism_db: 0.0
    situation_pct: 95.0
    time_pct: 95.0
simulation:
  radius_km: 50.0
  modem: fixture-modem
  environment: fixture-desert
  transmitter: {height_m: 2.0, gain_dbi: 3.0, loss_db: 2.0}
  receiver: {height_m: 2.0, gain_dbi: 3.0, loss_db: 2.0}
seek:
  peak_bin_size_m: 1500
  max_candidates: 4
sites:
  start:
    name: Start
    loc: [40.0, -119.5]
  goal:
    name: Goal
    loc: [40.25, -119.25]
  relay:
    name: Relay
    loc: [40.1, -119.4]
links: []
land:
  sources:
    test:
      path: data/eligible.geojson
      layers:
        - name: public
          role: include
"#;
            std::fs::write(project_dir.join("config.yaml"), config).expect("config");
            let geojson = r#"{"type":"FeatureCollection","features":[{"type":"Feature","geometry":{"type":"Polygon","coordinates":[[[-120,39],[-119,39],[-119,41],[-120,41],[-120,39]]]},"properties":{"name":"public"}}]}"#;
            std::fs::write(project_dir.join("data/eligible.geojson"), geojson).expect("geojson");
            let config_path = project_dir.join("config.yaml");
            let mut entries = std::collections::HashMap::new();
            entries.insert(
                "relay-peak".into(),
                peaky_preset::PeakCatalogEntry {
                    name: None,
                    loc: [40.12, -119.38],
                    elev_m: Some(2500.0),
                    source: "test".into(),
                    compute_key: None,
                    road_m: Some(100.0),
                    road_loc: Some([40.11, -119.39]),
                    hike_m: Some(200.0),
                    max_slope_deg: Some(10.0),
                    hike: None,
                    paved_loc: None,
                    jeep_m: None,
                    jeep: None,
                    hike_difficulty: None,
                    jeep_difficulty: None,
                    deny: None,
                },
            );
            peaky_preset::write_peaks_catalog(
                &config_path,
                &peaky_preset::PeaksCatalog {
                    generated_at: "2026-09-10".into(),
                    rules: peaky_preset::PeakAccessRules {
                        max_hike_m: 805.0,
                        max_slope_deg: 35.0,
                        road_highways: vec!["track".into()],
                        ..Default::default()
                    },
                    entries,
                },
            )
            .expect("peaks");
            config_path
        }

        fn flat_tile(sw_lat: i32, sw_lon: i32, n: usize, spike: bool) -> DemTile {
            let mut elevations = vec![2100i16; n * n];
            if spike {
                elevations[(n / 2) * n + (n / 2)] = 2800;
            }
            DemTile {
                sw_lat: sw_lat as f64,
                sw_lon: sw_lon as f64,
                n,
                elevations,
            }
        }

        fn seek_test_dem(n: usize) -> DemMosaic {
            use std::collections::HashMap;
            let mut tiles = HashMap::new();
            for sw_lat in 39..=41 {
                for sw_lon in -121..=-119 {
                    let spike = sw_lat == 39 && sw_lon == -120;
                    tiles.insert(
                        (sw_lat, sw_lon),
                        flat_tile(sw_lat, sw_lon, n, spike),
                    );
                }
            }
            DemMosaic::from_tiles(tiles)
        }

        fn seek_sample_request(preset_path: &Path) -> SeekRequest {
            SeekRequest {
                slug: "seek-sample".into(),
                preset_path: preset_path.to_path_buf(),
                from_lat: 40.0,
                from_lon: -119.5,
                goal_lat: 40.25,
                goal_lon: -119.25,
                west: -120.0,
                south: 39.0,
                east: -119.0,
                north: 41.0,
                exclude: Vec::new(),
                exclude_slugs: ["start".into(), "goal".into()].into_iter().collect(),
                goal_elev_m: None,
                peak_bin_size_m: Some(750.0),
            }
        }

        fn poll_seek_until_done(hub: &SeekHub, slug: &str, budget: Duration) -> Value {
            let deadline = Instant::now() + budget;
            loop {
                let body = hub.poll(slug);
                match body["status"].as_str() {
                    Some("done") => return body,
                    Some("error") => {
                        panic!("seek error: {}", body["error"].as_str().unwrap_or("?"))
                    }
                    Some("cancelled") => panic!("seek cancelled"),
                    _ => {}
                }
                if Instant::now() >= deadline {
                    panic!(
                        "seek timed out after {:?}; last poll={body}",
                        budget,
                        body = body
                    );
                }
                thread::sleep(Duration::from_millis(20));
            }
        }

        #[test]
        fn seek_hub_completes_within_ten_seconds() {
            let tmp = TempDir::new().expect("tempdir");
            let home = tmp.path();
            unsafe {
                std::env::set_var("PEAKY_HOME", home);
            }
            let preset_path = write_seek_fixture_project(home);
            let mirror = home.join("mirror");
            std::fs::create_dir_all(&mirror).expect("mirror");
            let session = Arc::new(Session::new(mirror, false, 4));
            session.install_dem_mosaic(seek_test_dem(3601));
            let hub = SeekHub::new(session, false);
            let req = seek_sample_request(&preset_path);
            hub.enqueue(req, false).expect("enqueue");
            let done = poll_seek_until_done(&hub, "seek-sample", SEEK_BUDGET);
            let result = done["result"].as_object().expect("result");
            assert!(result["meta"]["n_catalog_peaks"].as_u64().unwrap() >= 1);
            assert!(result["meta"]["scan_ms"].as_u64().unwrap() < SEEK_BUDGET.as_millis() as u64);
            assert!(
                result["meta"]["goal_finish_eligible"].as_bool().unwrap_or(false),
                "goal should be within hop range"
            );
            if result["meta"]["goal_in_hop_range"].as_bool().unwrap_or(false) {
                let from_lat = result["from"]["lat"].as_f64().unwrap();
                let from_lon = result["from"]["lon"].as_f64().unwrap();
                let goal_lat = result["goal"]["lat"].as_f64().unwrap();
                let goal_lon = result["goal"]["lon"].as_f64().unwrap();
                for feature in result["candidates"]["features"].as_array().unwrap() {
                    let props = &feature["properties"];
                    if props["is_goal"].as_bool().unwrap_or(false) {
                        continue;
                    }
                    let lat = props["lat"].as_f64().unwrap();
                    let lon = props["lon"].as_f64().unwrap();
                    assert!(
                        !peak_is_past_goal(from_lat, from_lon, goal_lat, goal_lon, lat, lon),
                        "in-range goal should not return overshooting candidate {props}"
                    );
                }
            }
        }

        #[tokio::test]
        async fn api_seek_polls_to_done_within_ten_seconds() {
            use axum::body::Body;
            use axum::http::{Request, StatusCode};
            use tower::ServiceExt;

            use crate::alternates::AlternatesHub;
            use crate::app::router;
            use crate::events::ServeEventHub;
            use crate::state::AppState;
            use crate::warm::WarmHub;
            use tokio::sync::Semaphore;

            let tmp = TempDir::new().expect("tempdir");
            let home = tmp.path();
            unsafe {
                std::env::set_var("PEAKY_HOME", home);
            }
            let preset_path = write_seek_fixture_project(home);
            let mirror = home.join("mirror");
            std::fs::create_dir_all(&mirror).expect("mirror");
            let session = Arc::new(Session::new(mirror, false, 4));
            session.install_dem_mosaic(seek_test_dem(3601));
            let events = ServeEventHub::default();
            let seek = SeekHub::new(Arc::clone(&session), false);
            let alternates = AlternatesHub::new(Arc::clone(&session), false);
            let fortify = crate::fortify::FortifyHub::new(Arc::clone(&session), false);
            let state = AppState {
                session: Arc::clone(&session),
                events: events.clone(),
                warm: WarmHub::new(session, events, false, 2),
                seek,
                alternates,
                fortify,
                verbose: false,
                dem_tile_render: Arc::new(Semaphore::new(crate::state::DEM_TILE_RENDER_PERMITS)),
                project_dir: preset_path.parent().unwrap().to_path_buf(),
                slug: "seek-sample".into(),
            };
            let app = router(state);

            let qs = "from_lat=40.0&from_lon=-119.5&goal_lat=40.25&goal_lon=-119.25\
                &bbox=-120,39,-119,41&exclude_slugs=start;goal";
            let kickoff = app
                .clone()
                .oneshot(
                    Request::builder()
                        .uri(format!("/api/p/seek-sample/seek/candidates?{qs}"))
                        .body(Body::empty())
                        .unwrap(),
                )
                .await
                .unwrap();
            assert_eq!(kickoff.status(), StatusCode::ACCEPTED);

            let deadline = Instant::now() + SEEK_BUDGET;
            loop {
                let resp = app
                    .clone()
                    .oneshot(
                        Request::builder()
                            .uri("/api/p/seek-sample/seek/scan-progress")
                            .body(Body::empty())
                            .unwrap(),
                    )
                    .await
                    .unwrap();
                assert_eq!(resp.status(), StatusCode::OK);
                let bytes = axum::body::to_bytes(resp.into_body(), usize::MAX)
                    .await
                    .unwrap();
                let body: Value = serde_json::from_slice(&bytes).unwrap();
                if body["status"] == "done" {
                    assert!(
                        body["result"]["meta"]["n_catalog_peaks"]
                            .as_u64()
                            .unwrap()
                            >= 1
                    );
                    assert!(
                        body["result"]["meta"]["scan_ms"]
                            .as_u64()
                            .unwrap()
                            < SEEK_BUDGET.as_millis() as u64
                    );
                    assert!(
                        body["result"]["meta"]["goal_finish_eligible"]
                            .as_bool()
                            .unwrap_or(false)
                    );
                    return;
                }
                if body["status"] == "error" {
                    panic!("seek API error: {}", body["error"]);
                }
                if Instant::now() >= deadline {
                    panic!("API seek timed out; last={body}");
                }
                tokio::time::sleep(Duration::from_millis(20)).await;
            }
        }
    }
}
