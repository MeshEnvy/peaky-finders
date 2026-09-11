//! Candidate peak universe before access filtering.

use std::collections::HashSet;

use peaky_preset::{slugify_files_segment, Preset};

#[derive(Debug, Clone)]
pub struct RawCandidate {
    pub name: Option<String>,
    pub lat: f64,
    pub lon: f64,
    pub elev_m: Option<f64>,
    pub source: String,
    pub seed_slug: Option<String>,
}

pub fn seed_candidates_from_sites(preset: &Preset) -> Vec<RawCandidate> {
    let mut out = Vec::new();
    for (slug, site) in &preset.sites {
        let tags: HashSet<&str> = site.tags.iter().map(String::as_str).collect();
        let source = if tags.contains("eip") {
            "eip"
        } else if tags.contains("alertwildfire") {
            "alertwildfire"
        } else if tags.contains("installed") {
            "installed"
        } else {
            continue;
        };
        out.push(RawCandidate {
            name: Some(site.name.clone()),
            lat: site.lat(),
            lon: site.lon(),
            elev_m: None,
            source: source.into(),
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

pub fn dedup_nearby(candidates: Vec<RawCandidate>, min_m: f64) -> Vec<RawCandidate> {
    let mut kept: Vec<RawCandidate> = Vec::new();
    'outer: for c in candidates {
        for k in &kept {
            if crate::hike::haversine_m(c.lat, c.lon, k.lat, k.lon) < min_m {
                continue 'outer;
            }
        }
        kept.push(c);
    }
    kept
}
