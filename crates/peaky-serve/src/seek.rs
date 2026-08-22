//! Goal-seek candidate peak scan (P2P RF via splatter).

use std::collections::{HashMap, HashSet};
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::path::PathBuf;
use std::sync::{Arc, Condvar, Mutex};
use std::thread;
use std::time::Instant;

use anyhow::Result;
use geo::{BooleanOps, BoundingRect, Coord, Geometry, HasDimensions, Intersects, LineString, Point, Polygon, Rect};
use peaky_geo::{eligible_land_dem_mask_dir, load_or_build_eligible_land_union, EligibleLandError};
use peaky_preset::{load_preset, Preset, SeekConfig};
use serde_json::{json, Value};
use splatter::peaks::LandFilterIndex;
use splatter::Session;

use crate::rf::{pair_within_hop_range, preset_to_request, default_repeater_tx_height_m, resolved_site_tx_height_m, rf_json_for_preset};
use crate::seek_progress::SeekProgressHub;

const SEEK_EXCLUDE_PROXIMITY_M: f64 = 100.0;
const SEEK_GOAL_DEDUP_M: f64 = 1500.0;
const SEEK_SITE_PEAK_DEDUP_M: f64 = 500.0;
const SEEK_PEAK_BIN_MIN_M: f64 = 500.0;

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
    let ceiling = SEEK_PEAK_BIN_MIN_M.max(seek_cfg.peak_bin_size_m);
    let Some(req) = requested else {
        return Ok(ceiling);
    };
    if !req.is_finite() || req <= 0.0 {
        return Err(SeekError("peak_bin_size_m must be a positive number".into()));
    }
    Ok(SEEK_PEAK_BIN_MIN_M.max(ceiling.min(req)))
}

fn haversine_m(lat1: f64, lon1: f64, lat2: f64, lon2: f64) -> f64 {
    splatter::propagate::haversine_m(lat1, lon1, lat2, lon2)
}

fn bearing_deg(lat1: f64, lon1: f64, lat2: f64, lon2: f64) -> f64 {
    let phi1 = lat1.to_radians();
    let phi2 = lat2.to_radians();
    let dlambda = (lon2 - lon1).to_radians();
    let y = dlambda.sin() * phi2.cos();
    let x = phi1.cos() * phi2.sin() - phi1.sin() * phi2.cos() * dlambda.cos();
    (y.atan2(x).to_degrees() + 360.0) % 360.0
}

fn angle_diff_deg(a: f64, b: f64) -> f64 {
    let mut d = (a - b).abs() % 360.0;
    if d > 180.0 {
        d = 360.0 - d;
    }
    d
}

/// Half-width of the goal-direction search wedge at ``hop_m`` (degrees). Narrow at the source, widens with distance.
fn seek_wedge_half_angle_deg(hop_m: f64, hop_radius_m: f64) -> f64 {
    const NEAR_DEG: f64 = 10.0;
    const FAR_DEG: f64 = 50.0;
    if hop_radius_m <= 0.0 {
        return FAR_DEG;
    }
    let t = (hop_m / hop_radius_m).clamp(0.0, 1.0);
    NEAR_DEG + t * (FAR_DEG - NEAR_DEG)
}

fn peak_in_goal_wedge(
    from_lat: f64,
    from_lon: f64,
    goal_lat: f64,
    goal_lon: f64,
    peak_lat: f64,
    peak_lon: f64,
    hop_radius_m: f64,
    far_angle_scale: f64,
) -> bool {
    let hop_m = haversine_m(from_lat, from_lon, peak_lat, peak_lon);
    if hop_m <= 1.0 {
        return false;
    }
    let goal_bearing = bearing_deg(from_lat, from_lon, goal_lat, goal_lon);
    let peak_bearing = bearing_deg(from_lat, from_lon, peak_lat, peak_lon);
    let delta = angle_diff_deg(peak_bearing, goal_bearing);
    if delta >= 90.0 {
        return false;
    }
    let mut half = seek_wedge_half_angle_deg(hop_m, hop_radius_m);
    if far_angle_scale > 1.0 {
        let near = seek_wedge_half_angle_deg(0.0, hop_radius_m);
        half = near + (half - near) * far_angle_scale;
        half = half.min(89.0);
    }
    delta <= half
}

