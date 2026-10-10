//! Background viewshed coverage queue and project warm scheduler.

use std::cmp::Reverse;
use std::collections::{BinaryHeap, HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use anyhow::Result;
use peaky_peaks::{
    ensure_osm_pbf, invalidate_osm_routing_caches, load_routing_for_points_progress,
    osm_routing_opts_from_preset, routing_bbox_for_points,
    routing_coverage_bbox,     inventory_all_geojson_bytes, routing_network_drive_geojson_bytes,
    routing_network_layer_geojson_bytes, warm_place_access_with_routing, NetworkLayer,
    NetworkViewBBox, NetworkViewCacheKey, OsmParsePhase, OsmRoutingOpts, ProjectRouting,
    OSM_ROUTE_PAD_DEG,
};
use peaky_preset::{
    load_preset, resolved_viewshed_root, BoardViewshedResolver, Preset, SiteEntry,
};
use serde_json::{json, Value};
use splatter::{propagate::required_tile_names_for_points, PRIORITY_DEM_BACKGROUND, PRIORITY_DEM_VIEWPORT, Session};

use crate::events::ServeEventHub;
use crate::links::{
    store_project_site_links_cache, warm_project_site_links,
};
use crate::peaks::access_payload_json;
use crate::rf::{max_hop_range_m, preset_to_request_with_sim, viewshed_workspace_digest};
use crate::viewshed::{
    coords_viewshed_overlay_if_ready, ensure_viewshed_progressive_for_coords_blocking,
    ensure_viewshed_progressive_for_site_blocking, preview_site_at, target_viewshed_digest_for_site,
    DRAFT_VIEWSHED_SLUG,
};
use crate::viewshed_index::site_viewshed_overlay_if_ready;
use crate::viewshed_log::viewshed_progress_log;
use crate::network_geo_cache::NetworkGeoByteCache;
use crate::viewshed_sim::ViewshedSimOverrides;

pub const PRIORITY_INTERACTIVE: i32 = 0;
pub const PRIORITY_VIEWPORT: i32 = 10;
pub const PRIORITY_LINK_ADJACENT: i32 = 20;
pub const PRIORITY_BACKGROUND: i32 = 100;

const LINKS_REFRESH_DEBOUNCE_MS: u64 = 2000;

/// Reuse the cached graph when the point plus this pad still sits inside it.
/// Jeep cap is ~20 mi (~0.29°); 0.35° keeps a margin without forcing a rebuild.
const OSM_COVER_PAD_DEG: f64 = 0.35;

struct CachedOsmRouting {
    routing: Arc<ProjectRouting>,
    /// `WarmHub::osm_epoch` when this graph was published. A roads save bumps the epoch so an in-flight parse cannot republish a stale graph.
    epoch: u64,
    west: f64,
    south: f64,
    east: f64,
    north: f64,
}

impl CachedOsmRouting {
    fn covers(&self, lat: f64, lon: f64, pad_deg: f64) -> bool {
        lon - pad_deg >= self.west
            && lon + pad_deg <= self.east
            && lat - pad_deg >= self.south
            && lat + pad_deg <= self.north
    }
}

#[derive(Clone, Debug)]
struct OsmStatus {
    status: String,
    phase: String,
    step: u32,
    total: u32,
    fraction: f64,
    error: Option<String>,
}

impl Default for OsmStatus {
    fn default() -> Self {
        Self {
            status: "idle".into(),
            phase: String::new(),
            step: 0,
            total: 4,
            fraction: 0.0,
            error: None,
        }
    }
}

impl OsmStatus {
    fn json(&self) -> Value {
        json!({
            "status": self.status,
            "phase": self.phase,
            "step": self.step,
            "total": self.total,
            "fraction": (self.fraction * 100.0).round() / 100.0,
            "error": self.error,
        })
    }
}

type JobFn = Box<dyn FnOnce() + Send>;

struct JobState {
    priority: i32,
    seq: u64,
    cancelled: bool,
    job: Option<JobFn>,
}

struct CoverageQueueInner {
    heap: BinaryHeap<(Reverse<(i32, u64)>, String)>,
    jobs: HashMap<String, JobState>,
    inflight: HashSet<String>,
    seq: u64,
}

pub struct CoverageQueue {
    inner: Mutex<CoverageQueueInner>,
    cv: Condvar,
}

impl CoverageQueue {
    fn new() -> Self {
        Self {
            inner: Mutex::new(CoverageQueueInner {
                heap: BinaryHeap::new(),
                jobs: HashMap::new(),
                inflight: HashSet::new(),
                seq: 0,
            }),
            cv: Condvar::new(),
        }
    }

    fn depth(&self) -> usize {
        let inner = self.inner.lock().unwrap();
        inner.heap.len() + inner.inflight.len()
    }

    fn submit(&self, key: String, priority: i32, job: JobFn) -> bool {
        let mut inner = self.inner.lock().unwrap();
        if inner.inflight.contains(&key) {
            Self::maybe_bump_locked(&mut inner, &key, priority);
            self.cv.notify_one();
            return false;
        }
        if inner.jobs.contains_key(&key) {
            Self::maybe_bump_locked(&mut inner, &key, priority);
            self.cv.notify_one();
            return false;
        }
        inner.seq += 1;
        let seq = inner.seq;
        inner.jobs.insert(
            key.clone(),
            JobState {
                priority,
                seq,
                cancelled: false,
                job: Some(job),
            },
        );
        inner.heap.push((Reverse((priority, seq)), key));
        self.cv.notify_one();
        true
    }

    fn maybe_bump_locked(inner: &mut CoverageQueueInner, key: &str, priority: i32) {
        let should_bump = inner
            .jobs
            .get(key)
            .map(|state| priority < state.priority)
            .unwrap_or(false);
        if !should_bump {
            return;
        }
        inner.seq += 1;
        let seq = inner.seq;
        if let Some(state) = inner.jobs.get_mut(key) {
            state.priority = priority;
            state.seq = seq;
            inner
                .heap
                .push((Reverse((priority, seq)), key.to_string()));
        }
    }

    fn bump(&self, key: &str, priority: i32) -> bool {
        let mut inner = self.inner.lock().unwrap();
        let had = inner.jobs.contains_key(key);
        Self::maybe_bump_locked(&mut inner, key, priority);
        if had {
            self.cv.notify_one();
        }
        had
    }

    fn pop_job(&self) -> Option<(String, JobFn)> {
        loop {
            let mut inner = self.inner.lock().unwrap();
            while inner.heap.is_empty() {
                inner = self.cv.wait(inner).unwrap();
            }
            let (_, key) = inner.heap.pop()?;
            let Some(state) = inner.jobs.get(&key) else {
                continue;
            };
            if state.cancelled {
                inner.jobs.remove(&key);
                continue;
            }
            let top = inner.heap.peek().map(|(Reverse((p, s)), _)| (*p, *s));
            if let Some((p, s)) = top {
                if p < state.priority || (p == state.priority && s < state.seq) {
                    let pr = state.priority;
                    let sq = state.seq;
                    inner.heap.push((Reverse((pr, sq)), key.clone()));
                    continue;
                }
            }
            inner.inflight.insert(key.clone());
            let mut state = inner.jobs.remove(&key).unwrap();
            let job = state.job.take()?;
            drop(inner);
            return Some((key, job));
        }
    }

    fn finish(&self, key: &str) {
        let mut inner = self.inner.lock().unwrap();
        inner.inflight.remove(key);
        self.cv.notify_all();
    }
}

struct ProjectWarm {
    slug: String,
    preset_path: PathBuf,
    queue: Arc<CoverageQueue>,
    warm_running: AtomicBool,
    links_refresh_gen: AtomicU64,
    links_phase: Mutex<LinksPhase>,
}

#[derive(Clone, Debug)]
struct LinksPhase {
    phase: String,
    done: usize,
    total: usize,
    detail: String,
}

impl Default for LinksPhase {
    fn default() -> Self {
        Self {
            phase: "idle".into(),
            done: 0,
            total: 0,
            detail: String::new(),
        }
    }
}

#[derive(Clone)]
pub struct WarmHub {
    session: Arc<Session>,
    events: ServeEventHub,
    verbose: bool,
    coverage_workers: usize,
    projects: Arc<Mutex<HashMap<String, Arc<ProjectWarm>>>>,
    workers_started: Arc<AtomicBool>,
    /// Project-wide OSM routing graph (lazy; shared across site access warms).
    osm_routing: Arc<Mutex<Option<CachedOsmRouting>>>,
    /// Bumped on every roads save. In-flight parses publish only if it is unchanged.
    osm_epoch: Arc<AtomicU64>,
    /// Single-flight lock so prefetch and HTTP warm share one PBF parse.
    osm_build: Arc<Mutex<()>>,
    osm_status: Arc<Mutex<OsmStatus>>,
    osm_last_pub: Arc<Mutex<Option<Instant>>>,
    project_slug: String,
    board_viewshed: Arc<BoardViewshedResolver>,
    network_geo_cache: Arc<Mutex<NetworkGeoByteCache>>,
}

impl WarmHub {
    pub fn new(
        session: Arc<Session>,
        events: ServeEventHub,
        verbose: bool,
        coverage_workers: usize,
        board_viewshed: Arc<BoardViewshedResolver>,
        project_slug: String,
    ) -> Self {
        Self {
            session,
            events,
            verbose,
            coverage_workers: coverage_workers.max(1),
            projects: Arc::new(Mutex::new(HashMap::new())),
            workers_started: Arc::new(AtomicBool::new(false)),
            osm_routing: Arc::new(Mutex::new(None)),
            osm_epoch: Arc::new(AtomicU64::new(0)),
            osm_build: Arc::new(Mutex::new(())),
            osm_status: Arc::new(Mutex::new(OsmStatus::default())),
            osm_last_pub: Arc::new(Mutex::new(None)),
            project_slug,
            board_viewshed,
            network_geo_cache: Arc::new(Mutex::new(NetworkGeoByteCache::new())),
        }
    }

    /// Viewport-clipped GeoJSON for one access-network layer.
    pub fn roads_network_layer_geojson_bytes(
        &self,
        preset_path: &Path,
        layer: NetworkLayer,
        view: NetworkViewBBox,
        zoom: f64,
    ) -> Result<Vec<u8>> {
        let routing = self.ensure_osm_covering(preset_path, &[])?;
        let cache_key =
            NetworkViewCacheKey::from_view(view.west, view.south, view.east, view.north, zoom);
        if let Some(hit) = self
            .network_geo_cache
            .lock()
            .unwrap()
            .get_layer(&routing, layer, cache_key)
        {
            return Ok((*hit).clone());
        }
        let opts = osm_routing_opts_from_preset(preset_path);
        let bytes = routing_network_layer_geojson_bytes(&routing, &opts, layer, view, zoom)
            .map_err(anyhow::Error::msg)?;
        self.network_geo_cache
            .lock()
            .unwrap()
            .put_layer(&routing, layer, cache_key, bytes.clone());
        Ok(bytes)
    }

    /// Paved + jeep GeoJSON in one response body (`{ "paved", "jeep" }`).
    pub fn roads_network_drive_geojson_bytes(
        &self,
        preset_path: &Path,
        view: NetworkViewBBox,
        zoom: f64,
    ) -> Result<Vec<u8>> {
        let routing = self.ensure_osm_covering(preset_path, &[])?;
        let cache_key =
            NetworkViewCacheKey::from_view(view.west, view.south, view.east, view.north, zoom);
        if let Some(hit) = self
            .network_geo_cache
            .lock()
            .unwrap()
            .get_drive(&routing, cache_key)
        {
            return Ok((*hit).clone());
        }
        let opts = osm_routing_opts_from_preset(preset_path);
        let bytes = routing_network_drive_geojson_bytes(&routing, &opts, view, zoom)
            .map_err(anyhow::Error::msg)?;
        self.network_geo_cache
            .lock()
            .unwrap()
            .put_drive(&routing, cache_key, bytes.clone());
        Ok(bytes)
    }

    /// Full OSM + GTLF inventory for the map "All" overlay (no zoom thinning).
    pub fn roads_network_all_geojson_bytes(
        &self,
        preset_path: &Path,
        view: NetworkViewBBox,
    ) -> Result<Vec<u8>> {
        let routing = self.ensure_osm_covering(preset_path, &[])?;
        let cache_key = NetworkViewCacheKey::from_view(
            view.west,
            view.south,
            view.east,
            view.north,
            -1.0,
        );
        if let Some(hit) = self
            .network_geo_cache
            .lock()
            .unwrap()
            .get_all(&routing, cache_key)
        {
            return Ok((*hit).clone());
        }
        let bytes = inventory_all_geojson_bytes(
            &routing.osm_inventory,
            &routing.gtlf_inventory,
            &routing.roads_customs,
            view,
        )
        .map_err(anyhow::Error::msg)?;
        self.network_geo_cache
            .lock()
            .unwrap()
            .put_all(&routing, cache_key, bytes.clone());
        Ok(bytes)
    }

    fn ensure_workers(&self, queue: Arc<CoverageQueue>) {
        if self.workers_started.swap(true, Ordering::SeqCst) {
            return;
        }
        let workers = self.coverage_workers;
        for i in 0..workers {
            let q = queue.clone();
            thread::Builder::new()
                .name(format!("coverage-queue-{i}"))
                .spawn(move || {
                    loop {
                        let Some((key, job)) = q.pop_job() else {
                            continue;
                        };
                        job();
                        q.finish(&key);
                    }
                })
                .expect("spawn coverage worker");
        }
    }

    fn project(&self, slug: &str, preset_path: PathBuf) -> Arc<ProjectWarm> {
        let mut map = self.projects.lock().unwrap();
        if let Some(p) = map.get(slug) {
            return p.clone();
        }
        let queue = Arc::new(CoverageQueue::new());
        self.ensure_workers(queue.clone());
        let warm = Arc::new(ProjectWarm {
            slug: slug.to_string(),
            preset_path,
            queue,
            warm_running: AtomicBool::new(false),
            links_refresh_gen: AtomicU64::new(0),
            links_phase: Mutex::new(LinksPhase::default()),
        });
        map.insert(slug.to_string(), warm.clone());
        warm
    }

    fn site_coverage_key(
        &self,
        _preset_path: &PathBuf,
        preset: &Preset,
        site: &SiteEntry,
    ) -> Result<String> {
        target_viewshed_digest_for_site(preset, site, Some(self.board_viewshed.as_ref()))
    }

    fn site_coverage_exists(preset_path: &PathBuf, key: &str) -> bool {
        let png = resolved_viewshed_root(preset_path).join(key).join("splat.png");
        png.is_file()
    }

    fn overlay_is_at_target(overlay: &Value) -> bool {
        overlay.get("at_target").and_then(|v| v.as_bool()) == Some(true)
    }

    fn publish_viewshed(&self, project_slug: &str, overlay: Value) {
        let mut data = overlay.as_object().cloned().unwrap_or_default();
        data.insert("project".into(), json!(project_slug));
        data.insert("status".into(), json!("ready"));
        self.events.publish(project_slug, "viewshed", Value::Object(data));
    }

    fn publish_links(&self, project_slug: &str, payload: Value) {
        let mut data = payload.as_object().cloned().unwrap_or_default();
        data.insert("project".into(), json!(project_slug));
        self.events.publish(project_slug, "links", Value::Object(data));
    }

    fn set_links_phase(&self, warm: &ProjectWarm, phase: LinksPhase) {
        *warm.links_phase.lock().unwrap() = phase;
    }

    fn schedule_links_refresh(&self, warm: Arc<ProjectWarm>) {
        let gen = warm.links_refresh_gen.fetch_add(1, Ordering::SeqCst) + 1;
        let hub = self.clone();
        thread::spawn(move || {
            thread::sleep(Duration::from_millis(LINKS_REFRESH_DEBOUNCE_MS));
            if warm.links_refresh_gen.load(Ordering::SeqCst) != gen {
                return;
            }
            if let Err(e) = hub.refresh_project_links_blocking(warm) {
                tracing::warn!("links refresh: {e:#}");
            }
        });
    }

    fn refresh_project_links_blocking(&self, warm: Arc<ProjectWarm>) -> Result<()> {
        self.set_links_phase(
            warm.as_ref(),
            LinksPhase {
                phase: "running".into(),
                done: 0,
                total: 0,
                detail: "RF links".into(),
            },
        );
        let preset_path = warm.preset_path.clone();
        let session = self.session.clone();
        let slug = warm.slug.clone();
        let preset = load_preset(&preset_path)?;
        let payload = warm_project_site_links(session, &preset_path, &preset)?;
        store_project_site_links_cache(&warm.preset_path, &preset, &payload)?;
        let mut wire = payload.as_object().cloned().unwrap_or_default();
        wire.insert("project".into(), json!(slug));
        self.events.publish(&slug, "links", Value::Object(wire));
        self.set_links_phase(warm.as_ref(), LinksPhase::default());
        Ok(())
    }

    fn warm_site_job(
        &self,
        warm: Arc<ProjectWarm>,
        site_slug: &str,
        site: &SiteEntry,
        warm_access: bool,
    ) {
        let preset_path = warm.preset_path.clone();
        let session = self.session.clone();
        let slug = warm.slug.clone();
        let site_slug = site_slug.to_string();
        let site = site.clone();
        let hub = self.clone();

        let preset = match load_preset(&preset_path) {
            Ok(p) => p,
            Err(e) => {
                tracing::warn!("viewshed warm {slug}/{site_slug}: {e:#}");
                hub.events.publish(
                    &slug,
                    "viewshed",
                    json!({
                        "project": slug,
                        "slug": site_slug,
                        "status": "error",
                        "error": e.to_string(),
                    }),
                );
                return;
            }
        };

        viewshed_progress_log(&format!("warm start {slug}/{site_slug}"));

        let viewshed_needed = match site_viewshed_overlay_if_ready(
            &slug,
            &preset_path,
            &site_slug,
            &site,
            &preset,
            None,
            Some(hub.board_viewshed.as_ref()),
        ) {
            Ok(Some(overlay)) => !Self::overlay_is_at_target(&overlay),
            _ => true,
        };

        if viewshed_needed {
            if let Err(e) = ensure_viewshed_progressive_for_site_blocking(
                session.clone(),
                &slug,
                &preset_path,
                &preset,
                &site_slug,
                &site,
                Some(hub.board_viewshed.as_ref()),
                hub.verbose,
                |overlay| hub.publish_viewshed(&slug, overlay),
            ) {
                tracing::warn!("viewshed warm {slug}/{site_slug}: {e:#}");
                hub.events.publish(
                    &slug,
                    "viewshed",
                    json!({ "project": slug, "slug": site_slug, "status": "error", "error": e.to_string() }),
                );
            }
        }

        // Access is warmed only via GET/POST place access (`warm=1`), not viewshed jobs.
        let _ = warm_access;
    }

    pub fn osm_status_json(&self) -> Value {
        let mut payload = self.osm_status.lock().unwrap().json();
        if let Some(obj) = payload.as_object_mut() {
            obj.insert("project".into(), json!(self.project_slug));
        }
        payload
    }

    fn publish_osm_progress(&self, phase: OsmParsePhase, fraction: f64, force: bool) {
        {
            let mut last = self.osm_last_pub.lock().unwrap();
            if !force {
                if let Some(prev) = *last {
                    if prev.elapsed() < Duration::from_millis(250) {
                        return;
                    }
                }
            }
            *last = Some(Instant::now());
        }
        let status = if phase == OsmParsePhase::Ready {
            "ready"
        } else {
            "parsing"
        };
        let snap = OsmStatus {
            status: status.into(),
            phase: phase.label().into(),
            step: phase.step(),
            total: phase.total(),
            fraction: fraction.clamp(0.0, 1.0),
            error: None,
        };
        *self.osm_status.lock().unwrap() = snap.clone();
        let mut payload = snap.json();
        if let Some(obj) = payload.as_object_mut() {
            obj.insert("project".into(), json!(self.project_slug));
        }
        self.events.publish(&self.project_slug, "osm", payload);
    }

    fn publish_osm_error(&self, error: &str) {
        let snap = OsmStatus {
            status: "error".into(),
            phase: "error".into(),
            step: 0,
            total: 4,
            fraction: 0.0,
            error: Some(error.to_string()),
        };
        *self.osm_status.lock().unwrap() = snap.clone();
        let mut payload = snap.json();
        if let Some(obj) = payload.as_object_mut() {
            obj.insert("project".into(), json!(self.project_slug));
        }
        self.events.publish(&self.project_slug, "osm", payload);
    }

    fn cached_osm_if_covers(&self, extra: &[(f64, f64)]) -> Option<Arc<ProjectRouting>> {
        let epoch = self.osm_epoch.load(Ordering::Acquire);
        let guard = self.osm_routing.lock().unwrap();
        let cached = guard.as_ref()?;
        if cached.epoch != epoch {
            return None;
        }
        if extra.is_empty() || extra.iter().all(|&(lat, lon)| cached.covers(lat, lon, OSM_COVER_PAD_DEG))
        {
            Some(cached.routing.clone())
        } else {
            None
        }
    }

    /// OSM graph covering booked sites plus ``extra`` points (0.5° pad).
    /// Reuses the in-process cache when it already covers the extras.
    pub fn ensure_osm_covering(
        &self,
        preset_path: &Path,
        extra: &[(f64, f64)],
    ) -> Result<Arc<ProjectRouting>> {
        if let Some(routing) = self.cached_osm_if_covers(extra) {
            return Ok(routing);
        }
        let _build = self.osm_build.lock().unwrap();
        if let Some(routing) = self.cached_osm_if_covers(extra) {
            return Ok(routing);
        }
        let started = self.osm_epoch.load(Ordering::Acquire);
        let preset = load_preset(preset_path)?;
        let mut points: Vec<(f64, f64)> = preset.sites.values().map(|s| (s.lat(), s.lon())).collect();
        points.extend_from_slice(extra);
        let project_dir = preset_path
            .parent()
            .ok_or_else(|| anyhow::anyhow!("preset path has no parent"))?;
        let meta = preset.resolved_access_meta();
        let opts = OsmRoutingOpts::from(&meta);
        let (west, south, east, north) = routing_bbox_for_points(&points, OSM_ROUTE_PAD_DEG)
            .ok_or_else(|| anyhow::anyhow!("no points for OSM routing"))?;
        self.publish_osm_progress(OsmParsePhase::Cache, 0.08, true);
        let hub = self.clone();
        let routing = match load_routing_for_points_progress(
            project_dir,
            &points,
            OSM_ROUTE_PAD_DEG,
            false,
            &opts,
            &mut |phase, frac| hub.publish_osm_progress(phase, frac, phase == OsmParsePhase::Ready),
        ) {
            Ok(r) => Arc::new(r),
            Err(e) => {
                self.publish_osm_error(&e.to_string());
                return Err(e);
            }
        };
        if self.osm_epoch.load(Ordering::Acquire) != started {
            drop(_build);
            return self.ensure_osm_covering(preset_path, extra);
        }
        let (cwest, csouth, ceast, cnorth) = routing_coverage_bbox(west, south, east, north);
        let cached = CachedOsmRouting {
            routing: routing.clone(),
            epoch: started,
            west: cwest,
            south: csouth,
            east: ceast,
            north: cnorth,
        };
        *self.osm_routing.lock().unwrap() = Some(cached);
        self.publish_osm_progress(OsmParsePhase::Ready, 1.0, true);
        Ok(routing)
    }

    /// Clear in-memory OSM routing and on-disk PBF-side caches after ``roads.yaml`` changes.
    pub fn invalidate_osm_routing(&self, preset_path: &Path) -> anyhow::Result<()> {
        self.osm_epoch.fetch_add(1, Ordering::AcqRel);
        *self.osm_routing.lock().unwrap() = None;
        self.network_geo_cache.lock().unwrap().clear();
        let project_dir = preset_path
            .parent()
            .ok_or_else(|| anyhow::anyhow!("preset path has no parent"))?;
        let pbf = ensure_osm_pbf(project_dir, false)?;
        invalidate_osm_routing_caches(&pbf);
        Ok(())
    }

    /// Prefetch the site-bbox graph after listen so the first draft click is a route, not a PBF parse.
    pub fn prefetch_osm_routing(&self, preset_path: &Path) {
        match self.ensure_osm_covering(preset_path, &[]) {
            Ok(_) => tracing::info!("osm routing graph ready"),
            Err(e) => tracing::warn!("osm routing prefetch: {e:#}"),
        }
    }

    fn ensure_project_osm_routing(
        &self,
        preset_path: &Path,
        _preset: &Preset,
    ) -> Result<Arc<ProjectRouting>> {
        self.ensure_osm_covering(preset_path, &[])
    }

    fn warm_and_publish_site_access(
        &self,
        preset_path: &std::path::Path,
        project_slug: &str,
        site_slug: &str,
        site: &SiteEntry,
        preset: &Preset,
    ) {
        let routing = match self.ensure_project_osm_routing(preset_path, preset) {
            Ok(r) => r,
            Err(e) => {
                tracing::warn!("access warm {project_slug}/{site_slug}: osm {e:#}");
                self.events.publish(
                    project_slug,
                    "access",
                    json!({
                        "project": project_slug,
                        "slug": site_slug,
                        "status": "error",
                        "error": e.to_string(),
                    }),
                );
                return;
            }
        };
        let meta = preset.resolved_access_meta();
        match warm_place_access_with_routing(
            preset_path,
            &self.session,
            routing.as_ref(),
            &meta,
            site.lat(),
            site.lon(),
        ) {
            Ok(Some(mut access)) => {
                if let Err(e) = peaky_peaks::ensure_site_access_options_on_probe(
                    preset_path.parent().unwrap_or(preset_path),
                    &peaky_peaks::SessionElev(&self.session),
                    routing.as_ref(),
                    &meta,
                    site.lat(),
                    site.lon(),
                    &mut access,
                ) {
                    tracing::warn!("site access options {project_slug}/{site_slug}: {e:#}");
                }
                let mut payload = access_payload_json(site_slug, &access, preset_path);
                if let Some(obj) = payload.as_object_mut() {
                    obj.insert("project".into(), json!(project_slug));
                }
                self.events.publish(project_slug, "access", payload);
            }
            Ok(None) => {
                self.events.publish(
                    project_slug,
                    "access",
                    json!({
                        "project": project_slug,
                        "slug": site_slug,
                        "status": "missing",
                    }),
                );
            }
            Err(e) => {
                tracing::warn!("access warm {project_slug}/{site_slug}: {e:#}");
                self.events.publish(
                    project_slug,
                    "access",
                    json!({
                        "project": project_slug,
                        "slug": site_slug,
                        "status": "error",
                        "error": e.to_string(),
                    }),
                );
            }
        }
    }

    fn submit_site_warm(
        &self,
        warm: Arc<ProjectWarm>,
        preset: &Preset,
        site_slug: &str,
        site: &SiteEntry,
        priority: i32,
    ) -> bool {
        let key = match self.site_coverage_key(&warm.preset_path, preset, site) {
            Ok(k) => k,
            Err(_) => return false,
        };
        let mut viewshed_needed = true;
        if let Ok(Some(overlay)) = site_viewshed_overlay_if_ready(
            &warm.slug,
            &warm.preset_path,
            site_slug,
            site,
            preset,
            None,
            Some(self.board_viewshed.as_ref()),
        ) {
            if Self::overlay_is_at_target(&overlay) {
                viewshed_needed = false;
            }
        } else if Self::site_coverage_exists(&warm.preset_path, &key) {
            let _ = std::fs::remove_dir_all(resolved_viewshed_root(&warm.preset_path).join(&key));
        }
        if !viewshed_needed {
            return false;
        }
        let hub = self.clone();
        let warm2 = warm.clone();
        let site_slug = site_slug.to_string();
        let site = site.clone();
        warm.queue.submit(
            key,
            priority,
            Box::new(move || hub.warm_site_job(warm2, &site_slug, &site, false)),
        )
    }

    fn aoi_dem_tile_names(preset: &Preset) -> Vec<String> {
        let points: Vec<(f64, f64)> = preset
            .sites
            .values()
            .map(|s| (s.loc[0], s.loc[1]))
            .collect();
        let buffer = max_hop_range_m(preset);
        required_tile_names_for_points(&points, buffer)
    }

    fn prefetch_dem(&self, preset: &Preset, priority: i32) {
        let mirror = self.session.dem_mirror().clone();
        let tiles = Self::aoi_dem_tile_names(preset);
        thread::spawn(move || {
            mirror.prefetch(&tiles, priority);
        });
    }

    /// Eager AOI Skadi mirror prefetch on project open (background priority).
    /// Idempotent: on-disk tiles are skipped; map/viewport/blocking bands still win.
    pub fn ensure_aoi_dem_prefetch(&self, slug: &str, preset_path: PathBuf) {
        let preset = match load_preset(&preset_path) {
            Ok(p) => p,
            Err(e) => {
                tracing::warn!("aoi dem prefetch {slug}: {e:#}");
                return;
            }
        };
        self.prefetch_dem(&preset, PRIORITY_DEM_BACKGROUND);
    }

    fn prefetch_dem_for_sites(&self, preset: &Preset, site_slugs: &[String], priority: i32) {
        let mirror = self.session.dem_mirror().clone();
        let points: Vec<(f64, f64)> = site_slugs
            .iter()
            .filter_map(|slug| preset.sites.get(slug))
            .map(|s| (s.loc[0], s.loc[1]))
            .collect();
        if points.is_empty() {
            return;
        }
        let buffer = max_hop_range_m(preset);
        let tiles = required_tile_names_for_points(&points, buffer);
        mirror.prefetch(&tiles, priority);
    }

    pub fn ensure_project_warm(&self, slug: &str, preset_path: PathBuf) -> Value {
        let warm = self.project(slug, preset_path.clone());
        if warm.warm_running.swap(true, Ordering::SeqCst) {
            return json!({ "project": slug, "status": "running" });
        }

        let hub = self.clone();
        let warm2 = warm.clone();
        let slug2 = slug.to_string();
        thread::spawn(move || {
            let result = (|| -> Result<()> {
                let preset = load_preset(&warm2.preset_path)?;
                hub.prefetch_dem(&preset, PRIORITY_DEM_BACKGROUND);

                // P2P mesh first — not gated on viewshed PNGs.
                if let Err(e) = hub.refresh_project_links_blocking(warm2.clone()) {
                    tracing::warn!("project links warm {slug2}: {e:#}");
                }

                for (site_slug, site) in preset.sites.iter() {
                    hub.submit_site_warm(
                        warm2.clone(),
                        &preset,
                        site_slug,
                        site,
                        PRIORITY_BACKGROUND,
                    );
                }
                Ok(())
            })();
            if let Err(e) = result {
                tracing::warn!("project warm {slug2}: {e:#}");
            }
            warm2.warm_running.store(false, Ordering::SeqCst);
        });

        json!({ "project": slug, "status": "started" })
    }

    pub fn bump_priorities(
        &self,
        slug: &str,
        preset_path: PathBuf,
        slugs: &[String],
        priority: i32,
    ) -> Result<Value> {
        let warm = self.project(slug, preset_path.clone());
        let preset = load_preset(&preset_path)?;
        let mut queued = 0usize;
        for site_slug in slugs {
            let Some(site) = preset.sites.get(site_slug) else {
                continue;
            };
            self.prefetch_dem_for_sites(&preset, std::slice::from_ref(site_slug), priority.min(PRIORITY_DEM_VIEWPORT));
            let key = self.site_coverage_key(&preset_path, &preset, site)?;
            if let Ok(Some(overlay)) = site_viewshed_overlay_if_ready(
                slug,
                &preset_path,
                site_slug,
                site,
                &preset,
                None,
                Some(self.board_viewshed.as_ref()),
            ) {
                self.publish_viewshed(slug, overlay.clone());
                if Self::overlay_is_at_target(&overlay) {
                    continue;
                }
            }
            if self.submit_site_warm(warm.clone(), &preset, site_slug, site, priority) {
                queued += 1;
            } else {
                warm.queue.bump(&key, priority.min(PRIORITY_LINK_ADJACENT));
            }
        }
        self.ensure_project_warm(slug, preset_path);
        Ok(json!({ "queued": queued, "priority": priority }))
    }

    pub fn warm_status(&self, slug: &str, preset_path: PathBuf) -> Value {
        let warm = self.project(slug, preset_path.clone());
        let phase = warm.links_phase.lock().unwrap().clone();
        let aoi = match load_preset(&preset_path) {
            Ok(preset) => {
                let tiles = Self::aoi_dem_tile_names(&preset);
                let stats = self.session.dem_mirror().aoi_tile_stats(&tiles);
                json!({
                    "total": stats.total,
                    "onDisk": stats.on_disk,
                    "queued": stats.queued,
                    "inflight": stats.inflight,
                    "missing": stats.total.saturating_sub(stats.on_disk),
                })
            }
            Err(_) => Value::Null,
        };
        json!({
            "project": slug,
            "coverageQueueDepth": warm.queue.depth(),
            "demMirrorQueueDepth": self.session.dem_mirror().queue_depth(),
            "aoiDem": aoi,
            "linksWarm": {
                "phase": phase.phase,
                "progress": if phase.phase == "running" {
                    json!({ "done": phase.done, "total": phase.total, "detail": phase.detail })
                } else {
                    Value::Null
                },
            },
        })
    }

    fn coords_coverage_key(
        _preset_path: &PathBuf,
        preset: &Preset,
        lat: f64,
        lon: f64,
        sim: &ViewshedSimOverrides,
    ) -> Result<String> {
        let site = preview_site_at(lat, lon).map_err(|e| anyhow::anyhow!(e.0))?;
        let req = preset_to_request_with_sim(preset, lat, lon, Some(&site), Some(sim), None)?;
        Ok(viewshed_workspace_digest(&req)?)
    }

    fn warm_coords_job(
        &self,
        warm: Arc<ProjectWarm>,
        lat: f64,
        lon: f64,
        sim: ViewshedSimOverrides,
    ) {
        let preset_path = warm.preset_path.clone();
        let session = self.session.clone();
        let slug = warm.slug.clone();
        let hub = self.clone();

        let preset = match load_preset(&preset_path) {
            Ok(p) => p,
            Err(e) => {
                tracing::warn!("viewshed warm coords {slug}: {e:#}");
                return;
            }
        };

        if let Err(e) = ensure_viewshed_progressive_for_coords_blocking(
            session,
            &slug,
            &preset_path,
            &preset,
            lat,
            lon,
            &sim,
            hub.verbose,
            |overlay| hub.publish_viewshed(&slug, overlay),
        ) {
            tracing::warn!("viewshed warm coords {slug}: {e:#}");
            hub.events.publish(
                &slug,
                "viewshed",
                json!({
                    "project": slug,
                    "slug": DRAFT_VIEWSHED_SLUG,
                    "status": "error",
                    "error": e.to_string(),
                    "lat": lat,
                    "lon": lon,
                }),
            );
        }
    }

    /// Interactive warm for coordinate draft viewsheds (link solver hops, site placement).
    pub fn warm_coords_viewshed(
        &self,
        slug: &str,
        preset_path: PathBuf,
        lat: f64,
        lon: f64,
        sim: ViewshedSimOverrides,
    ) -> Result<Value, String> {
        preview_site_at(lat, lon).map_err(|e| e.0)?;
        let preset = load_preset(&preset_path).map_err(|e| e.to_string())?;
        if let Ok(Some(overlay)) =
            coords_viewshed_overlay_if_ready(slug, &preset_path, &preset, lat, lon, Some(&sim))
        {
            let mut payload = overlay.as_object().cloned().unwrap_or_default();
            payload.insert("project".into(), json!(slug));
            payload.insert("status".into(), json!("ready"));
            self.publish_viewshed(slug, Value::Object(payload.clone()));
            return Ok(Value::Object(payload));
        }

        let warm = self.project(slug, preset_path.clone());
        let key = Self::coords_coverage_key(&preset_path, &preset, lat, lon, &sim)
            .map_err(|e| e.to_string())?;
        let hub = self.clone();
        let warm2 = warm.clone();
        warm.queue.submit(
            key,
            PRIORITY_INTERACTIVE,
            Box::new(move || hub.warm_coords_job(warm2, lat, lon, sim)),
        );
        self.events.publish(
            slug,
            "viewshed",
            json!({
                "project": slug,
                "slug": DRAFT_VIEWSHED_SLUG,
                "status": "queued",
                "lat": lat,
                "lon": lon,
            }),
        );
        Ok(json!({
            "project": slug,
            "slug": DRAFT_VIEWSHED_SLUG,
            "status": "queued",
            "lat": lat,
            "lon": lon,
        }))
    }

    pub fn start_links_warm(&self, slug: &str, preset_path: PathBuf) -> Value {
        let warm = self.project(slug, preset_path.clone());
        let hub = self.clone();
        let slug_owned = slug.to_string();
        thread::spawn(move || {
            if let Err(e) = hub.refresh_project_links_blocking(warm) {
                tracing::warn!("links warm {slug_owned}: {e:#}");
            }
        });
        self.ensure_project_warm(slug, preset_path);
        json!({ "project": slug, "status": "started" })
    }
}
