//! Candidate registry: tagged existing sites at start; peaks added on demand during search.

use std::collections::HashSet;
use std::path::Path;

use anyhow::Result;
use peaky_preset::{load_preset, Preset, SiteEntry};
use serde::{Deserialize, Serialize};
use serde_json::json;
use splatter::peaks::Peak;
use splatter::propagate::haversine_m;

use crate::cache::{digest_hex, FinderCache};
use crate::route::Waypoint;
use crate::telemetry::CacheLedger;
use crate::SITE_PEAK_DEDUP_M;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub enum CandidateKind {
    Existing,
    Peak,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Candidate {
    pub id: String,
    pub kind: CandidateKind,
    pub lat: f64,
    pub lon: f64,
    pub height_m: Option<f64>,
    pub slug: Option<String>,
}

/// Growable candidate list: existing sites first, peaks registered during wedge search.
#[derive(Debug, Clone, Default)]
pub struct CandidateRegistry {
    pub candidates: Vec<Candidate>,
    next_peak_id: usize,
}

impl CandidateRegistry {
    pub fn new(existing: Vec<Candidate>) -> Self {
        Self {
            candidates: existing,
            next_peak_id: 0,
        }
    }

    pub fn len(&self) -> usize {
        self.candidates.len()
    }

    pub fn is_empty(&self) -> bool {
        self.candidates.is_empty()
    }

    pub fn as_slice(&self) -> &[Candidate] {
        &self.candidates
    }

    pub fn get(&self, idx: usize) -> Option<&Candidate> {
        self.candidates.get(idx)
    }

    /// Return index of an existing candidate within dedup distance, if any.
    pub fn find_near(&self, lat: f64, lon: f64) -> Option<usize> {
        for (i, c) in self.candidates.iter().enumerate() {
            if haversine_m(lat, lon, c.lat, c.lon) <= SITE_PEAK_DEDUP_M {
                return Some(i);
            }
        }
        None
    }

    /// Register a discovered peak (deduped). Returns candidate index.
    pub fn register_peak(&mut self, peak: &Peak) -> usize {
        if let Some(idx) = self.find_near(peak.lat, peak.lon) {
            return idx;
        }
        self.next_peak_id += 1;
        let id = format!("peak-{}", self.next_peak_id);
        let idx = self.candidates.len();
        // height_m is antenna AGL (preset default when None) — never DEM elev.
        self.candidates.push(Candidate {
            id,
            kind: CandidateKind::Peak,
            lat: peak.lat,
            lon: peak.lon,
            height_m: None,
            slug: None,
        });
        idx
    }

    pub fn find_by_slug(&self, slug: &str) -> Option<usize> {
        self.candidates
            .iter()
            .position(|c| c.slug.as_deref() == Some(slug))
    }
}

/// Snapshot chain sites for run cache (stable across registry index shifts).
pub fn path_candidates(registry: &CandidateRegistry, path_indices: &[usize]) -> Result<Vec<Candidate>> {
    path_indices
        .iter()
        .map(|&idx| {
            registry
                .get(idx)
                .cloned()
                .ok_or_else(|| anyhow::anyhow!("path index {idx} out of range for registry len {}", registry.len()))
        })
        .collect()
}

/// Rehydrate registry indices from cached chain sites after a run cache hit.
pub fn restore_path_from_cached(registry: &mut CandidateRegistry, path: &[Candidate]) -> Result<Vec<usize>> {
    let mut out = Vec::with_capacity(path.len());
    for site in path {
        let idx = match site.kind {
            CandidateKind::Existing => {
                if let Some(ref slug) = site.slug {
                    if let Some(i) = registry.find_by_slug(slug) {
                        i
                    } else if let Some(i) = registry.find_near(site.lat, site.lon) {
                        i
                    } else {
                        anyhow::bail!("cached existing site {slug} not in preset");
                    }
                } else if let Some(i) = registry.find_near(site.lat, site.lon) {
                    i
                } else {
                    anyhow::bail!(
                        "cached existing site at {:.6},{:.6} not in preset",
                        site.lat,
                        site.lon
                    );
                }
            }
            CandidateKind::Peak => registry.register_peak(&Peak {
                lat: site.lat,
                lon: site.lon,
                // elev only for Peak scan metadata; Candidate.height_m stays antenna AGL (None).
                elev_m: 1.0,
            }),
        };
        out.push(idx);
    }
    Ok(out)
}

pub fn corridor_bbox(waypoints: &[Waypoint], radius_m: f64) -> (f64, f64, f64, f64) {
    let mut south = f64::INFINITY;
    let mut north = f64::NEG_INFINITY;
    let mut west = f64::INFINITY;
    let mut east = f64::NEG_INFINITY;
    for wp in waypoints {
        south = south.min(wp.lat);
        north = north.max(wp.lat);
        west = west.min(wp.lon);
        east = east.max(wp.lon);
    }
    let delta_deg = radius_m / 6_378_137.0 * (180.0 / std::f64::consts::PI);
    let mid_lat = (south + north) / 2.0;
    let cos_lat = mid_lat.to_radians().cos().max(0.01);
    let lon_delta = delta_deg / cos_lat;
    (
        west - lon_delta,
        south - delta_deg,
        east + lon_delta,
        north + delta_deg,
    )
}

pub fn tagged_existing_sites(preset: &Preset, allow_tags: &[String]) -> Vec<(String, SiteEntry)> {
    let allow: HashSet<&str> = allow_tags.iter().map(|s| s.as_str()).collect();
    let mut out = Vec::new();
    for (slug, site) in &preset.sites {
        if site.tags.iter().any(|t| allow.contains(t.as_str())) {
            out.push((slug.clone(), site.clone()));
        }
    }
    out.sort_by(|a, b| a.0.cmp(&b.0));
    out
}

/// Load only `--allow-tag` existing sites. Peaks are discovered during on-demand search.
pub fn build_candidates(
    preset_path: &Path,
    _waypoints: &[Waypoint],
    allow_tags: &[String],
    _cache: &FinderCache,
    ledger: &CacheLedger,
) -> Result<CandidateRegistry> {
    ledger.phase_start("candidates");
    let preset = load_preset(preset_path)?;
    let existing = tagged_existing_sites(&preset, allow_tags);
    ledger.progress(&format!(
        "existing tagged sites={} allow_tags=[{}]",
        existing.len(),
        allow_tags.join(",")
    ));
    if let Some(hub) = ledger.watch() {
        hub.publish(
            "existing",
            json!({
                "sites": existing.iter().map(|(slug, site)| json!({
                    "slug": slug,
                    "name": site.name,
                    "lat": site.lat(),
                    "lon": site.lon(),
                })).collect::<Vec<_>>()
            }),
        );
    }
    let candidates: Vec<Candidate> = existing
        .iter()
        .map(|(slug, site)| Candidate {
            id: slug.clone(),
            kind: CandidateKind::Existing,
            lat: site.lat(),
            lon: site.lon(),
            height_m: site.height_m,
            slug: Some(slug.clone()),
        })
        .collect();

    ledger.phase_end("candidates", &format!("existing={}", candidates.len()));
    if let Some(hub) = ledger.watch() {
        hub.publish("candidates", json!({ "count": candidates.len() }));
    }
    Ok(CandidateRegistry::new(candidates))
}

pub fn sites_version_digest(preset: &Preset) -> String {
    let mut parts: Vec<String> = preset
        .sites
        .iter()
        .map(|(slug, site)| format!("{slug}:{:.6},{:.6}", site.lat(), site.lon()))
        .collect();
    parts.sort();
    digest_hex(&[&parts.join(";")])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn restore_path_from_cached_reregisters_peaks() {
        let mut reg = CandidateRegistry::new(vec![Candidate {
            id: "site-a".into(),
            kind: CandidateKind::Existing,
            lat: 39.0,
            lon: -116.0,
            height_m: Some(3000.0),
            slug: Some("site-a".into()),
        }]);
        let cached = vec![
            Candidate {
                id: "site-a".into(),
                kind: CandidateKind::Existing,
                lat: 39.0,
                lon: -116.0,
                height_m: Some(3000.0),
                slug: Some("site-a".into()),
            },
            Candidate {
                id: "peak-1".into(),
                kind: CandidateKind::Peak,
                lat: 39.01,
                lon: -116.01,
                height_m: Some(2500.0),
                slug: None,
            },
        ];
        let path = restore_path_from_cached(&mut reg, &cached).unwrap();
        assert_eq!(path, vec![0, 1]);
        assert_eq!(reg.len(), 2);
        // Peak antenna height is preset default (None), never cached DEM elev.
        assert_eq!(reg.get(1).unwrap().height_m, None);
    }

    #[test]
    fn register_peak_dedupes_nearby() {
        let mut reg = CandidateRegistry::default();
        let p1 = Peak {
            lat: 39.0,
            lon: -116.0,
            elev_m: 2000.0,
        };
        let p2 = Peak {
            lat: 39.0001,
            lon: -116.0001,
            elev_m: 2100.0,
        };
        let i0 = reg.register_peak(&p1);
        let i1 = reg.register_peak(&p2);
        assert_eq!(i0, i1);
        assert_eq!(reg.len(), 1);
        assert_eq!(reg.get(0).unwrap().height_m, None);
    }
}
