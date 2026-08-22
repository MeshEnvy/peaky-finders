//! Land boot parallelism helpers.

use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::Instant;

/// Thread-safe progress counter for long land boot loops (preview/pipeline warm, etc.).
pub struct LandBootProgress {
    phase: &'static str,
    total: usize,
    done: AtomicUsize,
    started: Instant,
}

impl LandBootProgress {
    pub fn new(phase: &'static str, total: usize) -> Self {
        Self {
            phase,
            total,
            done: AtomicUsize::new(0),
            started: Instant::now(),
        }
    }

    pub fn begin_unit(&self, source_id: &str, unit: &str, action: &str) {
        tracing::info!(
            phase = self.phase,
            source_id = %source_id,
            unit = %unit,
            action = %action,
            "land boot: unit start"
        );
    }

    pub fn finish_unit(&self, source_id: &str, unit: &str, outcome: &str) {
        let done = self.done.fetch_add(1, Ordering::Relaxed) + 1;
        let elapsed_secs = self.started.elapsed().as_secs_f64();
        tracing::info!(
            phase = self.phase,
            source_id = %source_id,
            unit = %unit,
            outcome = %outcome,
            done,
            total = self.total,
            elapsed_secs,
            "land boot: unit done"
        );
    }
}

/// Thread pool size for land audit, refresh, validate, and cache warm.
pub fn land_boot_workers() -> usize {
    std::env::var("PEAKY_LAND_BOOT_WORKERS")
        .ok()
        .and_then(|v| v.parse().ok())
        .filter(|&n| n > 0)
        .unwrap_or_else(|| {
            std::thread::available_parallelism()
                .map(|n| n.get())
                .unwrap_or(4)
                .clamp(2, 8)
        })
}

pub fn land_boot_pool() -> rayon::ThreadPool {
    rayon::ThreadPoolBuilder::new()
        .num_threads(land_boot_workers())
        .build()
        .expect("build land boot thread pool")
}
