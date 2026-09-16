//! Fortify: catalog peaks that mutual-P2P both ends of an existing site link.

use std::collections::{HashMap, HashSet};
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::path::PathBuf;
use std::sync::{Arc, Condvar, Mutex};
use std::thread;
use std::time::Instant;

use anyhow::Result;
use peaky_preset::{load_preset, worse_difficulty, PeakCatalogEntry, Preset};

use crate::peaks_cache::cached_peaks_catalog_thin;
use serde_json::{json, Value};
use splatter::Session;

use crate::alternates::{inside_all_discs, multi_disc_lens_bbox, HopDiscAnchor};
use crate::geo::along_track_t;
use crate::rf::{load_board_viewshed, max_hop_range_m, site_hop_radius_m};
use crate::links::canonical_site_pair;
use crate::rf::{
    default_candidate_tx_height, mutual_rf_viable, resolved_site_tx_height_m, rf_json_for_preset,
};
use crate::scan_progress::ScanProgressHub;
use splatter::propagate::haversine_m;

const RF_CHUNK: usize = 512;

#[derive(Debug, thiserror::Error)]
#[error("{0}")]
pub struct FortifyError(pub String);

pub fn fortify_progress_key(slug_a: &str, slug_b: &str) -> String {
    let (a, b) = canonical_site_pair(slug_a, slug_b);
    format!("fortify:{a}:{b}")
}

#[derive(Debug, Clone)]
pub struct FortifyRequest {
    pub progress_key: String,
    pub preset_path: PathBuf,
    pub slug_a: String,
    pub slug_b: String,
    pub exclude_peaks: HashSet<String>,
}

#[derive(Debug, Clone)]
struct Endpoint {
    slug: String,
    name: String,
    lat: f64,
    lon: f64,
    tx_h: f64,
}

struct FortifyJob {
    progress_key: String,
    gen: u64,
    request: FortifyRequest,
}

struct QueueInner {
    pending: HashMap<String, FortifyJob>,
}

struct FortifyJobQueue {
    inner: Mutex<QueueInner>,
    cv: Condvar,
}

impl FortifyJobQueue {
    fn new() -> Self {
        Self {
            inner: Mutex::new(QueueInner {
                pending: HashMap::new(),
            }),
            cv: Condvar::new(),
        }
    }

    fn submit(&self, job: FortifyJob) {
        let mut inner = self.inner.lock().unwrap();
        inner.pending.insert(job.progress_key.clone(), job);
        self.cv.notify_one();
    }

