//! Candidate peak universe before access filtering.

use std::collections::HashSet;
use std::time::Instant;

use peaky_preset::{slugify_files_segment, Preset};
use tracing::info;

#[derive(Debug, Clone)]
pub struct RawCandidate {
    pub name: Option<String>,
    pub lat: f64,
    pub lon: f64,
    pub elev_m: Option<f64>,
    pub source: String,
    pub seed_slug: Option<String>,
}

pub fn seed_candidates_from_sites(preset: &Preset, site_tags: &[String]) -> Vec<RawCandidate> {
    let mut out = Vec::new();
    for (slug, site) in &preset.sites {
        let tags: HashSet<&str> = site.tags.iter().map(String::as_str).collect();
        let Some(source) = site_tags.iter().find(|t| tags.contains(t.as_str())) else {
            continue;
        };
        out.push(RawCandidate {
            name: Some(site.name.clone()),
            lat: site.lat(),
            lon: site.lon(),
            elev_m: None,
            source: source.clone(),
            seed_slug: Some(slug.clone()),
        });
    }
    out
}

pub fn slug_for_candidate(c: &RawCandidate, existing: &HashSet<String>) -> String {
    if let Some(slug) = &c.seed_slug {
        return format!("peak-{slug}");
    }
    let base = c
        .name
        .as_deref()
        .map(slugify_files_segment)
        .filter(|s| s != "site")
        .unwrap_or_else(|| format!("{}-{}", c.source, (c.lat * 1000.0) as i32));
    let mut slug = format!("{base}-{}", (c.lon * 1000.0) as i32);
    if existing.contains(&slug) {
        slug = format!("{slug}-{}", (c.lat * 1_000_000.0) as i64);
    }
    slug
}

fn candidate_elev(c: &RawCandidate) -> f64 {
    c.elev_m.unwrap_or(0.0)
}

const DEDUP_PROGRESS_EVERY: usize = 2500;
const DEDUP_HEARTBEAT_SECS: u64 = 10;

/// Drop nearby duplicates; keep the higher-elevation row when two seeds share a mountain.
pub fn dedup_nearby(candidates: Vec<RawCandidate>, min_m: f64) -> Vec<RawCandidate> {
    let total = candidates.len();
    let started = Instant::now();
    let mut last_log = started;
    let mut kept: Vec<RawCandidate> = Vec::new();
    for (i, c) in candidates.into_iter().enumerate() {
        let heartbeat = last_log.elapsed().as_secs() >= DEDUP_HEARTBEAT_SECS;
        if i == 0 || i % DEDUP_PROGRESS_EVERY == 0 || i + 1 == total || heartbeat {
            info!(
                processed = i,
                total,
                kept = kept.len(),
                min_m,
                elapsed_secs = started.elapsed().as_secs_f64(),
                "peaks: dedup nearby progress"
            );
            last_log = Instant::now();
        }
        if let Some(idx) = kept.iter().position(|k| {
            crate::hike::haversine_m(c.lat, c.lon, k.lat, k.lon) < min_m
        }) {
            if candidate_elev(&c) > candidate_elev(&kept[idx]) {
                kept[idx] = c;
            }
        } else {
            kept.push(c);
        }
    }
    info!(
        total,
        kept = kept.len(),
        min_m,
        elapsed_secs = started.elapsed().as_secs_f64(),
        "peaks: dedup nearby done"
    );
    kept
}
