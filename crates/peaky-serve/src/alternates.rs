//! Site alternates: other placements that still mutual-P2P to a site's RF neighbors.

use std::collections::{HashMap, HashSet};
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::path::PathBuf;
use std::sync::{Arc, Condvar, Mutex};
use std::thread;
use std::time::Instant;

use anyhow::Result;
use geo::{BoundingRect, Coord, LineString, Polygon};
use peaky_geo::{load_or_build_eligible_land_parts, EligibleLandError, LonLatBBox};
use peaky_preset::{load_preset, Preset};
use serde_json::{json, Value};
use splatter::Session;

use crate::links::load_single_site_links;
use crate::rf::{
    default_repeater_tx_height_m, load_board_viewshed, max_hop_range_m, preset_to_request,
    resolved_site_tx_height_m, rf_json_for_preset, site_hop_radius_m,
};
use crate::geo::bearing_deg;
use crate::peaks::catalog_peaks_filtered;
use crate::scan_progress::ScanProgressHub;
use splatter::propagate::haversine_m;

const SUBJECT_EXCLUDE_M: f64 = 500.0;
const SITE_PEAK_DEDUP_M: f64 = 500.0;
const LANDING_COARSE_M: f64 = 1500.0;
const CANDIDATE_SEP_M: f64 = 800.0;
const RF_CHUNK: usize = 512;
const MESH_NEIGHBOR_CAP: usize = 32;

#[derive(Debug, thiserror::Error)]
#[error("{0}")]
pub struct AlternatesError(pub String);

pub fn alternates_progress_key(site_slug: &str, anchor_slugs: Option<&[String]>) -> String {
    match anchor_slugs.filter(|s| !s.is_empty()) {
        Some(slugs) => {
            let mut sorted = slugs.to_vec();
            sorted.sort();
            format!("alternates:{site_slug}:{}", sorted.join(","))
        }
        None => format!("alternates:{site_slug}"),
    }
}

#[derive(Debug, Clone)]
pub struct AlternatesRequest {
    pub progress_key: String,
    pub preset_path: PathBuf,
    pub site_slug: String,
    /// When set, only these RF-linked neighbor slugs are required anchors (visible subset).
    pub anchor_slugs: Option<Vec<String>>,
    pub peak_bin_size_m: Option<f64>,
}

#[derive(Debug, Clone)]
struct Anchor {
    slug: String,
    name: String,
    lat: f64,
    lon: f64,
    tx_h: f64,
}

struct AlternatesJob {
    progress_key: String,
    gen: u64,
    request: AlternatesRequest,
}

struct QueueInner {
    pending: HashMap<String, AlternatesJob>,
}

struct AlternatesJobQueue {
    inner: Mutex<QueueInner>,
    cv: Condvar,
}

impl AlternatesJobQueue {
    fn new() -> Self {
        Self {
            inner: Mutex::new(QueueInner {
                pending: HashMap::new(),
            }),
            cv: Condvar::new(),
        }
    }

    fn submit(&self, job: AlternatesJob) {
        let mut inner = self.inner.lock().unwrap();
        inner.pending.insert(job.progress_key.clone(), job);
        self.cv.notify_one();
    }

