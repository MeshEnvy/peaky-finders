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

/// Nearby candidate dropped because a higher (or earlier) seed won the same mountain.
#[derive(Debug, Clone)]
pub struct DedupDrop {
    pub dropped: RawCandidate,
    pub kept: RawCandidate,
    pub dist_m: f64,
}

/// Drop nearby duplicates; keep the higher-elevation row when two seeds share a mountain.
pub fn dedup_nearby(candidates: Vec<RawCandidate>, min_m: f64) -> Vec<RawCandidate> {
    dedup_nearby_with_drops(candidates, min_m).0
}

/// Same as [`dedup_nearby`], plus the losers and who they collapsed into.
pub fn dedup_nearby_with_drops(
    candidates: Vec<RawCandidate>,
    min_m: f64,
) -> (Vec<RawCandidate>, Vec<DedupDrop>) {
    let total = candidates.len();
    let started = Instant::now();
    let mut last_log = started;
    let mut kept: Vec<RawCandidate> = Vec::new();
    let mut drops: Vec<DedupDrop> = Vec::new();
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
        if let Some(idx) = kept
            .iter()
            .position(|k| crate::hike::haversine_m(c.lat, c.lon, k.lat, k.lon) < min_m)
        {
            let dist_m = crate::hike::haversine_m(c.lat, c.lon, kept[idx].lat, kept[idx].lon);
            if candidate_elev(&c) > candidate_elev(&kept[idx]) {
                drops.push(DedupDrop {
                    dropped: kept[idx].clone(),
                    kept: c.clone(),
                    dist_m,
                });
                kept[idx] = c;
            } else {
                drops.push(DedupDrop {
                    dropped: c,
                    kept: kept[idx].clone(),
                    dist_m,
                });
            }
        } else {
            kept.push(c);
        }
    }
    info!(
        total,
        kept = kept.len(),
        dropped = drops.len(),
        min_m,
        elapsed_secs = started.elapsed().as_secs_f64(),
        "peaks: dedup nearby done"
    );
    (kept, drops)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn cand(lat: f64, lon: f64, elev_m: f64, source: &str) -> RawCandidate {
        RawCandidate {
            name: None,
            lat,
            lon,
            elev_m: Some(elev_m),
            source: source.into(),
            seed_slug: None,
        }
    }

    #[test]
    fn dedup_reports_lower_seed() {
        let (kept, drops) = dedup_nearby_with_drops(
            vec![
                cand(38.0, -117.0, 2000.0, "dem"),
                cand(38.001, -117.0, 1800.0, "gnis"),
            ],
            300.0,
        );
        assert_eq!(kept.len(), 1);
        assert_eq!(kept[0].source, "dem");
        assert_eq!(drops.len(), 1);
        assert_eq!(drops[0].dropped.source, "gnis");
        assert!(drops[0].dist_m < 300.0);
    }
}