    fn take(&self) -> FortifyJob {
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
pub struct FortifyHub {
    progress: ScanProgressHub,
    queue: Arc<FortifyJobQueue>,
}

impl FortifyHub {
    pub fn new(session: Arc<Session>, verbose: bool) -> Self {
        let queue = Arc::new(FortifyJobQueue::new());
        let progress = ScanProgressHub::default();
        for _ in 0..fortify_workers() {
            let queue = Arc::clone(&queue);
            let progress = progress.clone();
            let session = Arc::clone(&session);
            thread::spawn(move || {
                loop {
                    let job = queue.take();
                    let key = job.progress_key.clone();
                    let gen = job.gen;
                    let result = catch_unwind(AssertUnwindSafe(|| {
                        run_fortify_job(&session, verbose, &progress, job);
                    }));
                    if result.is_err() {
                        progress.finish(
                            &key,
                            gen,
                            "error",
                            None,
                            Some("Fortify worker crashed during geometry or RF scan"),
                            Some(500),
                        );
                    }
                }
            });
        }
        Self { progress, queue }
    }

    pub fn enqueue(&self, request: FortifyRequest) -> Result<u64, FortifyError> {
        let key = request.progress_key.clone();
        let gen = self.progress.begin(&key);
        self.queue.submit(FortifyJob {
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

fn fortify_workers() -> usize {
    std::env::var("PEAKY_SERVE_FORTIFY_WORKERS")
        .ok()
        .and_then(|v| v.parse().ok())
        .filter(|n| *n >= 1)
        .unwrap_or(1)
}

enum FortifyRunError {
    Cancelled,
    User(String, u16),
    Internal(String),
}

impl From<anyhow::Error> for FortifyRunError {
    fn from(value: anyhow::Error) -> Self {
        FortifyRunError::Internal(value.to_string())
    }
}

fn run_fortify_job(
    session: &Arc<Session>,
    _verbose: bool,
    progress: &ScanProgressHub,
    job: FortifyJob,
) {
    let key = job.progress_key.clone();
    let gen = job.gen;
    match load_fortify_body(session, progress, &job.request, gen) {
        Ok(result) => progress.finish(&key, gen, "done", Some(result), None, None),
        Err(FortifyRunError::Cancelled) => {
            progress.finish(&key, gen, "cancelled", None, None, None);
        }
        Err(FortifyRunError::User(msg, status)) => {
            progress.finish(&key, gen, "error", None, Some(&msg), Some(status));
        }
        Err(FortifyRunError::Internal(msg)) => {
            progress.finish(&key, gen, "error", None, Some(&msg), Some(500));
        }
    }
}

pub fn parse_fortify_request(
    preset_path: PathBuf,
    params: &HashMap<String, String>,
) -> Result<FortifyRequest, FortifyError> {
    let slug_a = params
        .get("a")
        .map(|s| s.trim())
        .filter(|s| !s.is_empty())
        .ok_or_else(|| FortifyError("a query parameter is required".into()))?
        .to_string();
    let slug_b = params
        .get("b")
        .map(|s| s.trim())
        .filter(|s| !s.is_empty())
        .ok_or_else(|| FortifyError("b query parameter is required".into()))?
        .to_string();
    if slug_a == slug_b {
        return Err(FortifyError("a and b must be different sites".into()));
    }
    let preset = load_preset(&preset_path)
        .map_err(|e| FortifyError(format!("load preset: {e}")))?;
    if !preset.sites.contains_key(&slug_a) {
        return Err(FortifyError(format!("unknown site {slug_a}")));
    }
    if !preset.sites.contains_key(&slug_b) {
        return Err(FortifyError(format!("unknown site {slug_b}")));
    }
    Ok(FortifyRequest {
        progress_key: fortify_progress_key(&slug_a, &slug_b),
        preset_path,
        slug_a,
        slug_b,
        exclude_peaks: crate::query_params::parse_exclude_peaks(params),
    })
}

#[derive(Clone)]
struct CatalogPeak {
    slug: String,
    name: String,
    lon: f64,
    lat: f64,
    elev_m: f64,
    hike_difficulty: Option<String>,
    jeep_difficulty: Option<String>,
    access_difficulty: Option<String>,
    hike_m: Option<f64>,
    jeep_m: Option<f64>,
}

#[derive(Clone)]
struct FortifyCandidate {
    slug: String,
    name: String,
    lon: f64,
    lat: f64,
    elev_m: f64,
    candidate_id: String,
    margin_ca: Option<f64>,
    margin_cb: Option<f64>,
    rf_viable: bool,
    along_track: f64,
    leg_a_km: f64,
    leg_b_km: f64,
    hike_difficulty: Option<String>,
    jeep_difficulty: Option<String>,
    access_difficulty: Option<String>,
    hike_m: Option<f64>,
    jeep_m: Option<f64>,
}

impl FortifyCandidate {
    fn weaker_leg_margin_db(&self) -> Option<f64> {
        if !self.rf_viable {
            return None;
        }
        match (self.margin_ca, self.margin_cb) {
            (Some(a), Some(b)) if a.is_finite() && b.is_finite() => Some(a.min(b)),
            _ => None,
        }
    }
}

fn catalog_peak_difficulty(entry: &PeakCatalogEntry) -> (Option<String>, Option<String>, Option<String>) {
    let hike = peaky_preset::derived_hike_difficulty(entry);
    let jeep = peaky_preset::derived_jeep_difficulty(entry);
    let access = match (&hike, &jeep) {
        (Some(h), Some(j)) => Some(worse_difficulty(h, j).to_string()),
        (Some(h), None) => Some(h.clone()),
        (None, Some(j)) => Some(j.clone()),
        (None, None) => None,
    };
    (hike, jeep, access)
}

fn cmp_fortify_rank(a: &FortifyCandidate, b: &FortifyCandidate) -> std::cmp::Ordering {
    b.rf_viable
        .cmp(&a.rf_viable)
        .then_with(|| {
            let ma = a.weaker_leg_margin_db().unwrap_or(f64::NEG_INFINITY);
            let mb = b.weaker_leg_margin_db().unwrap_or(f64::NEG_INFINITY);
            mb.partial_cmp(&ma).unwrap_or(std::cmp::Ordering::Equal)
        })
        .then_with(|| {
            let bal_a = match (a.margin_ca, a.margin_cb) {
                (Some(x), Some(y)) => (x - y).abs(),
                _ => f64::INFINITY,
            };
            let bal_b = match (b.margin_ca, b.margin_cb) {
                (Some(x), Some(y)) => (x - y).abs(),
                _ => f64::INFINITY,
            };
            bal_a
                .partial_cmp(&bal_b)
                .unwrap_or(std::cmp::Ordering::Equal)
        })
        .then_with(|| {
            let mid_a = (a.along_track - 0.5).abs();
            let mid_b = (b.along_track - 0.5).abs();
            mid_a
                .partial_cmp(&mid_b)
                .unwrap_or(std::cmp::Ordering::Equal)
        })
}

fn pair_mutual_margin(
    session: &Session,
    lat_a: f64,
    lon_a: f64,
    tx_a: f64,
    lat_b: f64,
    lon_b: f64,
    tx_b: f64,
    rf_json: &str,
) -> Result<Option<f64>, FortifyRunError> {
    let margins = session
        .seek_repeater_link_margins(lat_a, lon_a, tx_a, &[(lat_b, lon_b, tx_b)], rf_json)
        .map_err(|e| FortifyRunError::User(e.to_string(), 503))?;
    Ok(margins.first().copied().flatten())
}

fn load_fortify_body(
    session: &Arc<Session>,
    progress: &ScanProgressHub,
    req: &FortifyRequest,
    scan_gen: u64,
) -> Result<Value, FortifyRunError> {
    let scan_t0 = Instant::now();
    let key = &req.progress_key;
    let mut ensure_active = || {
        if !progress.active(key, scan_gen) {
            return Err(FortifyRunError::Cancelled);
        }
        Ok(())
    };

    let preset = load_preset(&req.preset_path)?;
    let boards = load_board_viewshed(&req.preset_path);
    let project_hop_m = max_hop_range_m(&preset);

    let site_a = preset
        .sites
        .get(&req.slug_a)
        .ok_or_else(|| FortifyRunError::User(format!("unknown site {}", req.slug_a), 422))?;
    let site_b = preset
        .sites
        .get(&req.slug_b)
        .ok_or_else(|| FortifyRunError::User(format!("unknown site {}", req.slug_b), 422))?;

    let endpoint_a = Endpoint {
        slug: req.slug_a.clone(),
        name: site_a.name.clone(),
        lat: site_a.loc[0],
        lon: site_a.loc[1],
        tx_h: resolved_site_tx_height_m(&preset, site_a).max(1.0),
    };
    let endpoint_b = Endpoint {
        slug: req.slug_b.clone(),
        name: site_b.name.clone(),
        lat: site_b.loc[0],
        lon: site_b.loc[1],
        tx_h: resolved_site_tx_height_m(&preset, site_b).max(1.0),
    };

    let hop_a = site_hop_radius_m(&preset, site_a, Some(&boards));
    let hop_b = site_hop_radius_m(&preset, site_b, Some(&boards));
    let anchors: [HopDiscAnchor; 2] = [
        (endpoint_a.lat, endpoint_a.lon, hop_a),
        (endpoint_b.lat, endpoint_b.lon, hop_b),
    ];
    let scan_bbox = multi_disc_lens_bbox(&anchors);
    if scan_bbox.0 >= scan_bbox.2 || scan_bbox.1 >= scan_bbox.3 {
        return Err(FortifyRunError::User(
            "those sites do not share a one-hop lens".into(),
            422,
        ));
    }

    let rf_json =
        rf_json_for_preset(&preset).map_err(|e| FortifyRunError::User(e.to_string(), 422))?;

    ensure_active()?;
    progress.update(key, scan_gen, "dem", 0, 0, "Loading Skadi DEM…");
    session
        .ensure_tiles_for_bounds(scan_bbox.0, scan_bbox.1, scan_bbox.2, scan_bbox.3)
        .map_err(|e| FortifyRunError::User(e.to_string(), 503))?;

    let original_margin = pair_mutual_margin(
        session,
        endpoint_a.lat,
        endpoint_a.lon,
        endpoint_a.tx_h,
        endpoint_b.lat,
        endpoint_b.lon,
        endpoint_b.tx_h,
        &rf_json,
    )?;

    ensure_active()?;
    progress.update(key, scan_gen, "catalog", 0, 0, "Loading peaks catalog…");

    let catalog = cached_peaks_catalog_thin(&req.preset_path)
        .map_err(|e| FortifyRunError::User(format!("load peaks catalog: {e}"), 422))?;
    let n_catalog_peaks = catalog.entries.len();
    if n_catalog_peaks == 0 {
        return Err(FortifyRunError::User(
            "peaks catalog is empty; run peaky peaks to build peaks/".into(),
            422,
        ));
    }

    let mut peaks: Vec<CatalogPeak> = catalog
        .entries
        .iter()
        .filter(|(_, entry)| !entry.deny.unwrap_or(false))
        .filter(|(slug, _)| !req.exclude_peaks.contains(slug.as_str()))
        .filter_map(|(slug, entry)| {
            let lat = entry.lat();
            let lon = entry.lon();
            if !inside_all_discs(lat, lon, &anchors) {
                return None;
            }
            let (hike_difficulty, jeep_difficulty, access_difficulty) =
                catalog_peak_difficulty(entry);
            Some(CatalogPeak {
                slug: slug.clone(),
                name: entry
                    .name
                    .clone()
                    .unwrap_or_else(|| slug.clone()),
                lon,
                lat,
                elev_m: entry.elev_m.unwrap_or(0.0),
                hike_difficulty,
                jeep_difficulty,
                access_difficulty,
                hike_m: entry.hike_m,
                jeep_m: entry.jeep_m,
            })
        })
        .collect();

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

    let endpoints = [
        (endpoint_a.lat, endpoint_a.lon, endpoint_a.tx_h),
        (endpoint_b.lat, endpoint_b.lon, endpoint_b.tx_h),
    ];

    let mut candidates: Vec<FortifyCandidate> = Vec::with_capacity(peaks.len());
    let mut peak_idx = 0usize;
    for chunk in peaks.chunks(RF_CHUNK) {
        ensure_active()?;
        let points: Vec<(f64, f64)> = chunk
            .iter()
            .map(|p| (p.lat, p.lon))
            .chain(anchors.iter().map(|(lat, lon, _)| (*lat, *lon)))
            .collect();
        session
            .ensure_tiles_for_points(&points, project_hop_m)
            .map_err(|e| FortifyRunError::User(e.to_string(), 503))?;

        for peak in chunk {
            let tx_h = default_candidate_tx_height(&preset, peak.lat, peak.lon);
            let margins = session
                .seek_repeater_link_margins(peak.lat, peak.lon, tx_h, &endpoints, &rf_json)
                .map_err(|e| FortifyRunError::User(e.to_string(), 503))?;
            let m_ca = margins.first().copied().flatten();
            let m_cb = margins.get(1).copied().flatten();
            let rf_viable = mutual_rf_viable(m_ca) && mutual_rf_viable(m_cb);
            if !rf_viable {
                continue;
            }
            let along_track = along_track_t(
                endpoint_a.lat,
                endpoint_a.lon,
                endpoint_b.lat,
                endpoint_b.lon,
                peak.lat,
                peak.lon,
            )
            .unwrap_or(0.5);
            let leg_a_km =
                haversine_m(endpoint_a.lat, endpoint_a.lon, peak.lat, peak.lon) / 1000.0;
            let leg_b_km =
                haversine_m(endpoint_b.lat, endpoint_b.lon, peak.lat, peak.lon) / 1000.0;
            candidates.push(FortifyCandidate {
                slug: peak.slug.clone(),
                name: peak.name.clone(),
                lon: peak.lon,
                lat: peak.lat,
                elev_m: peak.elev_m,
                candidate_id: format!("f{peak_idx}"),
                margin_ca: m_ca,
                margin_cb: m_cb,
                rf_viable,
                along_track,
                leg_a_km,
                leg_b_km,
                hike_difficulty: peak.hike_difficulty.clone(),
                jeep_difficulty: peak.jeep_difficulty.clone(),
                access_difficulty: peak.access_difficulty.clone(),
                hike_m: peak.hike_m,
                jeep_m: peak.jeep_m,
            });
            peak_idx += 1;
        }
    }

    candidates.sort_by(|a, b| cmp_fortify_rank(a, b));

    let (canonical_a, canonical_b) = canonical_site_pair(&req.slug_a, &req.slug_b);
    let link_distance_km =
        (haversine_m(endpoint_a.lat, endpoint_a.lon, endpoint_b.lat, endpoint_b.lon) / 1000.0
            * 10.0)
            .round()
            / 10.0;

    let mut candidate_features = Vec::new();
    let mut line_features = Vec::new();

    for row in &candidates {
        let weaker_margin = row.weaker_leg_margin_db();
        candidate_features.push(json!({
            "type": "Feature",
            "geometry": { "type": "Point", "coordinates": [row.lon, row.lat] },
            "properties": {
                "candidate_id": row.candidate_id,
                "peak_slug": row.slug,
                "slug": row.slug,
                "name": row.name,
                "lat": row.lat,
                "lon": row.lon,
                "elev_m": (row.elev_m * 10.0).round() / 10.0,
                "margin_db": weaker_margin.map(|m| (m * 10.0).round() / 10.0),
                "margin_ca_db": row.margin_ca.map(|m| (m * 10.0).round() / 10.0),
                "margin_cb_db": row.margin_cb.map(|m| (m * 10.0).round() / 10.0),
                "along_track": (row.along_track * 1000.0).round() / 1000.0,
                "split_from_a_pct": (row.along_track * 100.0).round() as i32,
                "leg_a_km": (row.leg_a_km * 10.0).round() / 10.0,
                "leg_b_km": (row.leg_b_km * 10.0).round() / 10.0,
                "split_imbalance_km": ((row.leg_a_km - row.leg_b_km).abs() * 10.0).round() / 10.0,
                "hike_difficulty": row.hike_difficulty,
                "jeep_difficulty": row.jeep_difficulty,
                "access_difficulty": row.access_difficulty,
                "hike_m": row.hike_m.map(|m| (m * 10.0).round() / 10.0),
                "jeep_m": row.jeep_m.map(|m| (m * 10.0).round() / 10.0),
                "rf_viable": row.rf_viable,
            },
        }));

        if !row.rf_viable {
            continue;
        }

        for (leg, ep) in [
            ("a", &endpoint_a),
            ("b", &endpoint_b),
        ] {
            let margin = if leg == "a" { row.margin_ca } else { row.margin_cb };
            let Some(margin) = margin else {
                continue;
            };
            line_features.push(json!({
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        [row.lon, row.lat],
                        [ep.lon, ep.lat],
                    ],
                },
                "properties": {
                    "candidate_id": row.candidate_id,
                    "endpoint_leg": leg,
                    "endpoint_slug": ep.slug,
                    "margin_db": (margin * 10.0).round() / 10.0,
                    "distance_km": (haversine_m(row.lat, row.lon, ep.lat, ep.lon) / 1000.0 * 10.0).round() / 10.0,
                    "rf_viable": true,
                },
            }));
        }
    }

    Ok(json!({
        "link": {
            "a": canonical_a,
            "b": canonical_b,
            "distance_km": link_distance_km,
            "original_margin_db": original_margin
                .filter(|m| m.is_finite())
                .map(|m| (m * 10.0).round() / 10.0),
        },
        "endpoints": [
            {
                "slug": endpoint_a.slug,
                "name": endpoint_a.name,
                "lat": endpoint_a.lat,
                "lon": endpoint_a.lon,
            },
            {
                "slug": endpoint_b.slug,
                "name": endpoint_b.name,
                "lat": endpoint_b.lat,
                "lon": endpoint_b.lon,
            },
        ],
        "candidates": { "type": "FeatureCollection", "features": candidate_features },
        "lines": { "type": "FeatureCollection", "features": line_features },
        "meta": {
            "n_candidates": candidates.len(),
            "hop_range_km": hop_a.min(hop_b) / 1000.0,
            "n_catalog_peaks": n_catalog_peaks,
            "scan_ms": scan_t0.elapsed().as_millis(),
        },
    }))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;
    use std::io::Write;
    use tempfile::TempDir;

    #[test]
    fn rank_prefers_higher_weaker_leg_margin() {
        let strong = FortifyCandidate {
            slug: "p1".into(),
            name: "P1".into(),
            lon: 0.0,
            lat: 0.0,
            elev_m: 100.0,
            candidate_id: "f0".into(),
            margin_ca: Some(8.0),
            margin_cb: Some(8.0),
            rf_viable: true,
            along_track: 0.5,
            leg_a_km: 10.0,
            leg_b_km: 10.0,
            hike_difficulty: None,
            jeep_difficulty: None,
            access_difficulty: None,
            hike_m: None,
            jeep_m: None,
        };
        let weak = FortifyCandidate {
            slug: "p2".into(),
            name: "P2".into(),
            lon: 0.1,
            lat: 0.1,
            elev_m: 100.0,
            candidate_id: "f1".into(),
            margin_ca: Some(3.0),
            margin_cb: Some(3.0),
            rf_viable: true,
            along_track: 0.5,
            leg_a_km: 10.0,
            leg_b_km: 10.0,
            hike_difficulty: None,
            jeep_difficulty: None,
            access_difficulty: None,
            hike_m: None,
            jeep_m: None,
        };
        assert_eq!(cmp_fortify_rank(&strong, &weak), std::cmp::Ordering::Less);

        let in_lens_only = FortifyCandidate {
            slug: "p3".into(),
            name: "P3".into(),
            lon: 0.2,
            lat: 0.2,
            elev_m: 100.0,
            candidate_id: "f2".into(),
            margin_ca: Some(9.0),
            margin_cb: None,
            rf_viable: false,
            along_track: 0.5,
            leg_a_km: 10.0,
            leg_b_km: 10.0,
            hike_difficulty: None,
            jeep_difficulty: None,
            access_difficulty: None,
            hike_m: None,
            jeep_m: None,
        };
        assert_eq!(
            cmp_fortify_rank(&weak, &in_lens_only),
            std::cmp::Ordering::Less
        );
    }

    #[test]
    fn parse_fortify_request_rejects_missing_and_unknown() {
        let dir = TempDir::new().unwrap();
        let preset_path = dir.path().join("config.yaml");
        let mut f = std::fs::File::create(&preset_path).unwrap();
        write!(
            f,
            r#"
sites:
  alpha:
    name: Alpha
    loc: [39.0, -119.0]
  beta:
    name: Beta
    loc: [39.1, -119.0]
simulation:
  radius_km: 50
scan:
  max_candidates: 10
"#
        )
        .unwrap();

        let err = parse_fortify_request(preset_path.clone(), &HashMap::new()).unwrap_err();
        assert!(err.0.contains("a query"));

        let mut params = HashMap::new();
        params.insert("a".into(), "alpha".into());
        let err = parse_fortify_request(preset_path.clone(), &params).unwrap_err();
        assert!(err.0.contains("b query"));

        params.insert("b".into(), "missing".into());
        let err = parse_fortify_request(preset_path.clone(), &params).unwrap_err();
        assert!(err.0.contains("unknown site missing"));

        params.insert("b".into(), "beta".into());
        let req = parse_fortify_request(preset_path, &params).unwrap();
        assert_eq!(req.slug_a, "alpha");
        assert_eq!(req.slug_b, "beta");
        assert_eq!(req.progress_key, "fortify:alpha:beta");
    }
}
