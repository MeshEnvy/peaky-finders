//! Live SSE watch server for `peaky find path --watch`.

use std::collections::HashMap;
use std::convert::Infallible;
use std::path::PathBuf;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::Duration;

use anyhow::{Context, Result};
use axum::{
    body::Body,
    extract::{Path, Query, State},
    http::{header, StatusCode},
    response::{Html, IntoResponse, Json, Response, Sse},
    routing::get,
    Router,
};
use parking_lot::Mutex;
use peaky_preset::resolved_viewshed_root;
use serde_json::{json, Value};
use splatter::session::Session;
use tokio::sync::{broadcast, oneshot};

const WATCH_HTML: &str = include_str!("../../../assets/finder-watch/index.html");
const BROADCAST_CAP: usize = 4096;
const LOG_REPLAY_MAX: usize = 80;

/// Events replayed to clients that connect mid-run (in this order).
const REPLAY_ORDER: &[&str] = &[
    "route",
    "existing",
    "gaps",
    "search_focus",
    "search_wedge",
    "search_disc",
    "segment_active",
    "search_step",
    "chain_partial",
    "viewshed",
    "dem_tiles",
    "dem_touch",
    "mask_tile",
    "peaks_ready",
    "scan_progress",
    "candidates",
    "chain",
    "done",
    "phase",
];

#[derive(Clone, Debug)]
pub struct WatchEvent {
    pub name: String,
    pub data: Value,
}

#[derive(Clone, Debug)]
pub struct FinderWatchHub {
    tx: broadcast::Sender<WatchEvent>,
    replay: Arc<Mutex<HashMap<String, Value>>>,
    peak_batches: Arc<Mutex<Vec<Value>>>,
    /// Latest land-mask snapshot per tile name (for mid-run reconnect).
    mask_tiles: Arc<Mutex<HashMap<String, Value>>>,
    /// Cumulative DEM tile meta for reconnect (`dem_tiles` merge).
    dem_tiles: Arc<Mutex<HashMap<String, Value>>>,
    log_lines: Arc<Mutex<Vec<String>>>,
    peaks_total: Arc<AtomicUsize>,
    viewshed_overlays: Arc<Mutex<HashMap<String, Value>>>,
}

impl Default for FinderWatchHub {
    fn default() -> Self {
        Self::new()
    }
}

impl FinderWatchHub {
    pub fn new() -> Self {
        let (tx, _) = broadcast::channel(BROADCAST_CAP);
        Self {
            tx,
            replay: Arc::new(Mutex::new(HashMap::new())),
            peak_batches: Arc::new(Mutex::new(Vec::new())),
            mask_tiles: Arc::new(Mutex::new(HashMap::new())),
            dem_tiles: Arc::new(Mutex::new(HashMap::new())),
            log_lines: Arc::new(Mutex::new(Vec::new())),
            peaks_total: Arc::new(AtomicUsize::new(0)),
            viewshed_overlays: Arc::new(Mutex::new(HashMap::new())),
        }
    }

    pub fn mask_tile(&self, tile: &str) -> Option<Value> {
        self.mask_tiles.lock().get(tile).cloned()
    }

    pub fn has_mask_tile(&self, tile: &str) -> bool {
        self.mask_tiles.lock().contains_key(tile)
    }

    pub fn peaks_batch(&self, tile: &str) -> Option<Value> {
        self.peak_batches
            .lock()
            .iter()
            .rev()
            .find(|b| b.get("tile").and_then(|t| t.as_str()) == Some(tile))
            .cloned()
    }

    /// Merged peak points from every batch (for reconnect hydrate).
    pub fn all_peaks(&self) -> Value {
        let batches = self.peak_batches.lock();
        let mut peaks = Vec::new();
        for batch in batches.iter() {
            if let Some(arr) = batch.get("peaks").and_then(|p| p.as_array()) {
                peaks.extend(arr.iter().cloned());
            }
        }
        let total = self.peaks_total.load(Ordering::Relaxed);
        json!({
            "peaks": peaks,
            "peaks_total": if total > 0 { total } else { peaks.len() },
        })
    }

    fn peaks_batch_stub(data: &Value, batch_n: usize, peaks_total: usize) -> Value {
        json!({
            "tile": data.get("tile"),
            "done": data.get("done"),
            "total": data.get("total"),
            "batch_peaks": batch_n,
            "peaks_total": peaks_total,
        })
    }

