//! Structured cache and phase logging for auto-finder.

use std::collections::HashMap;
use std::io::{self, Write};
use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use serde_json::json;

use crate::watch::FinderWatchHub;

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct OpStats {
    pub hits: u64,
    pub misses: u64,
    pub miss_ms: u64,
}

#[derive(Debug)]
pub struct CacheLedger {
    stats: Mutex<HashMap<String, OpStats>>,
    quiet: bool,
    watch: Option<Arc<FinderWatchHub>>,
    pub dem_tiles_fetched: AtomicU64,
    pub dem_tiles_loaded: AtomicU64,
    /// Sample counter for watch `link_check` SSE (failures are sparse).
    link_probe_seq: AtomicUsize,
}

/// Emit every Nth failed link probe; successes always emit (they are rare).
const LINK_PROBE_FAIL_EVERY: usize = 24;

impl CacheLedger {
    pub fn new(quiet: bool) -> Self {
        Self {
            quiet,
            stats: Mutex::new(HashMap::new()),
            watch: None,
            dem_tiles_fetched: AtomicU64::new(0),
            dem_tiles_loaded: AtomicU64::new(0),
            link_probe_seq: AtomicUsize::new(0),
        }
    }

    pub fn attach_watch(&mut self, hub: Arc<FinderWatchHub>) {
        self.watch = Some(hub);
    }

    pub fn watch(&self) -> Option<Arc<FinderWatchHub>> {
        self.watch.clone()
    }

    fn watch_emit(&self, name: &str, data: serde_json::Value) {
        if let Some(hub) = &self.watch {
            hub.publish(name, data);
        }
    }

    /// Ephemeral watch flash for a candidate pair RF check (lon/lat GeoJSON order).
    pub fn watch_link_probe(&self, a_lon: f64, a_lat: f64, b_lon: f64, b_lat: f64, ok: bool) {
        if self.watch.is_none() {
            return;
        }
        self.watch_emit(
            "link_check",
            json!({
                "ok": ok,
                "a": [a_lon, a_lat],
                "b": [b_lon, b_lat],
            }),
        );
    }

    /// Tile touched during on-demand DEM ensure (search wedge / seed).
    pub fn watch_dem_touch(&self, tile_name: &str) {
        self.watch_emit("dem_touch", json!({ "name": tile_name }));
    }

    pub fn set_quiet(&mut self, quiet: bool) {
        self.quiet = quiet;
    }

    pub fn record_dem_tiles(&self, fetched: u64, loaded: u64) {
        self.dem_tiles_fetched
            .fetch_add(fetched, Ordering::Relaxed);
        self.dem_tiles_loaded.fetch_add(loaded, Ordering::Relaxed);
    }

    pub fn phase_start(&self, name: &str) {
        self.watch_emit("phase", json!({ "name": name, "state": "start" }));
        if self.quiet {
            return;
        }
        let _ = writeln!(io::stderr(), "[finder] [phase] start {name}");
    }

    pub fn phase_end(&self, name: &str, detail: &str) {
        self.watch_emit(
            "phase",
            json!({ "name": name, "state": "end", "detail": detail }),
        );
        if self.quiet {
            return;
        }
        let _ = writeln!(io::stderr(), "[finder] [phase] end {name} {detail}");
    }

    pub fn progress(&self, detail: &str) {
        self.watch_emit("log", json!({ "line": detail }));
        if self.quiet {
            return;
        }
        let _ = writeln!(io::stderr(), "[finder] [progress] {detail}");
        let _ = io::stderr().flush();
    }

    /// Live hop-disc peak scan progress (progress bar + phase bar + throttled log).
    pub fn watch_hop_disc_scan(
        &self,
        rows_done: usize,
        rows_total: usize,
        tiles: usize,
        center_lon: f64,
        center_lat: f64,
        radius_m: f64,
    ) {
        let pct = if rows_total > 0 {
            ((rows_done as f64 / rows_total as f64) * 100.0).round() as u32
        } else {
            0
        };
        self.watch_emit(
            "scan_progress",
            json!({
                "status": "hop_disc",
                "done": rows_done,
                "total": rows_total,
                "tiles": tiles,
                "pct": pct,
                "center": [center_lon, center_lat],
                "radius_m": radius_m,
            }),
        );
        let log_every = (rows_total / 20).max(1).min(500);
        if rows_done == 0 || rows_done == rows_total || rows_done % log_every == 0 {
            let line = format!(
                "peak scan rows [{rows_done}/{rows_total}] ({pct}%) · {tiles} tile(s)"
            );
            self.watch_emit("search_step", json!({ "line": line }));
            if self.watch.is_none() {
                self.progress(&line);
            }
        }
    }

    /// Wedge coverage filter progress while evaluating disc peaks.
    pub fn watch_coverage_progress(&self, in_wedge: usize, covered: usize, disc_total: usize) {
        let pct = if disc_total > 0 {
            ((in_wedge as f64 / disc_total as f64) * 100.0).round() as u32
        } else {
            0
        };
        self.watch_emit(
            "scan_progress",
            json!({
                "status": "coverage",
                "done": in_wedge,
                "covered": covered,
                "total": disc_total,
                "pct": pct,
            }),
        );
    }

