//! Land boot parallelism helpers.

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