    fn take(&self) -> AlternatesJob {
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
pub struct AlternatesHub {
    progress: ScanProgressHub,
    queue: Arc<AlternatesJobQueue>,
}

impl AlternatesHub {
    pub fn new(session: Arc<Session>, verbose: bool) -> Self {
        let queue = Arc::new(AlternatesJobQueue::new());
        let progress = ScanProgressHub::default();
        for _ in 0..alternates_workers() {
            let queue = Arc::clone(&queue);
            let progress = progress.clone();
            let session = Arc::clone(&session);
            thread::spawn(move || {
                loop {
                    let job = queue.take();
                    let key = job.progress_key.clone();
                    let gen = job.gen;
                    let result = catch_unwind(AssertUnwindSafe(|| {
                        run_alternates_job(&session, verbose, &progress, job);
                    }));
                    if result.is_err() {
                        progress.finish(
                            &key,
                            gen,
                            "error",
                            None,
                            Some("Alternates worker crashed during geometry or RF scan"),
                            Some(500),
                        );
                    }
                }
            });
        }
        Self { progress, queue }
    }

    pub fn enqueue(&self, request: AlternatesRequest) -> Result<u64, AlternatesError> {
        let key = request.progress_key.clone();
        let gen = self.progress.begin(&key);
        self.queue.submit(AlternatesJob {
            progress_key: key,
            gen,
            request,
        });
        Ok(gen)
    }

    pub fn poll(&self, progress_key: &str, project: &str) -> Value {
        self.progress.poll(progress_key, project)
    }
}

fn alternates_workers() -> usize {
    std::env::var("PEAKY_SERVE_ALTERNATES_WORKERS")
        .ok()
        .and_then(|v| v.parse().ok())
        .filter(|n| *n >= 1)
        .unwrap_or(1)
}

enum AlternatesRunError {
    Cancelled,
    User(String, u16),
    Internal(String),
}

impl From<anyhow::Error> for AlternatesRunError {
    fn from(value: anyhow::Error) -> Self {
        AlternatesRunError::Internal(value.to_string())
    }
}

fn run_alternates_job(
    session: &Arc<Session>,
    verbose: bool,
    progress: &ScanProgressHub,
    job: AlternatesJob,
) {
    let key = job.progress_key.clone();
    let gen = job.gen;
    match load_alternates_body(session, verbose, progress, &job.request, gen) {
        Ok(result) => progress.finish(&key, gen, "done", Some(result), None, None),
        Err(AlternatesRunError::Cancelled) => {
            progress.finish(&key, gen, "cancelled", None, None, None);
        }
        Err(AlternatesRunError::User(msg, status)) => {
            progress.finish(&key, gen, "error", None, Some(&msg), Some(status));
        }
        Err(AlternatesRunError::Internal(msg)) => {
            progress.finish(&key, gen, "error", None, Some(&msg), Some(500));
        }
    }
}

pub fn parse_alternates_request(
    preset_path: PathBuf,
    params: &HashMap<String, String>,
) -> Result<AlternatesRequest, AlternatesError> {
    let site_slug = params
        .get("site")
        .map(|s| s.trim())
        .filter(|s| !s.is_empty())
        .ok_or_else(|| AlternatesError("site query parameter is required".into()))?
        .to_string();
    let anchor_slugs = params
        .get("anchors")
        .map(|raw| {
            raw.split(',')
                .map(|s| s.trim())
                .filter(|s| !s.is_empty())
                .map(String::from)
                .collect::<Vec<_>>()
        })
        .filter(|slugs| !slugs.is_empty());
    let peak_bin_size_m = match params
        .get("peak_bin_size_m")
        .map(|s| s.trim())
        .filter(|s| !s.is_empty())
    {
        None => None,
        Some(raw) => Some(
            raw.parse()
                .map_err(|_| AlternatesError("peak_bin_size_m must be a number".into()))?,
        ),
    };
    Ok(AlternatesRequest {
        progress_key: alternates_progress_key(&site_slug, anchor_slugs.as_deref()),
        preset_path,
        site_slug,
        anchor_slugs,
        peak_bin_size_m,
    })
}

/// Hop disc anchor: ``(lat, lon, hop_m)``.
pub type HopDiscAnchor = (f64, f64, f64);

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

fn hop_disc_bbox(lat: f64, lon: f64, hop_m: f64) -> (f64, f64, f64, f64) {
    let n = 64usize;
    let mut coords = Vec::with_capacity(n + 1);
    for i in 0..=n {
        let bearing = 360.0 * i as f64 / n as f64;
        let (dest_lat, dest_lon) = destination_point(lat, lon, bearing, hop_m);
        coords.push(Coord {
            x: dest_lon,
            y: dest_lat,
        });
    }
    let poly = Polygon::new(LineString::from(coords), vec![]);
    poly.bounding_rect()
        .map(|r| (r.min().x, r.min().y, r.max().x, r.max().y))
        .unwrap_or((lon, lat, lon, lat))
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

/// Intersection bbox of hop discs around each anchor (per-anchor radius).
pub fn multi_disc_lens_bbox(anchors: &[HopDiscAnchor]) -> (f64, f64, f64, f64) {
    let mut bbox = hop_disc_bbox(anchors[0].0, anchors[0].1, anchors[0].2);
    for &(lat, lon, hop_m) in anchors.iter().skip(1) {
        bbox = bbox_intersection(bbox, hop_disc_bbox(lat, lon, hop_m));
    }
    bbox
}

pub fn inside_all_discs(lat: f64, lon: f64, anchors: &[HopDiscAnchor]) -> bool {
    anchors
        .iter()
        .all(|&(alat, alon, hop_m)| haversine_m(lat, lon, alat, alon) <= hop_m + 1.0)
}

pub fn grid_points_in_lens(
    scan_bbox: (f64, f64, f64, f64),
    anchors: &[HopDiscAnchor],
    step_m: f64,
) -> Vec<(f64, f64)> {
    let (west, south, east, north) = scan_bbox;
    if west >= east || south >= north {
        return Vec::new();
    }
    let mid_lat = (south + north) / 2.0;
    let lat_step = step_m / 111_000.0;
    let lon_step = step_m / (111_000.0 * mid_lat.to_radians().cos().max(0.01));
    let mut out = Vec::new();
    let mut lat = south;
    while lat <= north + 1e-9 {
        let mut lon = west;
        while lon <= east + 1e-9 {
            if inside_all_discs(lat, lon, anchors) {
                out.push((lat, lon));
            }
            lon += lon_step;
        }
        lat += lat_step;
    }
    out
}

fn take_spatially_diverse<T: Clone>(
    ranked: Vec<(T, f64, f64)>,
    min_sep_m: f64,
    cap: usize,
) -> Vec<T> {
    let mut taken = Vec::new();
    for (item, lat, lon) in ranked {
        if taken.len() >= cap {
            break;
        }
        let too_close = taken.iter().any(|(_, tlat, tlon)| {
            haversine_m(lat, lon, *tlat, *tlon) <= min_sep_m
        });
        if !too_close {
            taken.push((item, lat, lon));
        }
    }
    taken.into_iter().map(|(item, _, _)| item).collect()
}

#[derive(Clone)]
struct PeakCandidate {
    lon: f64,
    lat: f64,
    elev_m: f64,
    candidate_id: String,
    margin_db: f64,
    mesh_links: u32,
    is_site: bool,
    site_slug: Option<String>,
    site_name: Option<String>,
}

fn resolve_subject_tx_height(preset: &Preset, _lat: f64, _lon: f64, site: &peaky_preset::SiteEntry) -> f64 {
    resolved_site_tx_height_m(preset, site).max(1.0)
}

fn default_candidate_tx_height(preset: &Preset, lat: f64, lon: f64) -> f64 {
    preset_to_request(preset, lat, lon, None)
        .map(|req| req.tx_height.max(1.0))
        .unwrap_or_else(|_| default_repeater_tx_height_m(&preset).max(1.0))
}

fn linked_peer_slugs(links_payload: &Value, site_slug: &str) -> Vec<String> {
    let mut peers = Vec::new();
    if let Some(rows) = links_payload.get("links").and_then(|v| v.as_array()) {
        for row in rows {
            let linked = row.get("linked").and_then(|v| v.as_bool()).unwrap_or(false);
            if !linked {
                continue;
            }
            let a = row.get("a").and_then(|v| v.as_str()).unwrap_or("");
            let b = row.get("b").and_then(|v| v.as_str()).unwrap_or("");
            if a == site_slug && !b.is_empty() {
                peers.push(b.to_string());
            } else if b == site_slug && !a.is_empty() {
                peers.push(a.to_string());
            }
        }
    }
    peers.sort();
    peers.dedup();
    peers
}

fn margins_to_all_anchors(
    session: &Session,
    from_lat: f64,
    from_lon: f64,
    from_tx_h: f64,
    anchors: &[Anchor],
    rf_json: &str,
) -> Result<Option<f64>, AlternatesRunError> {
    if anchors.is_empty() {
        return Ok(None);
    }
    let endpoints: Vec<(f64, f64, f64)> = anchors
        .iter()
        .map(|a| (a.lat, a.lon, a.tx_h))
        .collect();
    let margins = session
        .seek_repeater_link_margins(from_lat, from_lon, from_tx_h, &endpoints, rf_json)
        .map_err(|e| AlternatesRunError::User(e.to_string(), 503))?;
    let mut min_margin = f64::INFINITY;
    for margin in margins {
        let Some(m) = margin else {
            return Ok(None);
        };
        min_margin = min_margin.min(m);
    }
    Ok(Some(min_margin))
}

fn count_mesh_links(
    session: &Session,
    mesh: &[(f64, f64, f64, f64)],
    candidate_lat: f64,
    candidate_lon: f64,
    candidate_tx_h: f64,
    candidate_hop_m: f64,
    rf_json: &str,
) -> Result<u32, AlternatesRunError> {
    if mesh.is_empty() {
        return Ok(0);
    }
    let candidate = [(candidate_lat, candidate_lon, candidate_tx_h)];
    let points: Vec<(f64, f64)> = mesh
        .iter()
        .map(|(lat, lon, _, _)| (*lat, *lon))
        .chain(std::iter::once((candidate_lat, candidate_lon)))
        .collect();
    session
        .ensure_tiles_for_points(&points, candidate_hop_m)
        .map_err(|e| AlternatesRunError::User(e.to_string(), 503))?;
    let mut count = 0u32;
    for &(site_lat, site_lon, site_h, site_hop_m) in mesh {
        let pair_hop = site_hop_m.min(candidate_hop_m);
        if haversine_m(site_lat, site_lon, candidate_lat, candidate_lon) > pair_hop + 1.0 {
            continue;
        }
        let ok = session
            .seek_repeater_link_batch(site_lat, site_lon, site_h, &candidate, rf_json)
            .map_err(|e| AlternatesRunError::User(e.to_string(), 503))?;
        if ok.first().copied().unwrap_or(false) {
            count += 1;
        }
    }
    Ok(count)
}

fn local_mesh_sites(
    preset: &Preset,
    boards: &peaky_preset::BoardViewshedResolver,
    exclude_slugs: &HashSet<String>,
    lens_anchors: &[HopDiscAnchor],
) -> Vec<(f64, f64, f64, f64)> {
    let mut rows: Vec<(f64, f64, f64, f64)> = preset
        .sites
        .iter()
        .filter_map(|(slug, site)| {
            if exclude_slugs.contains(slug) {
                return None;
            }
            let lat = site.loc[0];
            let lon = site.loc[1];
            if !inside_all_discs(lat, lon, lens_anchors) {
                return None;
            }
            let h = resolved_site_tx_height_m(preset, site);
            let hop_m = site_hop_radius_m(preset, site, Some(boards));
            Some((lat, lon, h, hop_m))
        })
        .collect();
    rows.sort_by(|a, b| a.0.partial_cmp(&b.0).unwrap_or(std::cmp::Ordering::Equal));
    rows.truncate(MESH_NEIGHBOR_CAP);
    rows
}

fn load_alternates_body(
    session: &Arc<Session>,
    _verbose: bool,
    progress: &ScanProgressHub,
    req: &AlternatesRequest,
    scan_gen: u64,
) -> Result<Value, AlternatesRunError> {
    let scan_t0 = Instant::now();
    let key = &req.progress_key;
    let mut ensure_active = || {
        if !progress.active(key, scan_gen) {
            return Err(AlternatesRunError::Cancelled);
        }
        Ok(())
    };

    let preset = load_preset(&req.preset_path)?;
    let scan_cfg = preset.scan.clone();
    let cap = scan_cfg.max_candidates as usize;
    let boards = load_board_viewshed(&req.preset_path);
    let candidate_hop_m = max_hop_range_m(&preset);

    let subject = preset
        .sites
        .get(&req.site_slug)
        .ok_or_else(|| AlternatesRunError::User(format!("unknown site {}", req.site_slug), 422))?;
    let subject_lat = subject.loc[0];
    let subject_lon = subject.loc[1];
    let subject_name = subject.name.clone();
    let subject_tx_h = resolve_subject_tx_height(&preset, subject_lat, subject_lon, subject);

    ensure_active()?;
    progress.update(key, scan_gen, "links", 0, 0, "Loading RF neighbors…");

    let session_arc = Arc::clone(session);
    let links_payload = load_single_site_links(
        session_arc,
        &req.preset_path,
        &preset,
        &req.site_slug,
    )
    .map_err(|e| AlternatesRunError::User(e.to_string(), 503))?;
    let all_peer_slugs = linked_peer_slugs(&links_payload, &req.site_slug);
    let peer_slugs = match &req.anchor_slugs {
        None => all_peer_slugs,
        Some(requested) => {
            let allowed: HashSet<&str> = requested.iter().map(String::as_str).collect();
            let filtered: Vec<String> = all_peer_slugs
                .into_iter()
                .filter(|slug| allowed.contains(slug.as_str()))
                .collect();
            if filtered.is_empty() {
                return Err(AlternatesRunError::User(
                    "no visible RF-linked neighbors for this site".into(),
                    422,
                ));
            }
            filtered
        }
    };
    if peer_slugs.is_empty() {
        return Err(AlternatesRunError::User(
            "site has no RF-linked neighbors".into(),
            422,
        ));
    }

    let mut anchors: Vec<Anchor> = Vec::new();
    for slug in &peer_slugs {
        let site = preset
            .sites
            .get(slug)
            .ok_or_else(|| AlternatesRunError::User(format!("unknown anchor site {slug}"), 422))?;
        anchors.push(Anchor {
            slug: slug.clone(),
            name: site.name.clone(),
            lat: site.loc[0],
            lon: site.loc[1],
            tx_h: resolved_site_tx_height_m(&preset, site).max(1.0),
        });
    }
    let lens_anchors: Vec<HopDiscAnchor> = anchors
        .iter()
        .map(|a| {
            let site = preset.sites.get(&a.slug).expect("anchor site");
            (
                a.lat,
                a.lon,
                site_hop_radius_m(&preset, site, Some(&boards)),
            )
        })
        .collect();
    let scan_bbox = multi_disc_lens_bbox(&lens_anchors);
    if scan_bbox.0 >= scan_bbox.2 || scan_bbox.1 >= scan_bbox.3 {
        return Err(AlternatesRunError::User(
            "those neighbors do not share a one-hop lens".into(),
            422,
        ));
    }

    ensure_active()?;
    progress.update(key, scan_gen, "eligible_land", 0, 0, "Building eligible land…");
    let clip = LonLatBBox::from_tuple(scan_bbox).padded(0.05);
    let land_parts = load_or_build_eligible_land_parts(&req.preset_path, Some(clip)).map_err(
        |e| match e.downcast_ref::<EligibleLandError>() {
            Some(el) => AlternatesRunError::User(el.0.clone(), 422),
            None => AlternatesRunError::User(e.to_string(), 422),
        },
    )?;
    let eligible_digest = land_parts.digest.clone();
    let land_index = land_parts.index();
    let rf_json = rf_json_for_preset(&preset).map_err(|e| AlternatesRunError::User(e.to_string(), 422))?;

    ensure_active()?;
    progress.update(key, scan_gen, "dem", 0, 0, "Loading Skadi DEM…");
    session
        .ensure_tiles_for_bounds(scan_bbox.0, scan_bbox.1, scan_bbox.2, scan_bbox.3)
        .map_err(|e| AlternatesRunError::User(e.to_string(), 503))?;

    let mut exclude_slugs: HashSet<String> = HashSet::new();
    exclude_slugs.insert(req.site_slug.clone());
    for slug in &peer_slugs {
        exclude_slugs.insert(slug.clone());
    }
    let mesh = local_mesh_sites(&preset, &boards, &exclude_slugs, &lens_anchors);

    ensure_active()?;
    progress.update(key, scan_gen, "catalog", 0, 0, "Loading peaks catalog…");

    let (mut peaks, n_catalog_peaks) =
        catalog_peaks_filtered(&req.preset_path, |lat, lon| {
            inside_all_discs(lat, lon, &lens_anchors)
                && haversine_m(lat, lon, subject_lat, subject_lon) > SUBJECT_EXCLUDE_M
        })
        .map_err(|e| AlternatesRunError::User(e, 422))?;

    peaks.retain(|(lon, lat, _)| {
        !preset.sites.iter().any(|(slug, site)| {
            if exclude_slugs.contains(slug) {
                return false;
            }
            haversine_m(*lat, *lon, site.loc[0], site.loc[1]) <= SITE_PEAK_DEDUP_M
        })
    });

    progress.update(
        key,
        scan_gen,
        "catalog",
        peaks.len() as i32,
        n_catalog_peaks.max(1) as i32,
        &format!("Found {} catalog peak(s) in RF lens", peaks.len()),
    );

    ensure_active()?;
    progress.update(
        key,
        scan_gen,
        "rf",
        0,
        peaks.len().max(1) as i32,
        &format!("Checking RF on {} candidate(s)…", peaks.len()),
    );

    let mut viable: Vec<(f64, f64, f64, f64, u32)> = Vec::new();
    for chunk in peaks.chunks(RF_CHUNK) {
        ensure_active()?;
        let points: Vec<(f64, f64)> = chunk
            .iter()
            .map(|(lon, lat, _)| (*lat, *lon))
            .chain(lens_anchors.iter().map(|(lat, lon, _)| (*lat, *lon)))
            .collect();
        session
            .ensure_tiles_for_points(&points, candidate_hop_m)
            .map_err(|e| AlternatesRunError::User(e.to_string(), 503))?;
        for (lon, lat, elev) in chunk {
            let tx_h = default_candidate_tx_height(&preset, *lat, *lon);
            let margin = margins_to_all_anchors(session, *lat, *lon, tx_h, &anchors, &rf_json)?;
            let Some(min_margin) = margin else {
                continue;
            };
            let mesh_links = count_mesh_links(
                session,
                &mesh,
                *lat,
                *lon,
                tx_h,
                candidate_hop_m,
                &rf_json,
            )?;
            viable.push((*lon, *lat, *elev, min_margin, mesh_links));
        }
    }

    viable.sort_by(|a, b| {
        b.4.cmp(&a.4)
            .then_with(|| b.3.partial_cmp(&a.3).unwrap_or(std::cmp::Ordering::Equal))
    });

    let peak_candidates: Vec<PeakCandidate> = take_spatially_diverse(
        viable
            .into_iter()
            .enumerate()
            .map(|(i, (lon, lat, elev, margin, mesh_links))| {
                (
                    PeakCandidate {
                        lon,
                        lat,
                        elev_m: elev,
                        candidate_id: format!("c{i}"),
                        margin_db: margin,
                        mesh_links,
                        is_site: false,
                        site_slug: None,
                        site_name: None,
                    },
                    lat,
                    lon,
                )
            })
            .collect(),
        CANDIDATE_SEP_M,
        cap,
    );

    let mut site_candidates: Vec<PeakCandidate> = Vec::new();
    for (slug, site) in &preset.sites {
        if exclude_slugs.contains(slug) {
            continue;
        }
        let lat = site.loc[0];
        let lon = site.loc[1];
        if haversine_m(lat, lon, subject_lat, subject_lon) <= SUBJECT_EXCLUDE_M {
            continue;
        }
        if !inside_all_discs(lat, lon, &lens_anchors) {
            continue;
        }
        if !land_index.contains(lon, lat) {
            continue;
        }
        let tx_h = resolved_site_tx_height_m(&preset, site).max(1.0);
        let margin = margins_to_all_anchors(session, lat, lon, tx_h, &anchors, &rf_json)?;
        if margin.is_none() {
            continue;
        }
        let mesh_links = count_mesh_links(
            session,
            &mesh,
            lat,
            lon,
            tx_h,
            site_hop_radius_m(&preset, site, Some(&boards)),
            &rf_json,
        )?;
        site_candidates.push(PeakCandidate {
            lon,
            lat,
            elev_m: site.height_m.unwrap_or(0.0),
            candidate_id: format!("site:{slug}"),
            margin_db: margin.unwrap_or(0.0),
            mesh_links,
            is_site: true,
            site_slug: Some(slug.clone()),
            site_name: Some(site.name.clone()),
        });
    }
    site_candidates.sort_by(|a, b| {
        b.mesh_links
            .cmp(&a.mesh_links)
            .then_with(|| b.margin_db.partial_cmp(&a.margin_db).unwrap_or(std::cmp::Ordering::Equal))
    });

    let mut all_candidates = site_candidates;
    all_candidates.extend(peak_candidates);

    let anchor_json: Vec<Value> = anchors
        .iter()
        .map(|a| {
            json!({
                "slug": a.slug,
                "name": a.name,
                "lat": a.lat,
                "lon": a.lon,
            })
        })
        .collect();

    let mut candidate_features = Vec::new();
    let mut line_features = Vec::new();
    let mut site_candidate_slugs = Vec::new();
    let n_peak = all_candidates.iter().filter(|c| !c.is_site).count();

    for row in &all_candidates {
        let dist_km = haversine_m(subject_lat, subject_lon, row.lat, row.lon) / 1000.0;
        let bearing = bearing_deg(subject_lat, subject_lon, row.lat, row.lon);
        let mut props = json!({
            "candidate_id": row.candidate_id,
            "lat": row.lat,
            "lon": row.lon,
            "elev_m": (row.elev_m * 10.0).round() / 10.0,
            "distance_km": (dist_km * 10.0).round() / 10.0,
            "bearing_deg": bearing.round(),
            "rf_viable": true,
            "completes_anchors": true,
            "margin_db": (row.margin_db * 10.0).round() / 10.0,
            "mesh_links": row.mesh_links,
        });
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
        for anchor in &anchors {
            let anchor_dist = haversine_m(row.lat, row.lon, anchor.lat, anchor.lon) / 1000.0;
            line_features.push(json!({
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[row.lon, row.lat], [anchor.lon, anchor.lat]],
                },
                "properties": {
                    "candidate_id": row.candidate_id,
                    "anchor_slug": anchor.slug,
                    "distance_km": (anchor_dist * 10.0).round() / 10.0,
                    "rf_viable": true,
                },
            }));
        }
    }

    Ok(json!({
        "subject": {
            "slug": req.site_slug,
            "name": subject_name,
            "lat": subject_lat,
            "lon": subject_lon,
            "tx_height_m": subject_tx_h,
        },
        "anchors": anchor_json,
        "candidates": { "type": "FeatureCollection", "features": candidate_features },
        "lines": { "type": "FeatureCollection", "features": line_features },
        "meta": {
            "n_candidates": n_peak,
            "n_site_candidates": site_candidate_slugs.len(),
            "site_candidate_slugs": site_candidate_slugs,
            "anchor_slugs": peer_slugs,
            "eligible_digest": eligible_digest,
            "hop_range_km": lens_anchors
                .iter()
                .map(|(_, _, hop_m)| hop_m / 1000.0)
                .fold(f64::INFINITY, f64::min),
            "n_catalog_peaks": n_catalog_peaks,
            "scan_ms": scan_t0.elapsed().as_millis(),
        },
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn multi_disc_lens_includes_off_axis_hub() {
        let from_lat = 39.75567;
        let from_lon = -119.46126;
        let goal_lat = 39.778464;
        let goal_lon = -119.049911;
        let hop_m = 72_000.0;
        let hub_lat = 39.799379;
        let hub_lon = -119.184290;
        let anchors = [(from_lat, from_lon, hop_m), (goal_lat, goal_lon, hop_m)];
        let bbox = multi_disc_lens_bbox(&anchors);
        assert!(bbox.0 < bbox.2 && bbox.1 < bbox.3);
        assert!(inside_all_discs(hub_lat, hub_lon, &anchors));
        let locs = grid_points_in_lens(bbox, &anchors, LANDING_COARSE_M);
        assert!(
            locs.iter()
                .any(|(lat, lon)| haversine_m(*lat, *lon, hub_lat, hub_lon) <= LANDING_COARSE_M),
            "grid must sample the off-axis hub inside both discs"
        );
    }

    #[test]
    fn multi_disc_lens_empty_for_far_anchors() {
        let hop_m = 10_000.0;
        let anchors = [(39.0, -119.0, hop_m), (41.0, -117.0, hop_m)];
        let bbox = multi_disc_lens_bbox(&anchors);
        assert!(bbox.0 >= bbox.2 || bbox.1 >= bbox.3);
    }

    #[test]
    fn two_t096_discs_exclude_bare_schader_mid_gap() {
        let hop_m = 50_000.0;
        let bare = (36.87133, -116.683758);
        let schader = (36.46147, -116.06689);
        let ab = haversine_m(bare.0, bare.1, schader.0, schader.1);
        assert!(ab > 70_000.0);
        let t_out = 55_000.0 / ab;
        let out = (
            bare.0 + t_out * (schader.0 - bare.0),
            bare.1 + t_out * (schader.1 - bare.1),
        );
        let t_in = 25_000.0 / ab;
        let inside = (
            bare.0 + t_in * (schader.0 - bare.0),
            bare.1 + t_in * (schader.1 - bare.1),
        );
        let anchors = [(bare.0, bare.1, hop_m), (schader.0, schader.1, hop_m)];
        assert!(!inside_all_discs(out.0, out.1, &anchors));
        assert!(inside_all_discs(inside.0, inside.1, &anchors));
    }

    #[test]
    fn subject_pin_excluded_from_nearby() {
        let subject = (39.77, -119.17);
        let locs = vec![subject, (39.771, -119.171)];
        let samples: Vec<_> = locs
            .into_iter()
            .filter(|(lat, lon)| haversine_m(*lat, *lon, subject.0, subject.1) > SUBJECT_EXCLUDE_M)
            .collect();
        assert!(samples.is_empty());
    }
}
