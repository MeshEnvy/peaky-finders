//! Priority queue worker pool for Skadi ``*.hgt.gz`` mirror downloads.
//!
//! On-demand callers block at [`PRIORITY_DEM_BLOCKING`]; background warm uses lower priority.
//! Up to N concurrent downloads (from preset `simulation.max_workers.dem` at serve startup);
//! duplicate enqueue bumps priority.
//!
//! Console tracing: run `peaky serve --verbose` or set `PEAKY_DEM_MIRROR_LOG=1`.

use std::cmp::Reverse;
use std::collections::{BinaryHeap, HashMap, HashSet};
use std::env;
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Condvar, Mutex};
use std::thread;
use std::time::Instant;

use anyhow::{anyhow, Result};

use crate::skadi_fetch::fetch_and_write_mirror_tile;

/// Seek / links / viewshed — caller is blocked waiting for this tile.
pub const PRIORITY_DEM_BLOCKING: i32 = 0;
/// Viewport-visible sites (warm priority sync).
pub const PRIORITY_DEM_VIEWPORT: i32 = 10;
/// MapLibre hillshade/terrarium tiles — prefetch only, never block RF/seek.
pub const PRIORITY_DEM_MAP: i32 = 25;
/// Project warm: all preset sites + hop buffer.
pub const PRIORITY_DEM_BACKGROUND: i32 = 100;

fn map_queue_cap() -> usize {
    env::var("PEAKY_DEM_MAP_QUEUE_CAP")
        .ok()
        .and_then(|v| v.parse().ok())
        .filter(|n| *n >= 1)
        .unwrap_or(128)
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct AoiTileStats {
    pub total: usize,
    pub on_disk: usize,
    pub queued: usize,
    pub inflight: usize,
}

pub fn priority_label(priority: i32) -> &'static str {
    match priority {
        PRIORITY_DEM_BLOCKING => "blocking",
        PRIORITY_DEM_VIEWPORT => "viewport",
        PRIORITY_DEM_MAP => "map",
        PRIORITY_DEM_BACKGROUND => "background",
        other => {
            if other < PRIORITY_DEM_VIEWPORT {
                "blocking+"
            } else if other < PRIORITY_DEM_MAP {
                "viewport+"
            } else if other < PRIORITY_DEM_BACKGROUND {
                "map+"
            } else {
                "background+"
            }
        }
    }
}

fn mirror_log_enabled(verbose: bool) -> bool {
    verbose
        || env::var("PEAKY_DEM_MIRROR_LOG")
            .ok()
            .is_some_and(|v| matches!(v.trim(), "1" | "true" | "yes" | "on"))
}

struct QueueState {
    heap: BinaryHeap<(Reverse<(i32, u64)>, String)>,
    entries: HashMap<String, Entry>,
    inflight: HashSet<String>,
    inflight_priority: HashMap<String, i32>,
    ready: HashSet<String>,
    failed: HashMap<String, String>,
    seq: u64,
}

struct Entry {
    priority: i32,
    seq: u64,
}

struct QueueSnapshot {
    pending: usize,
    inflight: usize,
    blocking: usize,
    viewport: usize,
    map: usize,
    background: usize,
}

#[derive(Debug, Eq, PartialEq)]
enum EnqueueOutcome {
    Skip,
    New,
    Bump,
}

struct DemMirrorInner {
    mirror_root: PathBuf,
    verbose: bool,
    queue: Mutex<QueueState>,
    cv: Condvar,
}

#[derive(Clone)]
pub struct DemMirror {
    inner: Arc<DemMirrorInner>,
}

impl DemMirrorInner {
    fn log(&self, msg: impl AsRef<str>) {
        if mirror_log_enabled(self.verbose) {
            let _ = writeln!(io::stderr(), "[dem-mirror] {}", msg.as_ref());
        }
    }

    fn snapshot_locked(q: &QueueState) -> QueueSnapshot {
        let mut blocking = 0usize;
        let mut viewport = 0usize;
        let mut map = 0usize;
        let mut background = 0usize;
        for entry in q.entries.values() {
            Self::count_priority(entry.priority, &mut blocking, &mut viewport, &mut map, &mut background);
        }
        for priority in q.inflight_priority.values() {
            Self::count_priority(*priority, &mut blocking, &mut viewport, &mut map, &mut background);
        }
        QueueSnapshot {
            pending: q.entries.len(),
            inflight: q.inflight.len(),
            blocking,
            viewport,
            map,
            background,
        }
    }

