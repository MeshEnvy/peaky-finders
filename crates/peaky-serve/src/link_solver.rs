//! Link solver: multi-hop RF routes between two preset sites via catalog peaks.

use std::collections::hash_map::DefaultHasher;
use std::collections::{HashMap, HashSet};
use std::hash::{Hash, Hasher};
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Condvar, Mutex};
use std::thread;
use std::time::Instant;

use anyhow::Result;
use peaky_preset::{
    load_preset, resolved_preset_cache_dir, worse_difficulty, PeakCatalogEntry, Preset, SiteEntry,
};

use crate::peaks_cache::cached_peaks_catalog_thin;
use serde::Deserialize;
use serde_json::{json, Value};
use splatter::Session;

use crate::geo::bearing_deg;
use crate::links::{canonical_site_pair, load_project_site_links, site_pair_link_detail};
use crate::rf::{
    default_candidate_tx_height, resolved_site_tx_height_m, rf_json_for_preset,
};
use crate::scan_progress::ScanProgressHub;
use crate::sites::{create_site_from_peak, SiteCreateError};
use splatter::propagate::haversine_m;

const ENDPOINT_EXCLUDE_M: f64 = 500.0;
const SITE_PEAK_DEDUP_M: f64 = 500.0;
const RF_CHUNK: usize = 512;
const MAX_PATHS_PER_L: usize = 500;
const MAX_RF_EVALS: u64 = 20_000;
/// More-like-this: include routes that share at least this Jaccard overlap with the seed.
const JACCARD_MORE_LIKE_MIN: f64 = 0.0;

#[derive(Debug, thiserror::Error)]
#[error("{0}")]
pub struct LinkSolverError(pub String);

impl From<SiteCreateError> for LinkSolverError {
    fn from(value: SiteCreateError) -> Self {
        LinkSolverError(value.0)
    }
}

pub fn link_solver_progress_key(slug_a: &str, slug_b: &str) -> String {
    let (a, b) = canonical_site_pair(slug_a, slug_b);
    format!("linksolver:{a}:{b}")
}

#[derive(Debug, Clone)]
pub struct LinkSolverRequest {
    pub progress_key: String,
    pub preset_path: PathBuf,
    pub slug_a: String,
    pub slug_b: String,
    pub min_routes: usize,
}

struct LinkSolverJob {
    progress_key: String,
    gen: u64,
    request: LinkSolverRequest,
}

struct QueueInner {
    pending: HashMap<String, LinkSolverJob>,
}

struct LinkSolverJobQueue {
    inner: Mutex<QueueInner>,
    cv: Condvar,
}

impl LinkSolverJobQueue {
    fn new() -> Self {
        Self {
            inner: Mutex::new(QueueInner {
                pending: HashMap::new(),
            }),
            cv: Condvar::new(),
        }
    }

    fn submit(&self, job: LinkSolverJob) {
        let mut inner = self.inner.lock().unwrap();
        inner.pending.insert(job.progress_key.clone(), job);
        self.cv.notify_one();
    }