fn filter_peaks_in_goal_wedge(
    peaks: Vec<(f64, f64, f64)>,
    from_lat: f64,
    from_lon: f64,
    goal_lat: f64,
    goal_lon: f64,
    hop_radius_m: f64,
) -> Vec<(f64, f64, f64)> {
    for scale in [1.0, 1.5, 2.25, 4.0] {
        let filtered: Vec<_> = peaks
            .iter()
            .filter(|(lon, lat, _)| {
                peak_in_goal_wedge(
                    from_lat,
                    from_lon,
                    goal_lat,
                    goal_lon,
                    *lat,
                    *lon,
                    hop_radius_m,
                    scale,
                )
            })
            .copied()
            .collect();
        if !filtered.is_empty() {
            return filtered;
        }
    }
    peaks
}

/// Hop distance projected onto the source→goal bearing (m). Backward/side hops score lower.
fn forward_reach_m(
    from_lat: f64,
    from_lon: f64,
    goal_lat: f64,
    goal_lon: f64,
    peak_lat: f64,
    peak_lon: f64,
) -> f64 {
    let hop_m = haversine_m(from_lat, from_lon, peak_lat, peak_lon);
    if hop_m <= 0.0 {
        return 0.0;
    }
    let goal_bearing = bearing_deg(from_lat, from_lon, goal_lat, goal_lon);
    let peak_bearing = bearing_deg(from_lat, from_lon, peak_lat, peak_lon);
    let delta = angle_diff_deg(peak_bearing, goal_bearing).to_radians();
    hop_m * delta.cos().max(0.0)
}

/// Prefer farthest qualifying hops in the goal wedge, then elevation, then goal proximity.
fn seek_peak_rank_key(
    from_lat: f64,
    from_lon: f64,
    goal_lat: f64,
    goal_lon: f64,
    peak_lon: f64,
    peak_lat: f64,
    elev_m: f64,
) -> (f64, f64, f64) {
    let hop_m = haversine_m(from_lat, from_lon, peak_lat, peak_lon);
    let goal_dist = haversine_m(peak_lat, peak_lon, goal_lat, goal_lon);
    (hop_m, elev_m, -goal_dist)
}

fn cmp_seek_peak_rank(
    from_lat: f64,
    from_lon: f64,
    goal_lat: f64,
    goal_lon: f64,
    a: (f64, f64, f64),
    b: (f64, f64, f64),
) -> std::cmp::Ordering {
    let ka = seek_peak_rank_key(from_lat, from_lon, goal_lat, goal_lon, a.0, a.1, a.2);
    let kb = seek_peak_rank_key(from_lat, from_lon, goal_lat, goal_lon, b.0, b.1, b.2);
    // Farthest hop / highest elevation should sort first.
    kb.partial_cmp(&ka).unwrap_or(std::cmp::Ordering::Equal)
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

/// Forward goal-direction wedge (widest scale used by [`filter_peaks_in_goal_wedge`]).
fn goal_wedge_polygon(
    from_lat: f64,
    from_lon: f64,
    goal_lat: f64,
    goal_lon: f64,
    hop_radius_m: f64,
    far_angle_scale: f64,
) -> Polygon<f64> {
    let goal_bearing = bearing_deg(from_lat, from_lon, goal_lat, goal_lon);
    let mut half = seek_wedge_half_angle_deg(hop_radius_m, hop_radius_m);
    if far_angle_scale > 1.0 {
        let near = seek_wedge_half_angle_deg(0.0, hop_radius_m);
        half = near + (half - near) * far_angle_scale;
        half = half.min(89.0);
    }
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
    coords.push(Coord {
        x: from_lon,
        y: from_lat,
    });
    Polygon::new(LineString::from(coords), vec![])
}

/// Bounding box for peak DEM scan: goal wedge at hop range (scale 1.0, not map viewport).
fn seek_goal_wedge_scan_bbox(
    from_lat: f64,
    from_lon: f64,
    goal_lat: f64,
    goal_lon: f64,
    hop_m: f64,
) -> (f64, f64, f64, f64) {
    let wedge = goal_wedge_polygon(from_lat, from_lon, goal_lat, goal_lon, hop_m, 1.0);
    if let Some(rect) = wedge.bounding_rect() {
        return (rect.min().x, rect.min().y, rect.max().x, rect.max().y);
    }
    let hop = hop_disc_wgs84(from_lat, from_lon, hop_m);
    hop.bounding_rect()
        .map(|r| (r.min().x, r.min().y, r.max().x, r.max().y))
        .unwrap_or((from_lon, from_lat, from_lon, from_lat))
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

/// Hop disc ∩ map viewport (UI trim hints only; peak scan uses goal-wedge bbox).
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

fn goal_on_eligible(eligible: &Geometry<f64>, goal_lat: f64, goal_lon: f64) -> bool {
    point_on_eligible(eligible, goal_lat, goal_lon)
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
}

fn collect_reachable_site_rows(
    preset: &Preset,
    eligible: &Geometry<f64>,
    from_lat: f64,
    from_lon: f64,
    goal_lat: f64,
    goal_lon: f64,
    hop_radius_m: f64,
    exclude: &[SeekPoint],
    exclude_slugs: &HashSet<String>,
) -> Vec<(String, String, f64, f64, f64)> {
    let mut rows: Vec<(String, String, f64, f64, f64)> = preset
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
            if near_excluded(lat, lon, exclude) {
                return None;
            }
            if !goal_on_eligible(eligible, lat, lon) {
                return None;
            }
            if !peak_in_goal_wedge(
                from_lat,
                from_lon,
                goal_lat,
                goal_lon,
                lat,
                lon,
                hop_radius_m,
                1.0,
            ) {
                return None;
            }
            let elev = site.height_m.unwrap_or(0.0);
            Some((slug.clone(), site.name.clone(), lon, lat, elev))
        })
        .collect();
    rows.sort_by(|a, b| {
        cmp_seek_peak_rank(
            from_lat,
            from_lon,
            goal_lat,
            goal_lon,
            (a.2, a.3, a.4),
            (b.2, b.3, b.4),
        )
    });
    rows.into_iter()
        .map(|(slug, name, lon, lat, elev)| (slug, name, lon, lat, elev))
        .collect()
}