    pub fn publish(&self, name: impl Into<String>, data: Value) {
        let name = name.into();
        // Wire payload may be a stub; full blobs stay in hub storage for HTTP fetch.
        let mut wire = data.clone();
        match name.as_str() {
            "dem_tiles" => {
                let mut store = self.dem_tiles.lock();
                if let Some(tiles) = data.get("tiles").and_then(|t| t.as_array()) {
                    for t in tiles {
                        if let Some(name) = t.get("name").and_then(|n| n.as_str()) {
                            store.insert(name.to_string(), t.clone());
                        }
                    }
                }
                let merged: Vec<Value> = store.values().cloned().collect();
                let payload = json!({ "tiles": merged, "merge": true });
                self.replay
                    .lock()
                    .insert("dem_tiles".to_string(), payload.clone());
                wire = payload;
            }
            "mask_tile" => {
                if let Some(tile) = data.get("tile").and_then(|t| t.as_str()) {
                    self.mask_tiles
                        .lock()
                        .insert(tile.to_string(), data.clone());
                    // SSE only carries a pointer — full samples are ~16k u8/tile and lag the bus.
                    wire = json!({
                        "tile": tile,
                        "w": data.get("w"),
                        "h": data.get("h"),
                        "west": data.get("west"),
                        "south": data.get("south"),
                        "east": data.get("east"),
                        "north": data.get("north"),
                    });
                }
            }
            "peaks_batch" => {
                let batch_n = data
                    .get("peaks")
                    .and_then(|p| p.as_array())
                    .map(|a| a.len())
                    .unwrap_or(0);
                let total = self.peaks_total.fetch_add(batch_n, Ordering::Relaxed) + batch_n;
                self.peak_batches.lock().push(data.clone());
                wire = Self::peaks_batch_stub(&data, batch_n, total);
                let scan = json!({
                    "status": "tile_done",
                    "tile": data.get("tile"),
                    "done": data.get("done"),
                    "total": data.get("total"),
                    "batch_peaks": batch_n,
                    "peaks_total": total,
                });
                self.replay.lock().insert("scan_progress".to_string(), scan.clone());
                let _ = self.tx.send(WatchEvent {
                    name: "scan_progress".to_string(),
                    data: scan,
                });
            }
            "scan_progress" => {
                self.replay.lock().insert(name.clone(), data.clone());
            }
            "log" => {
                if let Some(line) = data.get("line").and_then(|v| v.as_str()) {
                    let mut lines = self.log_lines.lock();
                    lines.push(line.to_string());
                    if lines.len() > LOG_REPLAY_MAX {
                        let drop = lines.len() - LOG_REPLAY_MAX;
                        lines.drain(0..drop);
                    }
                }
            }
            "phase" => {
                // Keep phase-end and non-start updates for replay; skip stale "start" markers.
                let keep = data.get("state").and_then(|s| s.as_str()) != Some("start");
                if keep {
                    self.replay.lock().insert(name.clone(), data.clone());
                }
            }
            "viewshed" => {
                if let Some(slug) = data.get("slug").and_then(|s| s.as_str()) {
                    self.viewshed_overlays
                        .lock()
                        .insert(slug.to_string(), data.clone());
                }
            }
            _ if REPLAY_ORDER.contains(&name.as_str()) => {
                self.replay.lock().insert(name.clone(), data.clone());
            }
            _ => {}
        }
        let _ = self.tx.send(WatchEvent {
            name,
            data: wire,
        });
    }

    pub fn subscribe(&self) -> broadcast::Receiver<WatchEvent> {
        self.tx.subscribe()
    }