    fn count_priority(
        priority: i32,
        blocking: &mut usize,
        viewport: &mut usize,
        map: &mut usize,
        background: &mut usize,
    ) {
        match priority {
            PRIORITY_DEM_BLOCKING => *blocking += 1,
            PRIORITY_DEM_VIEWPORT => *viewport += 1,
            PRIORITY_DEM_MAP => *map += 1,
            PRIORITY_DEM_BACKGROUND => *background += 1,
            p if p < PRIORITY_DEM_VIEWPORT => *blocking += 1,
            p if p < PRIORITY_DEM_MAP => *viewport += 1,
            p if p < PRIORITY_DEM_BACKGROUND => *map += 1,
            _ => *background += 1,
        }
    }

    fn format_snapshot(s: &QueueSnapshot) -> String {
        format!(
            "queue pending={} inflight={} [blocking={} viewport={} map={} background={}]",
            s.pending, s.inflight, s.blocking, s.viewport, s.map, s.background
        )
    }
}

impl DemMirror {
    pub fn start(mirror_root: PathBuf, verbose: bool, fetch_workers: usize) -> Self {
        std::fs::create_dir_all(&mirror_root).ok();
        let workers = fetch_workers.max(1).min(16);
        let inner = Arc::new(DemMirrorInner {
            mirror_root: mirror_root.clone(),
            verbose,
            queue: Mutex::new(QueueState {
                heap: BinaryHeap::new(),
                entries: HashMap::new(),
                inflight: HashSet::new(),
                inflight_priority: HashMap::new(),
                ready: HashSet::new(),
                failed: HashMap::new(),
                seq: 0,
            }),
            cv: Condvar::new(),
        });
        if mirror_log_enabled(verbose) {
            inner.log(format!(
                "worker pool started mirror={} workers={} (simulation.max_workers.dem, PEAKY_DEM_FETCH_WORKERS override, PEAKY_DEM_MIRROR_LOG=1 or --verbose)",
                mirror_root.display(),
                workers
            ));
        }
        for _ in 0..workers {
            let worker = Arc::clone(&inner);
            thread::spawn(move || DemMirrorInner::run_worker(worker));
        }
        Self { inner }
    }

    pub fn mirror_root(&self) -> &Path {
        &self.inner.mirror_root
    }

    pub fn tile_path(&self, tile_name: &str) -> PathBuf {
        self.inner.mirror_root.join(tile_name)
    }

    pub fn is_on_disk(&self, tile_name: &str) -> bool {
        let path = self.tile_path(tile_name);
        path.is_file() && path.metadata().map(|m| m.len() > 0).unwrap_or(false)
    }

    /// Enqueue missing tiles without blocking (background prefetch).
    pub fn prefetch(&self, tile_names: &[String], priority: i32) {
        let mut enqueued = 0usize;
        let mut bumped = 0usize;
        let mut skipped_on_disk = 0usize;
        let mut skipped_cap = 0usize;
        for name in tile_names {
            if self.is_on_disk(name) {
                skipped_on_disk += 1;
                continue;
            }
            match self.enqueue(name, priority, true) {
                EnqueueOutcome::New => enqueued += 1,
                EnqueueOutcome::Bump => bumped += 1,
                EnqueueOutcome::Skip => {
                    if priority == PRIORITY_DEM_MAP {
                        skipped_cap += 1;
                    }
                }
            }
        }
        if !mirror_log_enabled(self.inner.verbose) {
            return;
        }
        if enqueued > 0 || bumped > 0 {
            let q = self.inner.queue.lock().unwrap();
            let snap = DemMirrorInner::snapshot_locked(&q);
            drop(q);
            self.inner.log(format!(
                "prefetch {}: +{} new, {} bumped — {}",
                priority_label(priority),
                enqueued,
                bumped,
                DemMirrorInner::format_snapshot(&snap)
            ));
        } else if priority == PRIORITY_DEM_BACKGROUND && !tile_names.is_empty() {
            let q = self.inner.queue.lock().unwrap();
            let snap = DemMirrorInner::snapshot_locked(&q);
            drop(q);
            self.inner.log(format!(
                "prefetch background: 0 new ({} tiles, {} on disk) — {}",
                tile_names.len(),
                skipped_on_disk,
                DemMirrorInner::format_snapshot(&snap)
            ));
        } else if priority == PRIORITY_DEM_MAP && skipped_cap > 0 && enqueued == 0 && bumped == 0 {
            self.inner.log(format!(
                "prefetch map: skipped {} (map queue cap {})",
                skipped_cap,
                map_queue_cap()
            ));
        }
    }

    /// Ensure one tile exists on disk, blocking until the mirror worker finishes or fails.
    pub fn ensure(&self, tile_name: &str, priority: i32) -> Result<()> {
        if self.is_on_disk(tile_name) {
            return Ok(());
        }
        {
            let mut q = self.inner.queue.lock().unwrap();
            q.failed.remove(tile_name);
        }
        self.enqueue(tile_name, priority, false);
        self.wait(tile_name, priority)
    }