fn dedupe_peaks_near_sites(
    peaks: Vec<(f64, f64, f64)>,
    site_rows: &[(String, String, f64, f64, f64)],
) -> Vec<(f64, f64, f64)> {
    if site_rows.is_empty() {
        return peaks;
    }
    peaks
        .into_iter()
        .filter(|(lon, lat, _)| {
            !site_rows.iter().any(|(_, _, site_lon, site_lat, _)| {
                haversine_m(*lat, *lon, *site_lat, *site_lon) <= SEEK_SITE_PEAK_DEDUP_M
            })
        })
        .collect()
}

fn load_seek_candidates_body(
    session: &Session,
    verbose: bool,
    progress: &SeekProgressHub,
    req: &SeekRequest,
    scan_gen: u64,
) -> Result<Value, SeekRunError> {
    let scan_t0 = Instant::now();
    let ensure_active = || {
        if !progress.active(&req.slug, scan_gen) {
            return Err(SeekRunError::Cancelled);
        }
        Ok(())
    };

    let preset = load_preset(&req.preset_path)?;
    let seek_cfg = preset.seek.clone();
    let peak_bin_m = resolve_seek_peak_bin_size_m(&seek_cfg, req.peak_bin_size_m)
        .map_err(|e| SeekRunError::User(e.0, 422))?;

    ensure_active()?;
    progress.update(
        &req.slug,
        scan_gen,
        "eligible_land",
        0,
        0,
        "Building eligible land…",
    );
    let (eligible, eligible_digest) =
        load_or_build_eligible_land_union(&req.preset_path).map_err(|e| {
            match e.downcast_ref::<EligibleLandError>() {
                Some(el) => SeekRunError::User(el.0.clone(), 422),
                None => SeekRunError::User(e.to_string(), 422),
            }
        })?;

    let hop_km = match &preset.simulation.radius_km {
        serde_yaml::Value::Number(n) => n.as_f64().unwrap_or(50.0),
        serde_yaml::Value::String(s) => s.parse().unwrap_or(50.0),
        _ => 50.0,
    };

    progress.update(
        &req.slug,
        scan_gen,
        "trim",
        0,
        0,
        "Trimming to hop range…",
    );
    let eligible_mp = geometry_to_multi(&eligible);
    let land_index = LandFilterIndex::from_multipolygon(&eligible_mp);
    let mask_dir = eligible_land_dem_mask_dir(&req.preset_path, &eligible_digest);
    let land_filter = if eligible_mp.is_empty() {
        None
    } else {
        Some(eligible_mp)
    };

    let hop_m = hop_km * 1000.0;
    let scan_bbox = seek_goal_wedge_scan_bbox(
        req.from_lat,
        req.from_lon,
        req.goal_lat,
        req.goal_lon,
        hop_m,
    );
    let scan_wedge = splatter::peaks::GoalWedgeFilter {
        goal_lat: req.goal_lat,
        goal_lon: req.goal_lon,
        far_angle_scale: 1.0,
    };
    let hop_region = Geometry::Polygon(hop_disc_wgs84(req.from_lat, req.from_lon, hop_m));
    let search_region = match &land_filter {
        None => hop_region.clone(),
        Some(mp) => geom_intersection(hop_region, &Geometry::MultiPolygon(mp.clone())),
    };

    let (filtered, n_peaks_scanned, n_peaks_in_wedge) = if search_region.is_empty() {
        (Vec::new(), 0, 0)
    } else {
        ensure_active()?;
        progress.update(
            &req.slug,
            scan_gen,
            "dem",
            0,
            0,
            "Loading Skadi DEM…",
        );
        session
            .ensure_tiles_for_bounds(
                scan_bbox.0,
                scan_bbox.1,
                scan_bbox.2,
                scan_bbox.3,
            )
            .map_err(|e| SeekRunError::User(e.to_string(), 503))?;
        session
            .ensure_tiles_for_points(&[(req.from_lat, req.from_lon)], 5_000.0)
            .map_err(|e| SeekRunError::User(e.to_string(), 503))?;
        ensure_active()?;
        progress.update(
            &req.slug,
            scan_gen,
            "peak_scan",
            0,
            0,
            "Scanning ridge bins in goal wedge…",
        );
        let binned = session
            .disc_binned_peaks(
                req.from_lat,
                req.from_lon,
                hop_m,
                land_filter.as_ref(),
                Some(scan_bbox),
                Some(scan_wedge),
                None,
                peak_bin_m,
                Some(&land_index),
                Some(&mask_dir),
                None,
            )
            .map_err(|e| SeekRunError::User(e.to_string(), 503))?;
        let n = binned.len();
        let mut peaks: Vec<(f64, f64, f64)> = binned
            .into_iter()
            .filter(|p| !near_excluded(p.lat, p.lon, &req.exclude))
            .map(|p| (p.lon, p.lat, p.elev_m))
            .collect();
        peaks = filter_peaks_in_goal_wedge(
            peaks,
            req.from_lat,
            req.from_lon,
            req.goal_lat,
            req.goal_lon,
            hop_m,
        );
        let n_wedge = peaks.len();
        peaks.sort_by(|a, b| {
            cmp_seek_peak_rank(
                req.from_lat,
                req.from_lon,
                req.goal_lat,
                req.goal_lon,
                *a,
                *b,
            )
        });
        (peaks, n, n_wedge)
    };

    progress.update(
        &req.slug,
        scan_gen,
        "peak_links",
        filtered.len() as i32,
        n_peaks_scanned.max(1) as i32,
        &format!("Found {} peak(s) in goal wedge", filtered.len()),
    );

    let hop_m = hop_km * 1000.0;
    let tx_height = resolve_seek_from_tx_height_m(&preset, req.from_lat, req.from_lon);
    let rf_json = rf_json_for_preset(&preset).map_err(|e| SeekRunError::User(e.to_string(), 422))?;
    let cap = seek_cfg.max_candidates as usize;
    let mut capped: Vec<(f64, f64, f64)> = Vec::new();
    let mut peak_rf_viable_flags: Vec<bool> = Vec::new();
    let mut n_peak_rf_viable_in_wedge = 0usize;

    if !filtered.is_empty() {
        ensure_active()?;
        progress.update(
            &req.slug,
            scan_gen,
            "rf",
            0,
            filtered.len().max(1) as i32,
            &format!("Checking RF on {} wedge peak(s)…", filtered.len()),
        );
        let points: Vec<(f64, f64)> = std::iter::once((req.from_lat, req.from_lon))
            .chain(filtered.iter().map(|(lon, lat, _)| (*lat, *lon)))
            .collect();
        session
            .ensure_tiles_for_points(&points, hop_km * 1000.0)
            .map_err(|e| SeekRunError::User(e.to_string(), 503))?;
        let pairs: Vec<(f64, f64, f64)> = filtered
            .iter()
            .map(|(lon, lat, _)| (*lat, *lon, default_repeater_tx_height_m(&preset)))
            .collect();
        const RF_CHUNK: usize = 512;
        let mut peak_rf: Vec<bool> = Vec::with_capacity(filtered.len());
        for chunk in pairs.chunks(RF_CHUNK) {
            ensure_active()?;
            peak_rf.extend(
                session
                    .seek_repeater_link_batch(
                        req.from_lat,
                        req.from_lon,
                        tx_height,
                        chunk,
                        &rf_json,
                    )
                    .map_err(|e| SeekRunError::User(e.to_string(), 503))?,
            );
        }
        n_peak_rf_viable_in_wedge = peak_rf.iter().filter(|v| **v).count();
        let mut viable: Vec<(f64, f64, f64)> = filtered
            .iter()
            .copied()
            .zip(peak_rf.iter().copied())
            .filter_map(|(peak, ok)| ok.then_some(peak))
            .collect();
        viable.sort_by(|a, b| {
            cmp_seek_peak_rank(
                req.from_lat,
                req.from_lon,
                req.goal_lat,
                req.goal_lon,
                *a,
                *b,
            )
        });
        capped = viable.into_iter().take(cap).collect();
        peak_rf_viable_flags = vec![true; capped.len()];
    }

    let site_rows = collect_reachable_site_rows(
        &preset,
        &eligible,
        req.from_lat,
        req.from_lon,
        req.goal_lat,
        req.goal_lon,
        hop_m,
        &req.exclude,
        &req.exclude_slugs,
    );

    capped = dedupe_peaks_near_sites(capped, &site_rows);
    if peak_rf_viable_flags.len() > capped.len() {
        peak_rf_viable_flags.truncate(capped.len());
    } else if peak_rf_viable_flags.len() < capped.len() {
        peak_rf_viable_flags.extend(std::iter::repeat(false).take(
            capped.len() - peak_rf_viable_flags.len(),
        ));
    }

    let goal_in_hop_range = pair_within_hop_range(
        &preset,
        req.from_lat,
        req.from_lon,
        req.goal_lat,
        req.goal_lon,
    );
    let goal_on_eligible = goal_in_hop_range && goal_on_eligible(&eligible, req.goal_lat, req.goal_lon);
    let goal_near_prior_hop =
        goal_in_hop_range && near_excluded(req.goal_lat, req.goal_lon, &req.exclude);
    let goal_hop_eligible = goal_in_hop_range && goal_on_eligible && !goal_near_prior_hop;
    let goal_finish_eligible = goal_in_hop_range;

    let goal_elev = req.goal_elev_m.unwrap_or(0.0);
    let goal_row = if goal_finish_eligible {
        Some((req.goal_lon, req.goal_lat, goal_elev))
    } else {
        None
    };

    if goal_row.is_some() {
        capped = dedupe_peaks_near_sites(
            capped
                .into_iter()
                .filter(|(lon, lat, _)| {
                    haversine_m(req.goal_lat, req.goal_lon, *lat, *lon) > SEEK_GOAL_DEDUP_M
                })
                .take(cap)
                .collect(),
            &site_rows,
        );
    }

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
        });
    }
    for (slug, name, lon, lat, elev_m) in &site_rows {
        rf_candidates.push(RfCandidate {
            lon: *lon,
            lat: *lat,
            elev_m: *elev_m,
            candidate_id: format!("site:{slug}"),
            is_goal: false,
            is_site: true,
            site_slug: Some(slug.clone()),
            site_name: Some(name.clone()),
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
    let n_peak_rf_viable = n_peak_rf_viable_in_wedge.min(peak_count);

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
            "n_peaks_in_wedge": n_peaks_in_wedge,
            "n_peaks_binned": n_peaks_scanned,
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
            "peak_bin_size_m": peak_bin_m,
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
    fn wedge_excludes_sideways_peak_near_source() {
        let from_lat = 38.0;
        let from_lon = -117.0;
        let goal_lat = 40.0;
        let goal_lon = -117.0;
        let hop_m = 72_000.0;
        assert!(peak_in_goal_wedge(
            from_lat,
            from_lon,
            goal_lat,
            goal_lon,
            from_lat + 0.4,
            from_lon,
            hop_m,
            1.0,
        ));
        assert!(!peak_in_goal_wedge(
            from_lat,
            from_lon,
            goal_lat,
            goal_lon,
            from_lat,
            from_lon + 0.15,
            hop_m,
            1.0,
        ));
    }

    #[test]
    fn wedge_widens_toward_hop_range() {
        let hop_m = 72_000.0;
        let near = seek_wedge_half_angle_deg(5_000.0, hop_m);
        let far = seek_wedge_half_angle_deg(70_000.0, hop_m);
        assert!(far > near);
    }

    #[test]
    fn farthest_rank_prefers_distant_peak_on_goal_bearing() {
        let from_lat = 38.0;
        let from_lon = -117.0;
        let goal_lat = 40.0;
        let goal_lon = -117.0;
        let near = (from_lon, from_lat + 0.1, 2500.0);
        let far = (from_lon, from_lat + 0.5, 2500.0);
        assert!(
            cmp_seek_peak_rank(from_lat, from_lon, goal_lat, goal_lon, far, near)
                == std::cmp::Ordering::Less
        );
    }

    #[test]
    fn farthest_rank_prefers_higher_elev_at_same_bearing() {
        let from_lat = 38.0;
        let from_lon = -117.0;
        let goal_lat = 40.0;
        let goal_lon = -117.0;
        let high = (from_lon, from_lat + 0.3, 2800.0);
        let low = (from_lon, from_lat + 0.3, 2100.0);
        assert!(
            cmp_seek_peak_rank(from_lat, from_lon, goal_lat, goal_lon, high, low)
                == std::cmp::Ordering::Less
        );
    }

    #[test]
    fn farthest_rank_take_cap_gets_farthest_not_nearest() {
        let from_lat = 38.0;
        let from_lon = -117.0;
        let goal_lat = 40.0;
        let goal_lon = -117.0;
        let mut peaks = vec![
            (from_lon, from_lat + 0.01, 2000.0),
            (from_lon, from_lat + 0.5, 2200.0),
            (from_lon + 0.4, from_lat, 2500.0),
        ];
        peaks.sort_by(|a, b| {
            cmp_seek_peak_rank(from_lat, from_lon, goal_lat, goal_lon, *a, *b)
        });
        let best = peaks.first().unwrap();
        let hop_m = haversine_m(from_lat, from_lon, best.1, best.0);
        assert!(hop_m > 30_000.0, "best peak should be farthest in wedge, got {hop_m}m");
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
            project_dir.join("config.yaml")
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
            assert_eq!(result["meta"]["peak_bin_size_m"].as_f64().unwrap(), 750.0);
            assert!(result["meta"]["scan_ms"].as_u64().unwrap() < SEEK_BUDGET.as_millis() as u64);
            assert!(
                result["meta"]["goal_finish_eligible"].as_bool().unwrap_or(false),
                "goal should be within hop range"
            );
        }

        #[tokio::test]
        async fn api_seek_polls_to_done_within_ten_seconds() {
            use axum::body::Body;
            use axum::http::{Request, StatusCode};
            use tower::ServiceExt;

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
            let state = AppState {
                session: Arc::clone(&session),
                events: events.clone(),
                warm: WarmHub::new(session, events, false, 2),
                seek,
                verbose: false,
                dem_tile_render: Arc::new(Semaphore::new(crate::state::DEM_TILE_RENDER_PERMITS)),
                project_dir: preset_path.parent().unwrap().to_path_buf(),
                slug: "seek-sample".into(),
            };
            let app = router(state);

            let qs = "from_lat=40.0&from_lon=-119.5&goal_lat=40.25&goal_lon=-119.25\
                &bbox=-120,39,-119,41&peak_bin_size_m=750&exclude_slugs=start;goal";
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
                    assert_eq!(
                        body["result"]["meta"]["peak_bin_size_m"].as_f64().unwrap(),
                        750.0
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