    fn replay_events(&self) -> Vec<WatchEvent> {
        let replay = self.replay.lock();
        let peaks = self.peak_batches.lock();
        let masks = self.mask_tiles.lock();
        let logs = self.log_lines.lock();
        let mut out = Vec::new();
        for key in REPLAY_ORDER {
            if *key == "peaks_ready" {
                if !peaks.is_empty() {
                    let n: usize = peaks
                        .iter()
                        .filter_map(|b| b.get("peaks").and_then(|p| p.as_array()).map(|a| a.len()))
                        .sum();
                    out.push(WatchEvent {
                        name: "peaks_ready".to_string(),
                        data: json!({
                            "peaks_total": self.peaks_total.load(Ordering::Relaxed).max(n),
                            "batches": peaks.len(),
                        }),
                    });
                }
            } else if *key == "dem_tiles" {
                let dems = self.dem_tiles.lock();
                if !dems.is_empty() {
                    let tiles: Vec<Value> = dems.values().cloned().collect();
                    out.push(WatchEvent {
                        name: "dem_tiles".to_string(),
                        data: json!({ "tiles": tiles, "merge": true }),
                    });
                }
            } else if *key == "mask_tile" {
                for data in masks.values() {
                    out.push(WatchEvent {
                        name: "mask_tile".to_string(),
                        data: json!({
                            "tile": data.get("tile"),
                            "w": data.get("w"),
                            "h": data.get("h"),
                            "west": data.get("west"),
                            "south": data.get("south"),
                            "east": data.get("east"),
                            "north": data.get("north"),
                        }),
                    });
                }
            } else if *key == "viewshed" {
                for data in self.viewshed_overlays.lock().values() {
                    out.push(WatchEvent {
                        name: "viewshed".to_string(),
                        data: data.clone(),
                    });
                }
            } else if *key == "phase" {
                if let Some(data) = replay.get("phase") {
                    out.push(WatchEvent {
                        name: "phase".to_string(),
                        data: data.clone(),
                    });
                }
            } else if let Some(data) = replay.get(*key) {
                out.push(WatchEvent {
                    name: (*key).to_string(),
                    data: data.clone(),
                });
            }
        }
        for line in logs.iter() {
            out.push(WatchEvent {
                name: "log".to_string(),
                data: json!({ "line": line }),
            });
        }
        out
    }
}

struct WatchState {
    hub: FinderWatchHub,
    /// Shared with the finder run — terrarium/hillshade reuse loaded Skadi tiles.
    session: Arc<Session>,
    preset_path: PathBuf,
}

pub struct FinderWatchServer {
    port: u16,
    shutdown: oneshot::Sender<()>,
}

impl FinderWatchServer {
    /// Bind localhost and spawn the axum server on the current tokio runtime.
    pub async fn start(
        hub: Arc<FinderWatchHub>,
        port: Option<u16>,
        session: Arc<Session>,
        preset_path: PathBuf,
    ) -> Result<Self> {
        let bind_port = port.unwrap_or(9847);
        let listener = tokio::net::TcpListener::bind(format!("127.0.0.1:{bind_port}"))
            .await
            .with_context(|| format!("bind finder watch on 127.0.0.1:{bind_port}"))?;
        let port = listener.local_addr()?.port();
        let (shutdown_tx, shutdown_rx) = oneshot::channel();
        let state = Arc::new(WatchState {
            hub: (*hub).clone(),
            session,
            preset_path,
        });
        let app = Router::new()
            .route("/", get(index))
            .route("/events", get(events))
            .route("/mask", get(get_mask_tile))
            .route("/peaks", get(get_peaks_batch))
            .route("/dem/hillshade/{z}/{x}/{y}", get(dem_hillshade_tile))
            .route("/dem/terrarium/{z}/{x}/{y}", get(dem_terrarium_tile))
            .route("/viewshed/{digest}/splat.png", get(viewshed_png))
            .with_state(state);
        tokio::spawn(async move {
            let graceful = async move {
                let _ = shutdown_rx.await;
            };
            if axum::serve(listener, app)
                .with_graceful_shutdown(graceful)
                .await
                .is_err()
            {
                tracing::warn!("finder watch server stopped");
            }
        });
        Ok(Self {
            port,
            shutdown: shutdown_tx,
        })
    }

    pub fn port(&self) -> u16 {
        self.port
    }

    pub fn url(&self) -> String {
        format!("http://127.0.0.1:{}/", self.port)
    }

    pub async fn wait_shutdown(self) -> Result<()> {
        eprintln!("Press Ctrl+C to close the watch server.");
        tokio::signal::ctrl_c().await?;
        let _ = self.shutdown.send(());
        Ok(())
    }
}

async fn index() -> Html<&'static str> {
    Html(WATCH_HTML)
}

#[derive(Debug, serde::Deserialize)]
struct MaskQuery {
    tile: String,
}

#[derive(Debug, serde::Deserialize)]
struct PeaksQuery {
    tile: Option<String>,
}