    fn take(&self) -> LinkSolverJob {
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
pub struct LinkSolverHub {
    progress: ScanProgressHub,
    queue: Arc<LinkSolverJobQueue>,
}

impl LinkSolverHub {
    pub fn new(session: Arc<Session>, verbose: bool) -> Self {
        let queue = Arc::new(LinkSolverJobQueue::new());
        let progress = ScanProgressHub::default();
        for _ in 0..link_solver_workers() {
            let queue = Arc::clone(&queue);
            let progress = progress.clone();
            let session = Arc::clone(&session);
            thread::spawn(move || {
                loop {
                    let job = queue.take();
                    let key = job.progress_key.clone();
                    let gen = job.gen;
                    let result = catch_unwind(AssertUnwindSafe(|| {
                        run_link_solver_job(&session, verbose, &progress, job);
                    }));
                    if result.is_err() {
                        progress.finish(
                            &key,
                            gen,
                            "error",
                            None,
                            Some("Link solver worker crashed during search or RF scan"),
                            Some(500),
                        );
                    }
                }
            });
        }
        Self { progress, queue }
    }

    pub fn enqueue(&self, request: LinkSolverRequest) -> Result<u64, LinkSolverError> {
        let key = request.progress_key.clone();
        let gen = self.progress.begin(&key);
        self.queue.submit(LinkSolverJob {
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

fn link_solver_workers() -> usize {
    std::env::var("PEAKY_SERVE_LINK_SOLVER_WORKERS")
        .ok()
        .and_then(|v| v.parse().ok())
        .filter(|n| *n >= 1)
        .unwrap_or(1)
}

pub fn parse_link_solver_request(
    preset_path: PathBuf,
    params: &HashMap<String, String>,
) -> Result<LinkSolverRequest, LinkSolverError> {
    let slug_a = params
        .get("a")
        .map(|s| s.trim())
        .filter(|s| !s.is_empty())
        .ok_or_else(|| LinkSolverError("a query parameter is required".into()))?
        .to_string();
    let slug_b = params
        .get("b")
        .map(|s| s.trim())
        .filter(|s| !s.is_empty())
        .ok_or_else(|| LinkSolverError("b query parameter is required".into()))?
        .to_string();
    if slug_a == slug_b {
        return Err(LinkSolverError("a and b must be different sites".into()));
    }
    let min_routes = params
        .get("min_routes")
        .and_then(|s| s.parse::<usize>().ok())
        .unwrap_or(5)
        .max(1);
    let preset = load_preset(&preset_path)
        .map_err(|e| LinkSolverError(format!("load preset: {e}")))?;
    if !preset.sites.contains_key(&slug_a) {
        return Err(LinkSolverError(format!("unknown site {slug_a}")));
    }
    if !preset.sites.contains_key(&slug_b) {
        return Err(LinkSolverError(format!("unknown site {slug_b}")));
    }
    if pair_linked_in_mesh(&preset_path, &preset, &slug_a, &slug_b)? {
        return Err(LinkSolverError(format!(
            "sites {slug_a} and {slug_b} are already linked"
        )));
    }
    Ok(LinkSolverRequest {
        progress_key: link_solver_progress_key(&slug_a, &slug_b),
        preset_path,
        slug_a,
        slug_b,
        min_routes,
    })
}

pub fn mesh_pair_linked(mesh: &Value, slug_a: &str, slug_b: &str) -> bool {
    let (canonical_a, canonical_b) = canonical_site_pair(slug_a, slug_b);
    let Some(rows) = mesh.get("links").and_then(|v| v.as_array()) else {
        return false;
    };
    for row in rows {
        let a = row.get("a").and_then(|v| v.as_str()).unwrap_or("");
        let b = row.get("b").and_then(|v| v.as_str()).unwrap_or("");
        if a == canonical_a && b == canonical_b {
            return row.get("linked").and_then(|v| v.as_bool()).unwrap_or(false);
        }
    }
    false
}

pub fn pair_linked_in_mesh(
    preset_path: &Path,
    preset: &Preset,
    slug_a: &str,
    slug_b: &str,
) -> Result<bool, LinkSolverError> {
    let mesh = load_project_site_links(preset_path, preset)
        .map_err(|e| LinkSolverError(format!("load site links: {e}")))?;
    if mesh
        .get("links")
        .and_then(|v| v.as_array())
        .map(|a| a.is_empty())
        .unwrap_or(true)
    {
        return Ok(false);
    }
    Ok(mesh_pair_linked(&mesh, slug_a, slug_b))
}

pub fn pair_linked_with_session(
    session: &Session,
    preset_path: &Path,
    preset: &Preset,
    slug_a: &str,
    slug_b: &str,
) -> Result<bool, LinkSolverError> {
    if pair_linked_in_mesh(preset_path, preset, slug_a, slug_b)? {
        return Ok(true);
    }
    let mesh = load_project_site_links(preset_path, preset)
        .map_err(|e| LinkSolverError(format!("load site links: {e}")))?;
    let mesh_empty = mesh
        .get("links")
        .and_then(|v| v.as_array())
        .map(|a| a.is_empty())
        .unwrap_or(true);
    if !mesh_empty {
        return Ok(false);
    }
    let detail = site_pair_link_detail(session, preset, slug_a, slug_b)
        .map_err(|e| LinkSolverError(e.0))?;
    Ok(detail.get("margin_db").and_then(|v| v.as_f64()).is_some())
}

enum LinkSolverRunError {
    Cancelled,
    User(String, u16),
    Internal(String),
}

impl From<anyhow::Error> for LinkSolverRunError {
    fn from(value: anyhow::Error) -> Self {
        LinkSolverRunError::Internal(value.to_string())
    }
}

impl From<LinkSolverError> for LinkSolverRunError {
    fn from(value: LinkSolverError) -> Self {
        LinkSolverRunError::User(value.0, 422)
    }
}

fn run_link_solver_job(
    session: &Arc<Session>,
    _verbose: bool,
    progress: &ScanProgressHub,
    job: LinkSolverJob,
) {
    let key = job.progress_key.clone();
    let gen = job.gen;
    match load_link_solver_body(session, progress, &job.request, gen) {
        Ok(result) => progress.finish(&key, gen, "done", Some(result), None, None),
        Err(LinkSolverRunError::Cancelled) => {
            progress.finish(&key, gen, "cancelled", None, None, None);
        }
        Err(LinkSolverRunError::User(msg, status)) => {
            progress.finish(&key, gen, "error", None, Some(&msg), Some(status));
        }
        Err(LinkSolverRunError::Internal(msg)) => {
            progress.finish(&key, gen, "error", None, Some(&msg), Some(500));
        }
    }
}

fn hop_m_from_preset(preset: &Preset) -> f64 {
    match &preset.simulation.radius_km {
        serde_yaml::Value::Number(n) => n.as_f64().unwrap_or(50.0) * 1000.0,
        serde_yaml::Value::String(s) => s.parse::<f64>().unwrap_or(50.0) * 1000.0,
        _ => 50_000.0,
    }
}

fn l0_hops(dist_ab_m: f64, hop_m: f64) -> usize {
    ((dist_ab_m / hop_m).ceil() as usize).max(1)
}

fn max_hops_for_l0(l0: usize) -> usize {
    (l0 + 4).min(8)
}

/// Node N is on the A–B ellipse for hop budget L when dist(A,N)+dist(N,B) <= L·hop_m.
pub fn ellipse_admissible(
    a_lat: f64,
    a_lon: f64,
    b_lat: f64,
    b_lon: f64,
    n_lat: f64,
    n_lon: f64,
    l: usize,
    hop_m: f64,
) -> bool {
    let dist_an = haversine_m(a_lat, a_lon, n_lat, n_lon);
    let dist_nb = haversine_m(n_lat, n_lon, b_lat, b_lon);
    dist_an + dist_nb <= l as f64 * hop_m + 1.0
}

/// At depth d from A along an L-hop path, dist(N,B) must be <= (L-d)·hop_m.
pub fn depth_admissible_from_a(
    b_lat: f64,
    b_lon: f64,
    n_lat: f64,
    n_lon: f64,
    l: usize,
    depth_from_a: usize,
    hop_m: f64,
) -> bool {
    if depth_from_a > l {
        return false;
    }
    let remaining = l - depth_from_a;
    haversine_m(n_lat, n_lon, b_lat, b_lon) <= remaining as f64 * hop_m + 1.0
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum NodeRole {
    EndpointA,
    EndpointB,
    Peak,
}

#[derive(Clone)]
struct GraphNode {
    role: NodeRole,
    slug: String,
    name: String,
    lat: f64,
    lon: f64,
    tx_h: f64,
    elev_m: f64,
    hike_difficulty: Option<String>,
    jeep_difficulty: Option<String>,
    access_difficulty: Option<String>,
    hike_m: Option<f64>,
    jeep_m: Option<f64>,
}

#[derive(Clone)]
struct RouteLeg {
    from_slug: String,
    to_slug: String,
    margin_db: f64,
    distance_km: f64,
    bearing_deg: f64,
}

#[derive(Clone)]
pub struct Route {
    pub route_id: String,
    pub hops: usize,
    pub bottleneck_db: f64,
    pub sum_margins_db: f64,
    pub total_km: f64,
    pub unique: bool,
    pub similar_to: Option<String>,
    pub peak_slugs: Vec<String>,
    pub node_path: Vec<usize>,
    pub legs: Vec<RouteLeg>,
}

pub trait EdgeOracle {
    fn margins(
        &mut self,
        from_lat: f64,
        from_lon: f64,
        from_tx: f64,
        targets: &[(f64, f64, f64)],
    ) -> Result<Vec<Option<f64>>, String>;
}

fn rf_digest(rf_json: &str) -> String {
    let mut hasher = DefaultHasher::new();
    rf_json.hash(&mut hasher);
    format!("{:016x}", hasher.finish())
}

fn pair_cache_key(rf_digest: &str, slug_a: &str, slug_b: &str) -> String {
    let (a, b) = canonical_site_pair(slug_a, slug_b);
    format!("{rf_digest}:{a}:{b}")
}

struct PairMarginCache {
    memory: HashMap<String, Option<f64>>,
    disk_path: PathBuf,
    rf_digest: String,
    cache_hits: u64,
    rf_evals: u64,
    dirty: bool,
}

impl PairMarginCache {
    fn new(preset_path: &Path, rf_json: &str) -> Self {
        let digest = rf_digest(rf_json);
        let disk_path = resolved_preset_cache_dir(preset_path)
            .join("link-solver/pairs.json");
        let mut memory = HashMap::new();
        if disk_path.is_file() {
            if let Ok(raw) = std::fs::read_to_string(&disk_path) {
                if let Ok(wire) = serde_json::from_str::<Value>(&raw) {
                    if wire.get("rf_digest").and_then(|v| v.as_str()) == Some(&digest) {
                        if let Some(obj) = wire.get("pairs").and_then(|v| v.as_object()) {
                            for (k, v) in obj {
                                let margin = v
                                    .get("margin_db")
                                    .and_then(|m| m.as_f64())
                                    .or_else(|| {
                                        if v.get("missing").and_then(|m| m.as_bool()) == Some(true) {
                                            None
                                        } else {
                                            None
                                        }
                                    });
                                memory.insert(k.clone(), margin);
                            }
                        }
                    }
                }
            }
        }
        Self {
            memory,
            disk_path,
            rf_digest: digest,
            cache_hits: 0,
            rf_evals: 0,
            dirty: false,
        }
    }

    fn get(&mut self, slug_a: &str, slug_b: &str) -> Option<Option<f64>> {
        let key = pair_cache_key(&self.rf_digest, slug_a, slug_b);
        if let Some(v) = self.memory.get(&key) {
            self.cache_hits += 1;
            return Some(*v);
        }
        None
    }

    fn insert(&mut self, slug_a: &str, slug_b: &str, margin: Option<f64>) {
        let key = pair_cache_key(&self.rf_digest, slug_a, slug_b);
        self.memory.insert(key, margin);
        self.dirty = true;
    }

    fn persist(&self) -> Result<()> {
        if !self.dirty {
            return Ok(());
        }
        if let Some(parent) = self.disk_path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let mut pairs = serde_json::Map::new();
        for (k, margin) in &self.memory {
            pairs.insert(
                k.clone(),
                match margin {
                    Some(m) => json!({ "margin_db": m }),
                    None => json!({ "missing": true }),
                },
            );
        }
        let wire = json!({
            "rf_digest": self.rf_digest,
            "pairs": pairs,
        });
        let tmp = self.disk_path.with_extension("json.tmp");
        std::fs::write(&tmp, serde_json::to_string(&wire)?)?;
        std::fs::rename(&tmp, &self.disk_path)?;
        Ok(())
    }
}

struct SessionEdgeOracle<'a> {
    session: &'a Session,
    rf_json: String,
    cache: PairMarginCache,
    slug_lookup: HashMap<(i64, i64), (String, String)>,
}

impl<'a> SessionEdgeOracle<'a> {
    fn new(session: &'a Session, preset_path: &Path, rf_json: &str, nodes: &[GraphNode]) -> Self {
        let mut slug_lookup = HashMap::new();
        for node in nodes {
            let key = ((node.lat * 1e7).round() as i64, (node.lon * 1e7).round() as i64);
            slug_lookup.insert(key, (node.slug.clone(), node.slug.clone()));
        }
        Self {
            session,
            rf_json: rf_json.to_string(),
            cache: PairMarginCache::new(preset_path, rf_json),
            slug_lookup,
        }
    }

    fn slug_at(&self, lat: f64, lon: f64) -> String {
        let key = ((lat * 1e7).round() as i64, (lon * 1e7).round() as i64);
        self.slug_lookup
            .get(&key)
            .map(|(s, _)| s.clone())
            .unwrap_or_else(|| format!("{lat:.5},{lon:.5}"))
    }

    fn cache_stats(&self) -> (u64, u64) {
        (self.cache.cache_hits, self.cache.rf_evals)
    }

    fn persist_cache(&mut self) -> Result<()> {
        self.cache.persist()
    }
}

impl EdgeOracle for SessionEdgeOracle<'_> {
    fn margins(
        &mut self,
        from_lat: f64,
        from_lon: f64,
        from_tx: f64,
        targets: &[(f64, f64, f64)],
    ) -> Result<Vec<Option<f64>>, String> {
        let from_slug = self.slug_at(from_lat, from_lon);
        let mut out = Vec::with_capacity(targets.len());
        let mut pending: Vec<(usize, f64, f64, f64)> = Vec::new();
        for (i, (lat, lon, tx)) in targets.iter().enumerate() {
            let to_slug = self.slug_at(*lat, *lon);
            if let Some(cached) = self.cache.get(&from_slug, &to_slug) {
                out.push(cached);
            } else {
                out.push(None);
                pending.push((i, *lat, *lon, *tx));
            }
        }
        if pending.is_empty() {
            return Ok(out);
        }
        if self.cache.rf_evals + pending.len() as u64 > MAX_RF_EVALS {
            return Err("RF evaluation budget exceeded".into());
        }
        for chunk in pending.chunks(RF_CHUNK) {
            let batch: Vec<(f64, f64, f64)> = chunk
                .iter()
                .map(|(_, lat, lon, tx)| (*lat, *lon, *tx))
                .collect();
            let margins = self
                .session
                .seek_repeater_link_margins(from_lat, from_lon, from_tx, &batch, &self.rf_json)
                .map_err(|e| e.to_string())?;
            self.cache.rf_evals += batch.len() as u64;
            for ((i, lat, lon, _), margin) in chunk.iter().zip(margins.into_iter()) {
                let to_slug = self.slug_at(*lat, *lon);
                self.cache.insert(&from_slug, &to_slug, margin);
                out[*i] = margin;
            }
        }
        Ok(out)
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

fn route_id_for_peaks(peak_slugs: &[String]) -> String {
    let mut hasher = DefaultHasher::new();
    peak_slugs.hash(&mut hasher);
    format!("r{:016x}", hasher.finish())
}

pub fn jaccard_similarity(a: &[String], b: &[String]) -> f64 {
    if a.is_empty() && b.is_empty() {
        return 1.0;
    }
    let set_a: HashSet<&str> = a.iter().map(String::as_str).collect();
    let set_b: HashSet<&str> = b.iter().map(String::as_str).collect();
    let inter = set_a.intersection(&set_b).count();
    let union = set_a.union(&set_b).count();
    if union == 0 {
        1.0
    } else {
        inter as f64 / union as f64
    }
}

pub fn shared_peak_count(a: &[String], b: &[String]) -> usize {
    let set_a: HashSet<&str> = a.iter().map(String::as_str).collect();
    b.iter()
        .filter(|s| set_a.contains(s.as_str()))
        .count()
}

pub fn routes_share_peaks(a: &[String], b: &[String]) -> bool {
    shared_peak_count(a, b) > 0
}

pub fn pick_diverse_routes(ranked: Vec<Route>, cap: usize) -> Vec<Route> {
    let mut unique: Vec<Route> = Vec::new();
    let mut out: Vec<Route> = Vec::new();
    for mut route in ranked {
        if out.len() >= cap {
            break;
        }
        let mut similar_to: Option<String> = None;
        for kept in &unique {
            if routes_share_peaks(&route.peak_slugs, &kept.peak_slugs) {
                similar_to = Some(kept.route_id.clone());
                break;
            }
        }
        if let Some(id) = similar_to {
            route.unique = false;
            route.similar_to = Some(id);
        } else {
            route.unique = true;
            unique.push(route.clone());
        }
        out.push(route);
    }
    out
}

pub fn count_unique_routes(routes: &[Route]) -> usize {
    routes.iter().filter(|r| r.unique).count()
}

pub fn relay_tags(site_a: &SiteEntry, site_b: &SiteEntry) -> (Vec<String>, Option<String>) {
    let set_a: HashSet<&str> = site_a.tags.iter().map(String::as_str).collect();
    let set_b: HashSet<&str> = site_b.tags.iter().map(String::as_str).collect();
    let mut intersection: Vec<String> = set_a
        .intersection(&set_b)
        .map(|s| (*s).to_string())
        .collect();
    intersection.sort();
    if !intersection.is_empty() {
        return (intersection, None);
    }
    let mut union: HashSet<String> = site_a.tags.iter().cloned().collect();
    union.extend(site_b.tags.iter().cloned());
    let mut tags: Vec<String> = union.into_iter().collect();
    tags.sort();
    (tags, Some("union-fallback".into()))
}

fn cmp_route_rank(a: &Route, b: &Route) -> std::cmp::Ordering {
    a.hops
        .cmp(&b.hops)
        .then_with(|| {
            a.total_km
                .partial_cmp(&b.total_km)
                .unwrap_or(std::cmp::Ordering::Equal)
        })
}

fn build_route(
    nodes: &[GraphNode],
    node_path: &[usize],
    margins: &[Option<f64>],
) -> Option<Route> {
    if node_path.len() < 2 || margins.len() != node_path.len() - 1 {
        return None;
    }
    let mut legs = Vec::new();
    let mut peak_slugs = Vec::new();
    let mut total_km = 0.0;
    let mut sum_margins = 0.0;
    let mut bottleneck = f64::INFINITY;
    for i in 0..node_path.len() - 1 {
        let from = &nodes[node_path[i]];
        let to = &nodes[node_path[i + 1]];
        let margin = margins[i]?;
        let dist_km = haversine_m(from.lat, from.lon, to.lat, to.lon) / 1000.0;
        total_km += dist_km;
        sum_margins += margin;
        bottleneck = bottleneck.min(margin);
        if to.role == NodeRole::Peak {
            peak_slugs.push(to.slug.clone());
        }
        legs.push(RouteLeg {
            from_slug: from.slug.clone(),
            to_slug: to.slug.clone(),
            margin_db: margin,
            distance_km: dist_km,
            bearing_deg: bearing_deg(from.lat, from.lon, to.lat, to.lon),
        });
    }
    Some(Route {
        route_id: route_id_for_peaks(&peak_slugs),
        hops: node_path.len() - 1,
        bottleneck_db: bottleneck,
        sum_margins_db: sum_margins,
        total_km,
        unique: true,
        similar_to: None,
        peak_slugs,
        node_path: node_path.to_vec(),
        legs,
    })
}

fn within_hop(from: &GraphNode, to: &GraphNode, hop_m: f64) -> bool {
    haversine_m(from.lat, from.lon, to.lat, to.lon) <= hop_m + 1.0
}

fn expand_layer(
    oracle: &mut dyn EdgeOracle,
    nodes: &[GraphNode],
    hop_m: f64,
    current: &HashMap<usize, Vec<Vec<usize>>>,
    max_paths: usize,
    admissible_peaks: Option<&HashSet<usize>>,
) -> Result<HashMap<usize, Vec<Vec<usize>>>, String> {
    let mut next: HashMap<usize, Vec<Vec<usize>>> = HashMap::new();
    let mut total = 0usize;
    'outer: for (from_idx, paths) in current {
        let from = &nodes[*from_idx];
        for path in paths {
            let in_path: HashSet<usize> = path.iter().copied().collect();
            let mut candidates: Vec<usize> = Vec::new();
            for (to_idx, to) in nodes.iter().enumerate() {
                if in_path.contains(&to_idx) {
                    continue;
                }
                if to.role == NodeRole::Peak {
                    if let Some(admissible) = admissible_peaks {
                        if !admissible.contains(&to_idx) {
                            continue;
                        }
                    }
                }
                if !within_hop(from, to, hop_m) {
                    continue;
                }
                candidates.push(to_idx);
            }
            if candidates.is_empty() {
                continue;
            }
            let targets: Vec<(f64, f64, f64)> = candidates
                .iter()
                .map(|i| (nodes[*i].lat, nodes[*i].lon, nodes[*i].tx_h))
                .collect();
            let margins = oracle.margins(from.lat, from.lon, from.tx_h, &targets)?;
            for (to_idx, margin) in candidates.iter().zip(margins.into_iter()) {
                if margin.is_none() {
                    continue;
                }
                let mut new_path = path.clone();
                new_path.push(*to_idx);
                next.entry(*to_idx).or_default().push(new_path);
                total += 1;
                if total >= max_paths {
                    break 'outer;
                }
            }
        }
    }
    Ok(next)
}

fn find_paths_for_l(
    oracle: &mut dyn EdgeOracle,
    nodes: &[GraphNode],
    hop_m: f64,
    l: usize,
    admissible_peaks: Option<&HashSet<usize>>,
) -> Result<Vec<Route>, String> {
    if l == 0 {
        return Ok(Vec::new());
    }
    let idx_a = 0usize;
    let idx_b = 1usize;
    let mut forward: HashMap<usize, Vec<Vec<usize>>> = HashMap::new();
    forward.insert(idx_a, vec![vec![idx_a]]);
    let mut backward: HashMap<usize, Vec<Vec<usize>>> = HashMap::new();
    backward.insert(idx_b, vec![vec![idx_b]]);

    let mut forward_layers = vec![forward];
    let mut backward_layers = vec![backward];

    for _depth in 1..=l {
        let prev = forward_layers.last().unwrap().clone();
        let layer = expand_layer(
            oracle,
            nodes,
            hop_m,
            &prev,
            MAX_PATHS_PER_L,
            admissible_peaks,
        )?;
        forward_layers.push(layer);
        let prev = backward_layers.last().unwrap().clone();
        let layer = expand_layer(
            oracle,
            nodes,
            hop_m,
            &prev,
            MAX_PATHS_PER_L,
            admissible_peaks,
        )?;
        backward_layers.push(layer);
    }

    let mut routes = Vec::new();
    for d_f in 0..=l {
        let d_b = l - d_f;
        let f_layer = &forward_layers[d_f];
        let b_layer = &backward_layers[d_b];
        for (meet_idx, f_paths) in f_layer {
            let Some(b_paths) = b_layer.get(meet_idx) else {
                continue;
            };
            if *meet_idx != idx_a && *meet_idx != idx_b {
                if nodes[*meet_idx].role != NodeRole::Peak {
                    continue;
                }
            }
            for f_path in f_paths {
                for b_path in b_paths {
                    let mut node_path = f_path.clone();
                    if d_b > 0 {
                        let mut tail: Vec<usize> = b_path.iter().rev().skip(1).copied().collect();
                        node_path.append(&mut tail);
                    }
                    if node_path.first() != Some(&idx_a) || node_path.last() != Some(&idx_b) {
                        continue;
                    }
                    if node_path.len() != l + 1 {
                        continue;
                    }
                    let unique: HashSet<usize> = node_path.iter().copied().collect();
                    if unique.len() != node_path.len() {
                        continue;
                    }
                    let mut margins = Vec::new();
                    let mut ok = true;
                    for i in 0..node_path.len() - 1 {
                        let from = &nodes[node_path[i]];
                        let to = &nodes[node_path[i + 1]];
                        let m = oracle.margins(
                            from.lat,
                            from.lon,
                            from.tx_h,
                            &[(to.lat, to.lon, to.tx_h)],
                        )?;
                        if m.first().copied().flatten().is_none() {
                            ok = false;
                            break;
                        }
                        margins.push(m[0]);
                    }
                    if !ok {
                        continue;
                    }
                    if let Some(route) = build_route(nodes, &node_path, &margins) {
                        routes.push(route);
                        if routes.len() >= MAX_PATHS_PER_L {
                            return Ok(routes);
                        }
                    }
                }
            }
        }
    }
    Ok(routes)
}

pub fn routes_like_cached(cached_routes: &[Route], route_id: &str) -> Vec<Route> {
    let Some(seed) = cached_routes.iter().find(|r| r.route_id == route_id) else {
        return Vec::new();
    };
    let mut similar: Vec<Route> = cached_routes
        .iter()
        .filter(|r| r.hops == seed.hops && r.route_id != seed.route_id)
        .filter(|r| {
            r.peak_slugs != seed.peak_slugs
                && jaccard_similarity(&r.peak_slugs, &seed.peak_slugs) > JACCARD_MORE_LIKE_MIN
        })
        .cloned()
        .collect();
    similar.sort_by(|a, b| {
        let shared_b = shared_peak_count(&b.peak_slugs, &seed.peak_slugs);
        let shared_a = shared_peak_count(&a.peak_slugs, &seed.peak_slugs);
        shared_b
            .cmp(&shared_a)
            .then_with(|| cmp_route_rank(a, b))
    });
    similar.truncate(10);
    similar
}

fn route_peak_slugs_from_json(route: &Value) -> Vec<String> {
    if let Some(rows) = route.get("peak_slugs").and_then(|v| v.as_array()) {
        return rows
            .iter()
            .filter_map(|v| v.as_str().map(str::to_string))
            .collect();
    }
    route
        .get("peaks")
        .and_then(|v| v.as_array())
        .map(|rows| {
            rows.iter()
                .filter_map(|row| {
                    row.as_str()
                        .map(str::to_string)
                        .or_else(|| {
                            row.get("peak_slug")
                                .or_else(|| row.get("slug"))
                                .and_then(|s| s.as_str())
                                .map(str::to_string)
                        })
                })
                .collect()
        })
        .unwrap_or_default()
}

pub fn routes_like_from_json(routes: &[Value], route_id: &str) -> Vec<Value> {
    let Some(seed) = routes
        .iter()
        .find(|r| r.get("route_id").and_then(|v| v.as_str()) == Some(route_id))
    else {
        return Vec::new();
    };
    let seed_hops = seed.get("hops").and_then(|v| v.as_u64()).unwrap_or(0);
    let seed_peaks = route_peak_slugs_from_json(seed);
    let mut similar: Vec<Value> = routes
        .iter()
        .filter(|r| {
            r.get("route_id").and_then(|v| v.as_str()) != Some(route_id)
                && r.get("hops").and_then(|v| v.as_u64()) == Some(seed_hops)
        })
        .filter(|r| {
            let peaks = route_peak_slugs_from_json(r);
            peaks != seed_peaks && jaccard_similarity(&peaks, &seed_peaks) > JACCARD_MORE_LIKE_MIN
        })
        .cloned()
        .collect();
    similar.sort_by(|a, b| {
        let peaks_a = route_peak_slugs_from_json(a);
        let peaks_b = route_peak_slugs_from_json(b);
        let shared_b = shared_peak_count(&peaks_b, &seed_peaks);
        let shared_a = shared_peak_count(&peaks_a, &seed_peaks);
        shared_b.cmp(&shared_a).then_with(|| {
            let ha = a.get("hops").and_then(|v| v.as_u64()).unwrap_or(0);
            let hb = b.get("hops").and_then(|v| v.as_u64()).unwrap_or(0);
            ha.cmp(&hb).then_with(|| {
                let ka = a.get("total_km").and_then(|v| v.as_f64()).unwrap_or(0.0);
                let kb = b.get("total_km").and_then(|v| v.as_f64()).unwrap_or(0.0);
                ka.partial_cmp(&kb).unwrap_or(std::cmp::Ordering::Equal)
            })
        })
    });
    similar.truncate(10);
    similar
}

fn load_link_solver_body(
    session: &Arc<Session>,
    progress: &ScanProgressHub,
    req: &LinkSolverRequest,
    scan_gen: u64,
) -> Result<Value, LinkSolverRunError> {
    let scan_t0 = Instant::now();
    let key = &req.progress_key;
    let mut ensure_active = || {
        if !progress.active(key, scan_gen) {
            return Err(LinkSolverRunError::Cancelled);
        }
        Ok(())
    };

    let preset = load_preset(&req.preset_path)?;
    if pair_linked_with_session(session, &req.preset_path, &preset, &req.slug_a, &req.slug_b)? {
        return Err(LinkSolverRunError::User(
            format!("sites {} and {} are already linked", req.slug_a, req.slug_b),
            422,
        ));
    }

    let hop_m = hop_m_from_preset(&preset);
    let site_a = preset
        .sites
        .get(&req.slug_a)
        .ok_or_else(|| LinkSolverRunError::User(format!("unknown site {}", req.slug_a), 422))?;
    let site_b = preset
        .sites
        .get(&req.slug_b)
        .ok_or_else(|| LinkSolverRunError::User(format!("unknown site {}", req.slug_b), 422))?;

    let a_lat = site_a.loc[0];
    let a_lon = site_a.loc[1];
    let b_lat = site_b.loc[0];
    let b_lon = site_b.loc[1];
    let dist_ab_m = haversine_m(a_lat, a_lon, b_lat, b_lon);
    let l0 = l0_hops(dist_ab_m, hop_m);
    let max_l = max_hops_for_l0(l0);

    let rf_json =
        rf_json_for_preset(&preset).map_err(|e| LinkSolverRunError::User(e.to_string(), 422))?;

    ensure_active()?;
    progress.update(key, scan_gen, "dem", 0, 0, "Loading Skadi DEM…");
    let pad = hop_m * max_l as f64;
    session
        .ensure_tiles_for_points(&[(a_lat, a_lon), (b_lat, b_lon)], pad)
        .map_err(|e| LinkSolverRunError::User(e.to_string(), 503))?;

    ensure_active()?;
    progress.update(key, scan_gen, "catalog", 0, 0, "Loading peaks catalog…");

    let catalog = cached_peaks_catalog_thin(&req.preset_path)
        .map_err(|e| LinkSolverRunError::User(format!("load peaks catalog: {e}"), 422))?;
    let n_catalog = catalog.entries.len();

    let mut peak_nodes: Vec<GraphNode> = Vec::new();
    for (slug, entry) in &catalog.entries {
        if entry.deny.unwrap_or(false) {
            continue;
        }
        let lat = entry.lat();
        let lon = entry.lon();
        if haversine_m(lat, lon, a_lat, a_lon) <= ENDPOINT_EXCLUDE_M {
            continue;
        }
        if haversine_m(lat, lon, b_lat, b_lon) <= ENDPOINT_EXCLUDE_M {
            continue;
        }
        if preset.sites.values().any(|site| {
            haversine_m(lat, lon, site.loc[0], site.loc[1]) <= SITE_PEAK_DEDUP_M
        }) {
            continue;
        }
        let (hike_difficulty, jeep_difficulty, access_difficulty) = catalog_peak_difficulty(entry);
        peak_nodes.push(GraphNode {
            role: NodeRole::Peak,
            slug: slug.clone(),
            name: entry
                .name
                .clone()
                .unwrap_or_else(|| slug.clone()),
            lat,
            lon,
            tx_h: default_candidate_tx_height(&preset, lat, lon),
            elev_m: entry.elev_m.unwrap_or(0.0),
            hike_difficulty,
            jeep_difficulty,
            access_difficulty,
            hike_m: entry.hike_m,
            jeep_m: entry.jeep_m,
        });
    }

    let mut nodes = vec![
        GraphNode {
            role: NodeRole::EndpointA,
            slug: req.slug_a.clone(),
            name: site_a.name.clone(),
            lat: a_lat,
            lon: a_lon,
            tx_h: resolved_site_tx_height_m(&preset, site_a).max(1.0),
            elev_m: 0.0,
            hike_difficulty: None,
            jeep_difficulty: None,
            access_difficulty: None,
            hike_m: None,
            jeep_m: None,
        },
        GraphNode {
            role: NodeRole::EndpointB,
            slug: req.slug_b.clone(),
            name: site_b.name.clone(),
            lat: b_lat,
            lon: b_lon,
            tx_h: resolved_site_tx_height_m(&preset, site_b).max(1.0),
            elev_m: 0.0,
            hike_difficulty: None,
            jeep_difficulty: None,
            access_difficulty: None,
            hike_m: None,
            jeep_m: None,
        },
    ];
    nodes.extend(peak_nodes);

    progress.update(
        key,
        scan_gen,
        "catalog",
        nodes.len().saturating_sub(2) as i32,
        n_catalog.max(1) as i32,
        &format!("{} catalog peak(s) eligible as relays", nodes.len().saturating_sub(2)),
    );

    let mut oracle = SessionEdgeOracle::new(session, &req.preset_path, &rf_json, &nodes);
    let cap = preset.scan.max_candidates as usize;
    let mut diverse: Vec<Route> = Vec::new();
    let mut max_hops_searched = l0;
    let mut truncated = false;

    for l in l0..=max_l {
        ensure_active()?;
        max_hops_searched = l;
        progress.update(
            key,
            scan_gen,
            "search",
            l as i32 - l0 as i32 + 1,
            (max_l - l0 + 1) as i32,
            &format!("Searching {l}-hop routes…"),
        );

        let admissible: HashSet<usize> = (2..nodes.len())
            .filter(|&idx| {
                let n = &nodes[idx];
                ellipse_admissible(a_lat, a_lon, b_lat, b_lon, n.lat, n.lon, l, hop_m)
            })
            .collect();

        let paths = find_paths_for_l(&mut oracle, &nodes, hop_m, l, Some(&admissible))
            .map_err(|e| LinkSolverRunError::User(e, 503))?;

        let mut ranked = paths;
        ranked.sort_by(cmp_route_rank);
        let picked = pick_diverse_routes(ranked, cap.saturating_sub(diverse.len()));
        diverse.extend(picked);
        if diverse.len() >= MAX_PATHS_PER_L {
            truncated = true;
        }
        if count_unique_routes(&diverse) >= req.min_routes {
            break;
        }
    }

    let _ = oracle.persist_cache();
    let (cache_hits, rf_evals) = oracle.cache_stats();

    diverse.sort_by(cmp_route_rank);

    let mut line_features = Vec::new();
    let mut peak_features = Vec::new();
    let mut peak_seen: HashSet<String> = HashSet::new();
    let mut route_rows = Vec::new();

    for route in &diverse {
        let coords: Vec<[f64; 2]> = route
            .node_path
            .iter()
            .filter_map(|&idx| nodes.get(idx).map(|n| [n.lon, n.lat]))
            .collect();
        if coords.len() < 2 {
            continue;
        }
        line_features.push(json!({
            "type": "Feature",
            "geometry": { "type": "LineString", "coordinates": coords },
            "properties": {
                "route_id": route.route_id,
                "hops": route.hops,
                "bottleneck_db": (route.bottleneck_db * 10.0).round() / 10.0,
                "total_km": (route.total_km * 10.0).round() / 10.0,
                "unique": route.unique,
                "similar_to": route.similar_to,
            },
        }));

        for &idx in &route.node_path {
            if idx < 2 {
                continue;
            }
            let peak = &nodes[idx];
            if !peak_seen.insert(peak.slug.clone()) {
                continue;
            }
            peak_features.push(json!({
                "type": "Feature",
                "geometry": { "type": "Point", "coordinates": [peak.lon, peak.lat] },
                "properties": {
                    "peak_slug": peak.slug,
                    "name": peak.name,
                    "lat": peak.lat,
                    "lon": peak.lon,
                    "elev_m": (peak.elev_m * 10.0).round() / 10.0,
                    "hike_difficulty": peak.hike_difficulty,
                    "jeep_difficulty": peak.jeep_difficulty,
                    "access_difficulty": peak.access_difficulty,
                    "hike_m": peak.hike_m.map(|m| (m * 10.0).round() / 10.0),
                    "jeep_m": peak.jeep_m.map(|m| (m * 10.0).round() / 10.0),
                },
            }));
        }

        let legs_json: Vec<Value> = route
            .legs
            .iter()
            .map(|leg| {
                json!({
                    "from": leg.from_slug,
                    "to": leg.to_slug,
                    "margin_db": (leg.margin_db * 10.0).round() / 10.0,
                    "distance_km": (leg.distance_km * 10.0).round() / 10.0,
                    "bearing_deg": (leg.bearing_deg * 10.0).round() / 10.0,
                })
            })
            .collect();

        let peaks_meta: Vec<Value> = route
            .peak_slugs
            .iter()
            .filter_map(|slug| {
                nodes.iter().find(|n| n.slug == *slug).map(|p| {
                    json!({
                        "peak_slug": p.slug,
                        "name": p.name,
                        "lat": p.lat,
                        "lon": p.lon,
                        "elev_m": (p.elev_m * 10.0).round() / 10.0,
                    })
                })
            })
            .collect();

        route_rows.push(json!({
            "route_id": route.route_id,
            "hops": route.hops,
            "bottleneck_db": (route.bottleneck_db * 10.0).round() / 10.0,
            "total_km": (route.total_km * 10.0).round() / 10.0,
            "unique": route.unique,
            "similar_to": route.similar_to,
            "legs": legs_json,
            "peaks": peaks_meta,
        }));
    }

    let (canonical_a, canonical_b) = canonical_site_pair(&req.slug_a, &req.slug_b);

    Ok(json!({
        "endpoints": [
            {
                "slug": req.slug_a,
                "name": site_a.name,
                "lat": a_lat,
                "lon": a_lon,
            },
            {
                "slug": req.slug_b,
                "name": site_b.name,
                "lat": b_lat,
                "lon": b_lon,
            },
        ],
        "routes": route_rows,
        "lines": { "type": "FeatureCollection", "features": line_features },
        "peaks": { "type": "FeatureCollection", "features": peak_features },
        "meta": {
            "a": canonical_a,
            "b": canonical_b,
            "l0": l0,
            "max_hops_searched": max_hops_searched,
            "truncated": truncated,
            "rf_evals": rf_evals,
            "cache_hits": cache_hits,
            "scan_ms": scan_t0.elapsed().as_millis(),
            "n_routes": diverse.len(),
            "hop_range_km": hop_m / 1000.0,
        },
    }))
}

#[derive(Debug, Deserialize)]
struct AcceptHop {
    peak_slug: String,
    lat: f64,
    lon: f64,
    name: Option<String>,
}

#[derive(Debug, Deserialize)]
struct AcceptLinkSolverBody {
    a: String,
    b: String,
    route_id: String,
    hops: Vec<AcceptHop>,
}

pub fn accept_link_solver_route(
    preset_path: &Path,
    session: &Session,
    body: Value,
) -> Result<Value, LinkSolverError> {
    let body: AcceptLinkSolverBody = serde_json::from_value(body)
        .map_err(|e| LinkSolverError(format!("invalid accept body: {e}")))?;
    let preset = load_preset(preset_path)
        .map_err(|e| LinkSolverError(format!("load preset: {e}")))?;
    let site_a = preset
        .sites
        .get(&body.a)
        .ok_or_else(|| LinkSolverError(format!("unknown site {}", body.a)))?;
    let site_b = preset
        .sites
        .get(&body.b)
        .ok_or_else(|| LinkSolverError(format!("unknown site {}", body.b)))?;
    if pair_linked_with_session(session, preset_path, &preset, &body.a, &body.b)? {
        return Err(LinkSolverError(format!(
            "sites {} and {} are already linked",
            body.a, body.b
        )));
    }
    let (tags, tag_policy) = relay_tags(site_a, site_b);
    let mut created = Vec::new();
    let mut path_slugs = vec![body.a.clone()];
    for (i, hop) in body.hops.iter().enumerate() {
        let catalog_name = cached_peaks_catalog_thin(preset_path)
            .ok()
            .and_then(|c| c.entries.get(&hop.peak_slug).and_then(|e| e.name.clone()));
        let name = hop.name.clone().or(catalog_name).unwrap_or_else(|| {
            format!("{}–{} relay {}", site_a.name, site_b.name, i + 1)
        });
        let (slug, entry) = create_site_from_peak(
            preset_path,
            name,
            hop.lat,
            hop.lon,
            tags.clone(),
            &hop.peak_slug,
        )?;
        created.push(json!({
            "slug": slug,
            "name": entry.name,
            "lat": entry.loc[0],
            "lon": entry.loc[1],
            "preferred_slug": hop.peak_slug,
        }));
        path_slugs.push(slug);
    }
    path_slugs.push(body.b.clone());
    let created_slugs: Vec<String> = created
        .iter()
        .filter_map(|v| v.get("slug").and_then(|s| s.as_str()).map(str::to_string))
        .collect();
    Ok(json!({
        "route_id": body.route_id,
        "created": created,
        "created_slugs": created_slugs,
        "path_slugs": path_slugs,
        "tag_policy": tag_policy,
        "a": body.a,
        "b": body.b,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;
    use std::io::Write;
    use tempfile::TempDir;

    #[test]
    fn ellipse_admissibility_respects_hop_budget() {
        let a = (39.0, -119.0);
        let b = (39.2, -119.0);
        let mid = (39.1, -119.0);
        let far = (40.0, -119.0);
        let hop_m = 50_000.0;
        assert!(ellipse_admissible(a.0, a.1, b.0, b.1, mid.0, mid.1, 2, hop_m));
        assert!(!ellipse_admissible(a.0, a.1, b.0, b.1, far.0, far.1, 2, hop_m));
        assert!(depth_admissible_from_a(b.0, b.1, mid.0, mid.1, 2, 1, hop_m));
        assert!(!depth_admissible_from_a(b.0, b.1, far.0, far.1, 2, 1, hop_m));
    }

    struct GridOracle {
        hop_m: f64,
        evals: u64,
    }

    impl EdgeOracle for GridOracle {
        fn margins(
            &mut self,
            from_lat: f64,
            from_lon: f64,
            _from_tx: f64,
            targets: &[(f64, f64, f64)],
        ) -> Result<Vec<Option<f64>>, String> {
            self.evals += targets.len() as u64;
            Ok(targets
                .iter()
                .map(|(lat, lon, _)| {
                    let d = haversine_m(from_lat, from_lon, *lat, *lon);
                    if d <= self.hop_m {
                        Some(10.0 - d / self.hop_m)
                    } else {
                        None
                    }
                })
                .collect())
        }
    }

    #[test]
    fn synthetic_oracle_finds_two_hop_paths_without_cycles() {
        let hop_m = 40_000.0;
        let nodes = vec![
            GraphNode {
                role: NodeRole::EndpointA,
                slug: "a".into(),
                name: "A".into(),
                lat: 39.0,
                lon: -119.0,
                tx_h: 2.0,
                elev_m: 0.0,
                hike_difficulty: None,
                jeep_difficulty: None,
                access_difficulty: None,
                hike_m: None,
                jeep_m: None,
            },
            GraphNode {
                role: NodeRole::EndpointB,
                slug: "b".into(),
                name: "B".into(),
                lat: 39.15,
                lon: -119.0,
                tx_h: 2.0,
                elev_m: 0.0,
                hike_difficulty: None,
                jeep_difficulty: None,
                access_difficulty: None,
                hike_m: None,
                jeep_m: None,
            },
            GraphNode {
                role: NodeRole::Peak,
                slug: "p1".into(),
                name: "P1".into(),
                lat: 39.05,
                lon: -119.0,
                tx_h: 2.0,
                elev_m: 100.0,
                hike_difficulty: None,
                jeep_difficulty: None,
                access_difficulty: None,
                hike_m: None,
                jeep_m: None,
            },
            GraphNode {
                role: NodeRole::Peak,
                slug: "p2".into(),
                name: "P2".into(),
                lat: 39.1,
                lon: -119.0,
                tx_h: 2.0,
                elev_m: 100.0,
                hike_difficulty: None,
                jeep_difficulty: None,
                access_difficulty: None,
                hike_m: None,
                jeep_m: None,
            },
        ];
        let mut oracle = GridOracle { hop_m, evals: 0 };
        let routes = find_paths_for_l(&mut oracle, &nodes, hop_m, 2, None).unwrap();
        assert!(!routes.is_empty());
        for route in &routes {
            assert_eq!(route.hops, 2);
            assert_eq!(route.peak_slugs.len(), 1);
            assert!(route.node_path.windows(2).all(|w| w[0] != w[1]));
        }
    }

    #[test]
    fn jaccard_diversity_picker_keeps_dissimilar_routes() {
        let mk = |id: &str, peaks: &[&str]| Route {
            route_id: id.into(),
            hops: peaks.len(),
            bottleneck_db: 5.0,
            sum_margins_db: 10.0,
            total_km: 20.0,
            unique: true,
            similar_to: None,
            peak_slugs: peaks.iter().map(|s| s.to_string()).collect(),
            node_path: vec![],
            legs: vec![],
        };
        let ranked = vec![
            mk("r1", &["p1", "p2"]),
            mk("r2", &["p1", "p2"]),
            mk("r3", &["p3", "p4"]),
        ];
        let picked = pick_diverse_routes(ranked, 10);
        assert_eq!(picked.len(), 3);
        assert_eq!(count_unique_routes(&picked), 2);
        assert_eq!(picked.iter().filter(|r| !r.unique).count(), 1);
    }

    #[test]
    fn route_rank_prefers_fewer_hops_then_shorter_distance() {
        let mk = |hops: usize, km: f64| Route {
            route_id: format!("r{hops}-{km}"),
            hops,
            bottleneck_db: 10.0,
            sum_margins_db: 30.0,
            total_km: km,
            unique: true,
            similar_to: None,
            peak_slugs: vec![],
            node_path: vec![],
            legs: vec![],
        };
        let mut routes = vec![mk(4, 50.0), mk(3, 90.0), mk(3, 80.0), mk(2, 100.0)];
        routes.sort_by(cmp_route_rank);
        assert_eq!(routes[0].hops, 2);
        assert_eq!(routes[1].hops, 3);
        assert_eq!(routes[1].total_km, 80.0);
        assert_eq!(routes[2].total_km, 90.0);
        assert_eq!(routes[3].hops, 4);
    }

    #[test]
    fn shared_hop_routes_are_not_both_unique() {
        let mk = |id: &str, peaks: &[&str]| Route {
            route_id: id.into(),
            hops: peaks.len() + 1,
            bottleneck_db: 13.0,
            sum_margins_db: 30.0,
            total_km: 84.0,
            unique: true,
            similar_to: None,
            peak_slugs: peaks.iter().map(|s| s.to_string()).collect(),
            node_path: vec![],
            legs: vec![],
        };
        let ranked = vec![
            mk("r1", &["hop-a", "hop-b"]),
            mk("r2", &["hop-c", "hop-b"]),
        ];
        let picked = pick_diverse_routes(ranked, 10);
        assert!(picked[0].unique);
        assert!(!picked[1].unique);
        assert_eq!(picked[1].similar_to.as_deref(), Some("r1"));
    }

    #[test]
    fn relay_tags_intersection_and_union_fallback() {
        let a = SiteEntry {
            name: "A".into(),
            loc: [0.0, 0.0],
            tags: vec!["mesh".into(), "north".into()],
            height_m: None,
            description: None,
            node: None,
        };
        let b = SiteEntry {
            name: "B".into(),
            loc: [0.0, 0.0],
            tags: vec!["mesh".into(), "south".into()],
            height_m: None,
            description: None,
            node: None,
        };
        let (tags, policy) = relay_tags(&a, &b);
        assert_eq!(tags, vec!["mesh".to_string()]);
        assert!(policy.is_none());

        let c = SiteEntry {
            tags: vec!["alpha".into()],
            ..a.clone()
        };
        let (tags, policy) = relay_tags(&c, &b);
        assert!(tags.contains(&"alpha".to_string()));
        assert!(tags.contains(&"mesh".to_string()));
        assert_eq!(policy.as_deref(), Some("union-fallback"));
    }

    #[test]
    fn mesh_pair_linked_and_parse_rejects_when_linked() {
        let mesh = json!({
            "links": [
                { "a": "alpha", "b": "beta", "linked": true, "manual": false, "strength": "strong" }
            ],
        });
        assert!(mesh_pair_linked(&mesh, "beta", "alpha"));

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

        let mut params = HashMap::new();
        params.insert("a".into(), "alpha".into());
        params.insert("b".into(), "beta".into());
        let req = parse_link_solver_request(preset_path.clone(), &params).unwrap();
        assert_eq!(req.progress_key, "linksolver:alpha:beta");

        assert!(!mesh_pair_linked(&json!({"links": []}), "alpha", "beta"));

        let linked_mesh = json!({
            "links": [{ "a": "alpha", "b": "beta", "linked": true }],
        });
        assert!(mesh_pair_linked(&linked_mesh, "alpha", "beta"));
    }

}
