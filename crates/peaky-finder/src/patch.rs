//! Merge discovered sites and links into preset YAML.

use std::collections::{HashSet, HashMap};
use std::io::{self, Write};
use std::path::Path;

use anyhow::{Context, Result};
use peaky_preset::{
    load_preset_raw, site_row_from_entry, slugify, unique_site_slug, write_preset_document,
    Preset, SiteEntry,
};
use serde::{Deserialize, Serialize};
use serde_yaml::Mapping;

use crate::candidates::{Candidate, CandidateKind};
use crate::job::FindPathJob;
use crate::solve::SolveResult;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NewSite {
    pub slug: String,
    pub name: String,
    pub lat: f64,
    pub lon: f64,
    pub height_m: Option<f64>,
    pub tags: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PatchPlan {
    pub new_sites: Vec<NewSite>,
    pub link_pairs: Vec<[String; 2]>,
    pub chain_slugs: Vec<String>,
}

pub fn build_patch_plan(
    job: &FindPathJob,
    preset: &Preset,
    candidates: &[Candidate],
    solve: &SolveResult,
) -> PatchPlan {
    let mut existing: HashSet<String> = preset.sites.keys().cloned().collect();
    let mut counter = max_prefixed_number(&preset, &job.name_prefix);
    let mut coord_to_slug: HashMap<String, String> = HashMap::new();
    let mut new_sites = Vec::new();
    let mut chain_slugs = Vec::new();

    for &idx in &solve.path_indices {
        let cand = &candidates[idx];
        match &cand.kind {
            CandidateKind::Existing => {
                if let Some(slug) = &cand.slug {
                    chain_slugs.push(slug.clone());
                }
            }
            CandidateKind::Peak => {
                let key = format!("{:.6},{:.6}", cand.lat, cand.lon);
                let slug = if let Some(s) = coord_to_slug.get(&key) {
                    s.clone()
                } else {
                    counter += 1;
                    let name = format!("{} {counter}", job.name_prefix);
                    let slug = unique_site_slug(&existing, &name);
                    existing.insert(slug.clone());
                    coord_to_slug.insert(key, slug.clone());
                    new_sites.push(NewSite {
                        slug: slug.clone(),
                        name,
                        lat: cand.lat,
                        lon: cand.lon,
                        height_m: cand.height_m,
                        tags: job.new_tags.clone(),
                    });
                    slug
                };
                chain_slugs.push(slug);
            }
        }
    }

    chain_slugs = dedupe_consecutive_slugs(chain_slugs);
    let mut link_pairs = Vec::new();
    for pair in chain_slugs.windows(2) {
        let a = pair[0].clone();
        let b = pair[1].clone();
        if a != b {
            link_pairs.push([a, b]);
        }
    }

    PatchPlan {
        new_sites,
        link_pairs,
        chain_slugs,
    }
}

fn dedupe_consecutive_slugs(slugs: Vec<String>) -> Vec<String> {
    let mut out = Vec::new();
    for s in slugs {
        if out.last() == Some(&s) {
            continue;
        }
        out.push(s);
    }
    out
}

fn max_prefixed_number(preset: &Preset, prefix: &str) -> u32 {
    let slug_base = slugify(prefix);
    let mut max_num = 0u32;
    for (slug, site) in &preset.sites {
        if let Some(n) = parse_name_number(&site.name, prefix) {
            max_num = max_num.max(n);
        }
        if slug.starts_with(&slug_base) {
            let rest = slug.strip_prefix(&slug_base).unwrap_or(slug);
            let digits: String = rest
                .trim_start_matches('-')
                .chars()
                .take_while(|c| c.is_ascii_digit())
                .collect();
            if let Ok(n) = digits.parse::<u32>() {
                max_num = max_num.max(n);
            }
        }
    }
    max_num
}

fn parse_name_number(name: &str, prefix: &str) -> Option<u32> {
    let name = name.trim();
    let prefix = prefix.trim();
    if name.len() < prefix.len() || !name[..prefix.len()].eq_ignore_ascii_case(prefix) {
        return None;
    }
    let rest = name[prefix.len()..].trim_start();
    if rest.is_empty() || !rest.chars().all(|c| c.is_ascii_digit()) {
        return None;
    }
    rest.parse().ok()
}

pub fn print_patch_diff(plan: &PatchPlan) {
    let _ = writeln!(io::stderr(), "finder dry-run: +{} sites, +{} links", plan.new_sites.len(), plan.link_pairs.len());
    for site in &plan.new_sites {
        let _ = writeln!(
            io::stderr(),
            "  + sites/{}: {} [{:.5}, {:.5}] tags={:?}",
            site.slug, site.name, site.lat, site.lon, site.tags
        );
    }
    for [a, b] in &plan.link_pairs {
        let _ = writeln!(io::stderr(), "  + links: [{a}, {b}]");
    }
    let _ = writeln!(io::stderr(), "  chain: {}", plan.chain_slugs.join(" → "));
}

pub fn apply_patch(
    preset_path: &Path,
    plan: &PatchPlan,
    run_digest: &str,
    viewshed_popcount: u32,
) -> Result<()> {
    let mut raw = load_preset_raw(preset_path)?;
    let map = raw
        .as_mapping_mut()
        .context("preset root must be a mapping")?;

    let sites_val = map
        .entry(serde_yaml::Value::from("sites"))
        .or_insert_with(|| serde_yaml::Value::Mapping(Mapping::new()));
    if let serde_yaml::Value::Mapping(sites_map) = sites_val {
        for site in &plan.new_sites {
            let entry = SiteEntry {
                name: site.name.clone(),
                loc: [site.lat, site.lon],
                tags: site.tags.clone(),
                height_m: site.height_m,
                description: None,
            };
            sites_map.insert(
                serde_yaml::Value::from(site.slug.clone()),
                serde_yaml::to_value(&entry)?,
            );
        }
    }

    let links_val = map
        .entry(serde_yaml::Value::from("links"))
        .or_insert_with(|| serde_yaml::Value::Sequence(vec![]));
    if let serde_yaml::Value::Sequence(links_seq) = links_val {
        let existing_pairs: HashSet<(String, String)> = links_seq
            .iter()
            .filter_map(|v| {
                v.as_sequence().and_then(|seq| {
                    let a = seq.first()?.as_str()?.to_string();
                    let b = seq.get(1)?.as_str()?.to_string();
                    Some(normalize_pair(a, b))
                })
            })
            .collect();
        for [a, b] in &plan.link_pairs {
            let key = normalize_pair(a.clone(), b.clone());
            if existing_pairs.contains(&key) {
                continue;
            }
            links_seq.push(serde_yaml::Value::Sequence(vec![
                serde_yaml::Value::from(a.clone()),
                serde_yaml::Value::from(b.clone()),
            ]));
        }
    }

    let finder_meta = serde_yaml::Mapping::from_iter([
        (
            serde_yaml::Value::from("last_run_digest"),
            serde_yaml::Value::from(run_digest),
        ),
        (
            serde_yaml::Value::from("site_count"),
            serde_yaml::Value::from(plan.chain_slugs.len() as i64),
        ),
        (
            serde_yaml::Value::from("new_sites"),
            serde_yaml::Value::from(plan.new_sites.len() as i64),
        ),
        (
            serde_yaml::Value::from("viewshed_popcount"),
            serde_yaml::Value::from(viewshed_popcount as i64),
        ),
    ]);
    map.insert(
        serde_yaml::Value::from("finder"),
        serde_yaml::Value::Mapping(finder_meta),
    );

    write_preset_document(preset_path, map)?;
    let _ = writeln!(
        io::stderr(),
        "finder patch config.yaml +{} sites +{} links",
        plan.new_sites.len(),
        plan.link_pairs.len()
    );
    Ok(())
}

fn normalize_pair(a: String, b: String) -> (String, String) {
    if a <= b {
        (a, b)
    } else {
        (b, a)
    }
}

pub fn patch_summary_rows(plan: &PatchPlan, preset: &Preset) -> Vec<serde_json::Value> {
    let mut rows = Vec::new();
    for site in &plan.new_sites {
        let entry = SiteEntry {
            name: site.name.clone(),
            loc: [site.lat, site.lon],
            tags: site.tags.clone(),
            height_m: site.height_m,
            description: None,
        };
        rows.push(serde_json::to_value(site_row_from_entry(&site.slug, &entry)).unwrap_or_default());
    }
    for slug in &plan.chain_slugs {
        if let Some(entry) = preset.sites.get(slug) {
            rows.push(serde_json::to_value(site_row_from_entry(slug, entry)).unwrap_or_default());
        }
    }
    rows
}

#[cfg(test)]
mod tests {
    use super::*;
    use peaky_preset::Preset;

    #[test]
    fn chain_dedupes_consecutive() {
        assert_eq!(
            dedupe_consecutive_slugs(vec!["a".into(), "a".into(), "b".into()]),
            vec!["a", "b"]
        );
    }

    #[test]
    fn max_prefixed_parses_site_names() {
        let mut preset = Preset::default();
        preset.sites.insert(
            "site-3".into(),
            SiteEntry {
                name: "Site 3".into(),
                loc: [39.0, -119.0],
                tags: vec![],
                height_m: None,
                description: None,
            },
        );
        assert_eq!(max_prefixed_number(&preset, "Site"), 3);
    }
}