async fn get_mask_tile(
    State(state): State<Arc<WatchState>>,
    Query(q): Query<MaskQuery>,
) -> Result<Json<Value>, StatusCode> {
    state
        .hub
        .mask_tile(&q.tile)
        .map(Json)
        .ok_or(StatusCode::NOT_FOUND)
}

async fn get_peaks_batch(
    State(state): State<Arc<WatchState>>,
    Query(q): Query<PeaksQuery>,
) -> Result<Json<Value>, StatusCode> {
    match q.tile.as_deref() {
        Some(tile) => state.hub.peaks_batch(tile).map(Json).ok_or(StatusCode::NOT_FOUND),
        None => Ok(Json(state.hub.all_peaks())),
    }
}

fn dem_tile_status(err: anyhow::Error) -> StatusCode {
    if err.to_string().contains("waiting on") {
        StatusCode::SERVICE_UNAVAILABLE
    } else {
        StatusCode::NOT_FOUND
    }
}

fn png_response(png: Vec<u8>) -> Response {
    Response::builder()
        .header(header::CONTENT_TYPE, "image/png")
        .header(header::CACHE_CONTROL, "public, max-age=86400")
        .body(Body::from(png))
        .unwrap()
}

async fn dem_hillshade_tile(
    State(state): State<Arc<WatchState>>,
    Path((z, x, y)): Path<(u32, u32, u32)>,
) -> Result<Response, StatusCode> {
    // Fail fast: never hold the HTTP connection while HGT prefetches.
    // Long waits starve Chrome's ~6 connections/origin and block EventSource.
    let session = state.session.clone();
    let png = tokio::task::spawn_blocking(move || session.skadi_hillshade_tile_png(z, x, y))
        .await
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?
        .map_err(dem_tile_status)?;
    Ok(png_response(png))
}

async fn dem_terrarium_tile(
    State(state): State<Arc<WatchState>>,
    Path((z, x, y)): Path<(u32, u32, u32)>,
) -> Result<Response, StatusCode> {
    let session = state.session.clone();
    let png = tokio::task::spawn_blocking(move || session.skadi_terrarium_tile_png(z, x, y))
        .await
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?
        .map_err(dem_tile_status)?;
    Ok(png_response(png))
}

async fn viewshed_png(
    State(state): State<Arc<WatchState>>,
    Path(digest): Path<String>,
) -> Result<Response, StatusCode> {
    let png_path = resolved_viewshed_root(&state.preset_path)
        .join(&digest)
        .join("splat.png");
    if !png_path.is_file() {
        return Err(StatusCode::NOT_FOUND);
    }
    let bytes = tokio::fs::read(&png_path)
        .await
        .map_err(|_| StatusCode::NOT_FOUND)?;
    Ok(png_response(bytes))
}

async fn events(State(state): State<Arc<WatchState>>) -> impl IntoResponse {
    let mut rx = state.hub.subscribe();
    let replay = state.hub.replay_events();
    let stream = async_stream::stream! {
        yield Ok::<_, Infallible>(
            axum::response::sse::Event::default().comment("connected"),
        );
        yield Ok(axum::response::sse::Event::default().event("hello").data(
            json!({ "service": "finder-watch" }).to_string(),
        ));
        // Let hello flush before the mask stub flood so the UI can leave "Connecting…".
        tokio::task::yield_now().await;
        for ev in replay {
            yield Ok(axum::response::sse::Event::default()
                .event(&ev.name)
                .data(ev.data.to_string()));
            tokio::task::yield_now().await;
        }
        let mut interval = tokio::time::interval(Duration::from_secs(15));
        loop {
            tokio::select! {
                msg = rx.recv() => {
                    match msg {
                        Ok(ev) => {
                            yield Ok(axum::response::sse::Event::default()
                                .event(&ev.name)
                                .data(ev.data.to_string()));
                        }
                        Err(broadcast::error::RecvError::Lagged(_)) => continue,
                        Err(broadcast::error::RecvError::Closed) => break,
                    }
                }
                _ = interval.tick() => {
                    yield Ok(axum::response::sse::Event::default().comment("keepalive"));
                }
            }
        }
    };
    Sse::new(stream).keep_alive(
        axum::response::sse::KeepAlive::new().interval(Duration::from_secs(15)),
    )
}