    /// Blocking ensure for a batch (deduped). Enqueues all missing tiles first so workers
    /// can fetch in parallel, then waits for each.
    pub fn ensure_batch(&self, tile_names: &[String], priority: i32) -> Result<()> {
        let mut unique: Vec<&String> = tile_names.iter().collect();
        unique.sort();
        unique.dedup();

        {
            let mut q = self.inner.queue.lock().unwrap();
            for name in &unique {
                if self.is_on_disk(name) {
                    continue;
                }
                q.failed.remove(*name);
            }
        }

        for name in &unique {
            if !self.is_on_disk(name) {
                self.enqueue(name, priority, true);
            }
        }
        for name in unique {
            if !self.is_on_disk(name) {
                self.wait(name, priority)?;
            }
        }
        Ok(())
    }

    pub fn queue_depth(&self) -> usize {
        let q = self.inner.queue.lock().unwrap();
        q.entries.len() + q.inflight.len()
    }

    /// Progress for a fixed AOI tile list (sites + hop buffer).
    pub fn aoi_tile_stats(&self, tile_names: &[String]) -> AoiTileStats {
        let q = self.inner.queue.lock().unwrap();
        let mut stats = AoiTileStats {
            total: tile_names.len(),
            ..Default::default()
        };
        for name in tile_names {
            if self.is_on_disk(name) {
                stats.on_disk += 1;
                continue;
            }
            if q.inflight.contains(name) {
                stats.inflight += 1;
            } else if q.entries.contains_key(name) {
                stats.queued += 1;
            }
        }
        stats
    }

    fn enqueue(&self, tile_name: &str, priority: i32, quiet: bool) -> EnqueueOutcome {
        let name = tile_name.to_string();
        let mut q = self.inner.queue.lock().unwrap();
        if q.ready.contains(&name) || self.is_on_disk(&name) {
            return EnqueueOutcome::Skip;
        }
        if q.inflight.contains(&name) {
            let from = q.inflight_priority.get(&name).copied();
            if Self::maybe_bump_inflight_locked(&mut q, &name, priority) {
                let snap = DemMirrorInner::snapshot_locked(&q);
                drop(q);
                if !quiet {
                    if let Some(from) = from {
                        if from > priority {
                            self.inner.log(format!(
                                "bump inflight {name} {from}→{priority} ({}) — {}",
                                priority_label(priority),
                                DemMirrorInner::format_snapshot(&snap)
                            ));
                        }
                    }
                }
                return EnqueueOutcome::Bump;
            }
            return EnqueueOutcome::Skip;
        }
        if q.entries.contains_key(&name) {
            let from = q.entries.get(&name).map(|e| e.priority).unwrap_or(priority);
            if Self::maybe_bump_locked(&mut q, &name, priority) {
                let snap = DemMirrorInner::snapshot_locked(&q);
                drop(q);
                if !quiet && from > priority {
                    self.inner.log(format!(
                        "bump queued {name} {from}→{priority} ({}) — {}",
                        priority_label(priority),
                        DemMirrorInner::format_snapshot(&snap)
                    ));
                }
                return EnqueueOutcome::Bump;
            }
            return EnqueueOutcome::Skip;
        }
        if priority == PRIORITY_DEM_MAP {
            let snap = DemMirrorInner::snapshot_locked(&q);
            if snap.map >= map_queue_cap() {
                return EnqueueOutcome::Skip;
            }
        }
        q.seq += 1;
        let seq = q.seq;
        q.entries.insert(
            name.clone(),
            Entry {
                priority,
                seq,
            },
        );
        q.heap.push((Reverse((priority, seq)), name.clone()));
        let snap = DemMirrorInner::snapshot_locked(&q);
        drop(q);
        self.inner.cv.notify_all();
        if !quiet {
            self.inner.log(format!(
                "enqueue {name} priority={priority} ({}) — {}",
                priority_label(priority),
                DemMirrorInner::format_snapshot(&snap)
            ));
        }
        EnqueueOutcome::New
    }

    fn maybe_bump_inflight_locked(q: &mut QueueState, key: &str, priority: i32) -> bool {
        let Some(current) = q.inflight_priority.get(key).copied() else {
            return false;
        };
        if priority >= current {
            return false;
        }
        q.inflight_priority.insert(key.to_string(), priority);
        true
    }

    fn maybe_bump_locked(q: &mut QueueState, key: &str, priority: i32) -> bool {
        let Some(entry) = q.entries.get(key) else {
            return false;
        };
        if priority >= entry.priority {
            return false;
        }
        q.seq += 1;
        let seq = q.seq;
        if let Some(entry) = q.entries.get_mut(key) {
            entry.priority = priority;
            entry.seq = seq;
            q.heap.push((Reverse((priority, seq)), key.to_string()));
        }
        true
    }