    /// Hop disc about to load / scan (full radius ring on map before row sweep).
    pub fn watch_search_disc(&self, center_lon: f64, center_lat: f64, radius_m: f64, tiles: usize) {
        self.watch_emit(
            "search_disc",
            json!({
                "center": [center_lon, center_lat],
                "radius_m": radius_m,
                "tiles": tiles,
            }),
        );
    }

    /// Route gap scan progress (eligible-land sampling along the KML).
    pub fn watch_gap_overview(
        &self,
        seg_done: usize,
        seg_total: usize,
        gaps_so_far: usize,
        from_lon: f64,
        from_lat: f64,
        to_lon: f64,
        to_lat: f64,
    ) {
        let pct = if seg_total > 0 {
            ((seg_done as f64 / seg_total as f64) * 100.0).round() as u32
        } else {
            0
        };
        self.watch_emit(
            "scan_progress",
            json!({
                "status": "gap_overview",
                "done": seg_done,
                "total": seg_total,
                "gaps": gaps_so_far,
                "pct": pct,
                "from": [from_lon, from_lat],
                "to": [to_lon, to_lat],
            }),
        );
        if seg_done == 1 || seg_done == seg_total || seg_done % 2 == 0 {
            let line = format!(
                "gap overview segment {seg_done}/{seg_total} ({gaps_so_far} gaps so far)"
            );
            self.watch_emit("search_step", json!({ "line": line }));
            if self.watch.is_none() {
                self.progress(&line);
            }
        }
    }

    /// One ineligible land gap found while walking the route.
    pub fn watch_gap_found(
        &self,
        start_lon: f64,
        start_lat: f64,
        end_lon: f64,
        end_lat: f64,
        length_m: f64,
    ) {
        self.watch_emit(
            "gap_found",
            json!({
                "start_lon": start_lon,
                "start_lat": start_lat,
                "end_lon": end_lon,
                "end_lat": end_lat,
                "length_m": length_m,
            }),
        );
    }

    /// Live solver heartbeat for watch UI (phase bar + log + replay on reconnect).
    pub fn search_step(&self, detail: &str) {
        self.watch_emit("search_step", json!({ "line": detail }));
        self.progress(detail);
    }

    fn should_log_cache(&self, op: &str) -> bool {
        if self.quiet {
            return false;
        }
        // With --watch, cover/link spam hides meaningful progress lines.
        self.watch.is_none() || !matches!(op, "cover" | "link")
    }

    pub fn segment_bfs(&self, idx: usize, total: usize, hops: usize) {
        if self.quiet {
            return;
        }
        let _ = writeln!(
            io::stderr(),
            "[finder] [progress] segment {idx}/{total} bfs hops={hops}"
        );
    }

    pub fn log_hit(&self, op: &str, detail: &str) {
        if let Ok(mut stats) = self.stats.lock() {
            let entry = stats.entry(op.to_string()).or_default();
            entry.hits += 1;
        }
        if !self.should_log_cache(op) {
            return;
        }
        let _ = writeln!(io::stderr(), "[finder] [cache] hit  {op} {detail}");
    }

    pub fn log_miss(&self, op: &str, detail: &str, elapsed: Duration) {
        if let Ok(mut stats) = self.stats.lock() {
            let entry = stats.entry(op.to_string()).or_default();
            entry.misses += 1;
            entry.miss_ms += elapsed.as_millis() as u64;
        }
        if !self.should_log_cache(op) {
            return;
        }
        let _ = writeln!(
            io::stderr(),
            "[finder] [cache] miss {op} {detail} elapsed={:.1}s",
            elapsed.as_secs_f64()
        );
    }

    pub fn print_summary(&self) {
        let fmt = |op: &str| -> String {
            let s = self.stats().get(op).cloned().unwrap_or_default();
            format!("miss={} hit={}", s.misses, s.hits)
        };
        let _ = writeln!(
            io::stderr(),
            "[finder] [summary] dem tiles fetched={} loaded={} | peaks {} | cover {} | link {} | bitmask {} | run {}",
            self.dem_tiles_fetched.load(Ordering::Relaxed),
            self.dem_tiles_loaded.load(Ordering::Relaxed),
            fmt("peaks"),
            fmt("cover"),
            fmt("link"),
            fmt("bitmask"),
            fmt("run"),
        );
    }

    pub fn dem_tiles_fetched(&self) -> u64 {
        self.dem_tiles_fetched.load(Ordering::Relaxed)
    }

    pub fn dem_tiles_loaded(&self) -> u64 {
        self.dem_tiles_loaded.load(Ordering::Relaxed)
    }

    pub fn stats(&self) -> HashMap<String, OpStats> {
        self.stats.lock().map(|s| s.clone()).unwrap_or_default()
    }

    pub fn timed<F, T>(&self, op: &str, detail: &str, hit: bool, f: F) -> T
    where
        F: FnOnce() -> T,
    {
        if hit {
            self.log_hit(op, detail);
            return f();
        }
        let t0 = Instant::now();
        let out = f();
        self.log_miss(op, detail, t0.elapsed());
        out
    }
}