    fn wait(&self, tile_name: &str, priority: i32) -> Result<()> {
        let name = tile_name.to_string();
        let mut q = self.inner.queue.lock().unwrap();
        if !self.is_on_disk(&name) {
            let snap = DemMirrorInner::snapshot_locked(&q);
            self.inner.log(format!(
                "wait {name} priority={priority} ({}) — {}",
                priority_label(priority),
                DemMirrorInner::format_snapshot(&snap)
            ));
        }
        loop {
            if self.is_on_disk(&name) {
                return Ok(());
            }
            if let Some(err) = q.failed.get(&name) {
                return Err(anyhow!("DEM mirror fetch failed for {name}: {err}"));
            }
            if q.ready.contains(&name) {
                return Ok(());
            }
            q = self.inner.cv.wait(q).unwrap();
        }
    }
}

impl DemMirrorInner {
    fn run_worker(self: Arc<Self>) {
        loop {
            let job = self.pop_next();
            let Some((tile, priority)) = job else {
                continue;
            };
            let snap = {
                let q = self.queue.lock().unwrap();
                Self::format_snapshot(&Self::snapshot_locked(&q))
            };
            self.log(format!(
                "fetch start {tile} priority={priority} ({}) — {snap}",
                priority_label(priority)
            ));
            let started = Instant::now();
            let result = fetch_and_write_mirror_tile(&self.mirror_root, &tile, self.verbose);
            let elapsed_ms = started.elapsed().as_millis();
            let mut q = self.queue.lock().unwrap();
            q.inflight.remove(&tile);
            q.inflight_priority.remove(&tile);
            match result {
                Ok(()) => {
                    q.ready.insert(tile.clone());
                    let snap = Self::format_snapshot(&Self::snapshot_locked(&q));
                    drop(q);
                    self.log(format!("fetch done {tile} {elapsed_ms}ms — {snap}"));
                }
                Err(e) => {
                    let err = e.to_string();
                    q.failed.insert(tile.clone(), err.clone());
                    let snap = Self::format_snapshot(&Self::snapshot_locked(&q));
                    drop(q);
                    self.log(format!("fetch fail {tile} {elapsed_ms}ms: {err} — {snap}"));
                }
            }
            self.cv.notify_all();
        }
    }

    fn pop_next(self: &Arc<Self>) -> Option<(String, i32)> {
        loop {
            let mut q = self.queue.lock().unwrap();
            while q.heap.is_empty() {
                q = self.cv.wait(q).unwrap();
            }
            let (_, key) = q.heap.pop()?;
            let Some(entry) = q.entries.get(&key) else {
                continue;
            };
            let (entry_priority, entry_seq) = (entry.priority, entry.seq);
            let top = q.heap.peek().map(|(Reverse((p, s)), _)| (*p, *s));
            if let Some((p, s)) = top {
                if p < entry_priority || (p == entry_priority && s < entry_seq) {
                    q.heap
                        .push((Reverse((entry_priority, entry_seq)), key.clone()));
                    continue;
                }
            }
            q.entries.remove(&key);
            q.inflight.insert(key.clone());
            q.inflight_priority.insert(key.clone(), entry_priority);
            return Some((key, entry_priority));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn on_disk_skips_enqueue() {
        let dir = std::env::temp_dir().join(format!("dem-mirror-disk-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("N40W119.hgt.gz"), b"x").unwrap();
        let mirror = DemMirror::start(dir.clone(), false, 4);
        assert!(mirror.is_on_disk("N40W119.hgt.gz"));
        mirror.prefetch(&["N40W119.hgt.gz".into()], PRIORITY_DEM_BACKGROUND);
        assert_eq!(mirror.queue_depth(), 0);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn map_queue_cap_skips_new_low_priority() {
        let dir = std::env::temp_dir().join(format!("dem-mirror-cap-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let mirror = DemMirror::start(dir.clone(), false, 4);
        let cap = map_queue_cap();
        for i in 0..cap {
            let lat = 40 + (i / 10) as i32;
            let lon = -119 - (i % 10) as i32;
            let name = format!("N{lat}W{lon}.hgt.gz");
            assert_eq!(
                mirror.enqueue(&name, PRIORITY_DEM_MAP, true),
                EnqueueOutcome::New
            );
        }
        assert_eq!(
            mirror.enqueue("N99W199.hgt.gz", PRIORITY_DEM_MAP, true),
            EnqueueOutcome::Skip
        );
        assert_eq!(mirror.queue_depth(), cap);
        let _ = std::fs::remove_dir_all(&dir);
    }
}
