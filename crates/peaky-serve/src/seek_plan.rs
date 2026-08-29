//! Persist goal-seek committed path under ``seek.plan`` in project YAML.

use std::collections::{HashMap, HashSet};
use std::path::Path;

use anyhow::{Context, Result};
use peaky_preset::{
    insert_preset_site, load_preset, load_preset_raw, normalize_site_tags, patch_preset_sites_tags,
    site_row_from_entry, slugify, unique_site_slug, write_preset_document, SeekPlan, SeekPlanHop,
    SiteEntry,
};
use serde_json::{json, Value};
use serde_yaml::Mapping;

#[derive(Debug, thiserror::Error)]
#[error("{0}")]
pub struct SeekPlanError(pub String);

fn plan_hop_to_yaml(hop: &SeekPlanHop) -> Mapping {
    let mut row = Mapping::new();
    if let Some(site) = &hop.site {
        row.insert(
            serde_yaml::Value::from("site"),
            serde_yaml::Value::from(site.clone()),
        );
        return row;
    }
    if let Some(loc) = hop.loc {
        row.insert(
            serde_yaml::Value::from("loc"),
            serde_yaml::Value::from(vec![loc[0], loc[1]]),
        );
    }
    if let Some(h) = hop.height_m {
        row.insert(serde_yaml::Value::from("height_m"), serde_yaml::Value::from(h));
    }
    row
}

pub fn seek_plan_to_api(plan: &Option<SeekPlan>) -> Value {
    match plan {
        None => Value::Null,
        Some(plan) => json!({
            "start": plan.start,
            "goal": [plan.goal[0], plan.goal[1]],
            "complete": plan.complete,
            "hops": plan.hops.iter().map(plan_hop_to_json).collect::<Vec<_>>(),
        }),
    }
}

fn plan_hop_to_json(hop: &SeekPlanHop) -> Value {
    if let Some(site) = &hop.site {
        return json!({ "site": site });
    }
    let mut row = json!({ "loc": hop.loc });
    if let Some(h) = hop.height_m {
        row["height_m"] = json!(h);
    }
    row
}

pub fn load_seek_plan_payload(preset_path: &Path) -> Result<Value> {
    let preset = load_preset(preset_path)?;
    Ok(seek_plan_to_api(&preset.seek.plan))
}

pub fn patch_seek_plan(preset_path: &Path, body: &Value) -> Result<Value, SeekPlanError> {
    let plan: SeekPlan = serde_json::from_value(body.clone())
        .map_err(|e| SeekPlanError(format!("invalid seek plan: {e}")))?;
    if plan.start.trim().is_empty() {
        return Err(SeekPlanError("start is required".into()));
    }
    if plan.hops.is_empty() {
        return Err(SeekPlanError("hops must be a non-empty list".into()));
    }
    for hop in &plan.hops {
        if hop.site.is_none() && hop.loc.is_none() {
            return Err(SeekPlanError("each hop requires site or loc".into()));
        }
    }

    persist_seek_plan(preset_path, &plan)?;
    Ok(seek_plan_to_api(&Some(plan)))
}

fn persist_seek_plan(preset_path: &Path, plan: &SeekPlan) -> Result<(), SeekPlanError> {
    let mut raw = load_preset_raw(preset_path).map_err(|e| SeekPlanError(e.to_string()))?;
    let map = raw
        .as_mapping_mut()
        .ok_or_else(|| SeekPlanError("preset root must be a mapping".into()))?;
    let seek = map
        .entry(serde_yaml::Value::from("seek"))
        .or_insert_with(|| serde_yaml::Value::Mapping(Mapping::new()));
    if let serde_yaml::Value::Mapping(seek_map) = seek {
        seek_map.insert(
            serde_yaml::Value::from("plan"),
            serde_yaml::to_value(plan).map_err(|e| SeekPlanError(e.to_string()))?,
        );
    }
    write_preset_document(preset_path, map).map_err(|e| SeekPlanError(e.to_string()))?;
    Ok(())
}

pub fn clear_seek_plan(preset_path: &Path) -> Result<(), SeekPlanError> {
    let mut raw = load_preset_raw(preset_path).map_err(|e| SeekPlanError(e.to_string()))?;
    let map = raw.as_mapping_mut().ok_or_else(|| SeekPlanError("preset root must be a mapping".into()))?;
    if let Some(seek) = map.get_mut(&serde_yaml::Value::from("seek")) {
        if let serde_yaml::Value::Mapping(seek_map) = seek {
            seek_map.remove(&serde_yaml::Value::from("plan"));
            if seek_map.is_empty() {
                map.remove(&serde_yaml::Value::from("seek"));
            }
        }
    }
    write_preset_document(preset_path, map).map_err(|e| SeekPlanError(e.to_string()))?;
    Ok(())
}

fn loc_dedupe_key(loc: [f64; 2], height_m: Option<f64>) -> String {
    let hm = height_m.map(|h| (h * 10.0).round() / 10.0);
    format!(
        "{:.6},{:.6},{}",
        loc[0],
        loc[1],
        hm.map(|h| h.to_string()).unwrap_or_else(|| "null".into())
    )
}

/// Highest trailing number on an existing site whose name or slug matches ``prefix``.
fn max_existing_prefixed_site_number(
    sites: &HashMap<String, SiteEntry>,
    prefix: &str,
) -> u32 {
    let prefix = prefix.trim();
    if prefix.is_empty() {
        return 0;
    }
    let slug_base = slugify(prefix);
    let mut max_num = 0u32;
    for (slug, site) in sites {
        if let Some(n) = parse_prefixed_site_name_number(&site.name, prefix) {
            max_num = max_num.max(n);
        }
        if let Some(n) = parse_prefixed_site_slug_number(slug, &slug_base) {
            max_num = max_num.max(n);
        }
    }
    max_num
}

fn parse_prefixed_site_name_number(name: &str, prefix: &str) -> Option<u32> {
    let name = name.trim();
    let prefix = prefix.trim();
    if prefix.is_empty() || name.len() < prefix.len() {
        return None;
    }
    if !name[..prefix.len()].eq_ignore_ascii_case(prefix) {
        return None;
    }
    let rest = name[prefix.len()..].trim_start();
    if rest.is_empty() || !rest.chars().all(|c| c.is_ascii_digit()) {
        return None;
    }
    rest.parse().ok()
}

fn parse_prefixed_site_slug_number(slug: &str, slug_base: &str) -> Option<u32> {
    if slug_base.is_empty() || slug == slug_base {
        return None;
    }
    if !slug.starts_with(slug_base) {
        return None;
    }
    let mut rest = &slug[slug_base.len()..];
    if let Some(stripped) = rest.strip_prefix('-') {
        rest = stripped;
    }
    let digits: String = rest.chars().take_while(|c| c.is_ascii_digit()).collect();
    if digits.is_empty() {
        return None;
    }
    digits.parse().ok()
}

fn plan_path_site_slugs(start: &str, hops: &[Mapping]) -> Vec<String> {
    let mut slugs = vec![start.to_string()];
    let mut seen = HashSet::from([start.to_string()]);
    for hop in hops {
        if let Some(site) = hop.get(&serde_yaml::Value::from("site")).and_then(|v| v.as_str()) {
            let site = site.trim();
            if site.is_empty() || seen.contains(site) {
                continue;
            }
            seen.insert(site.to_string());
            slugs.push(site.to_string());
        }
    }
    slugs
}

pub fn convert_seek_plan_locs_to_sites(
    preset_path: &Path,
    name_prefix: &str,
    tags: &[String],
) -> Result<Value, SeekPlanError> {
    let prefix = name_prefix.trim();
    if prefix.is_empty() {
        return Err(SeekPlanError("name_prefix is required".into()));
    }
    let tags_value = normalize_site_tags(Some(&serde_yaml::Value::Sequence(
        tags.iter()
            .map(|t| serde_yaml::Value::from(t.clone()))
            .collect(),
    )))
    .map_err(|e| SeekPlanError(e))?;
    if tags_value.is_empty() {
        return Err(SeekPlanError("tags must be a non-empty list".into()));
    }

    let preset = load_preset(preset_path).map_err(|e| SeekPlanError(e.to_string()))?;
    let plan = preset
        .seek
        .plan
        .clone()
        .ok_or_else(|| SeekPlanError("no seek plan".into()))?;
    if !plan.hops.iter().any(|h| h.loc.is_some()) {
        return Err(SeekPlanError("no coordinate hops to convert".into()));
    }

    let mut preset = preset;
    let mut existing: HashSet<String> = preset.sites.keys().cloned().collect();
    let mut coord_to_slug: HashMap<String, String> = HashMap::new();
    let mut loc_counter = max_existing_prefixed_site_number(&preset.sites, prefix);
    let mut created_rows = Vec::new();
    let mut new_hops = Vec::new();

    for hop in &plan.hops {
        if let Some(site) = &hop.site {
            new_hops.push(SeekPlanHop {
                site: Some(site.clone()),
                loc: None,
                height_m: None,
            });
            continue;
        }
        let loc = hop.loc.context("hop loc").map_err(|e| SeekPlanError(e.to_string()))?;
        let key = loc_dedupe_key(loc, hop.height_m);
        let slug = if let Some(slug) = coord_to_slug.get(&key) {
            slug.clone()
        } else {
            loc_counter += 1;
            let name = format!("{prefix} {loc_counter}");
            let slug = unique_site_slug(&existing, &name);
            existing.insert(slug.clone());
            coord_to_slug.insert(key, slug.clone());
            let entry = SiteEntry {
                name,
                loc,
                tags: tags_value.clone(),
                height_m: hop.height_m,
                description: None,
            };
            preset.sites.insert(slug.clone(), entry.clone());
            created_rows.push(json!(site_row_from_entry(&slug, &entry)));
            slug
        };
        new_hops.push(SeekPlanHop {
            site: Some(slug),
            loc: None,
            height_m: None,
        });
    }

    let created_slug_list: Vec<String> = created_rows
        .iter()
        .filter_map(|row| row.get("slug").and_then(|v| v.as_str()).map(str::to_string))
        .collect();
    let created_slugs: HashSet<String> = created_slug_list.iter().cloned().collect();
    let mut tagged_rows = Vec::new();
    let yaml_hops: Vec<Mapping> = new_hops.iter().map(plan_hop_to_yaml).collect();
    for slug in plan_path_site_slugs(&plan.start, &yaml_hops) {
        if created_slugs.contains(&slug) {
            continue;
        }
        if let Some(site) = preset.sites.get_mut(&slug) {
            let current: HashSet<String> = site.tags.iter().cloned().collect();
            let merged: HashSet<String> = current.union(&tags_value.iter().cloned().collect()).cloned().collect();
            if merged != current {
                site.tags = merged.into_iter().collect::<Vec<_>>();
                site.tags.sort();
                tagged_rows.push(json!(site_row_from_entry(&slug, site)));
            }
        }
    }

    let path_slugs = plan_path_site_slugs(&plan.start, &yaml_hops);
    let tagged_slugs: Vec<String> = tagged_rows
        .iter()
        .filter_map(|row| row.get("slug").and_then(|v| v.as_str()).map(str::to_string))
        .collect();
    for slug in &created_slug_list {
        let entry = preset
            .sites
            .get(slug)
            .ok_or_else(|| SeekPlanError(format!("missing created site {slug}")))?;
        insert_preset_site(preset_path, slug, entry).map_err(|e| SeekPlanError(e.to_string()))?;
    }
    if !tagged_slugs.is_empty() {
        patch_preset_sites_tags(preset_path, &tagged_slugs, &tags_value, &[])
            .map_err(|e| SeekPlanError(e.to_string()))?;
    }

    let new_plan = SeekPlan {
        start: plan.start,
        goal: plan.goal,
        complete: plan.complete,
        hops: new_hops,
    };
    persist_seek_plan(preset_path, &new_plan)?;
    preset.seek.plan = Some(new_plan);

    let converted = created_rows.len();
    let tagged = tagged_rows.len();
    Ok(json!({
        "sites": created_rows.into_iter().chain(tagged_rows).collect::<Vec<_>>(),
        "created_slugs": created_slug_list,
        "path_slugs": path_slugs,
        "plan": seek_plan_to_api(&preset.seek.plan),
        "converted": converted,
        "tagged": tagged,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn site(name: &str) -> SiteEntry {
        SiteEntry {
            name: name.into(),
            loc: [39.0, -119.0],
            tags: vec![],
            height_m: None,
            description: None,
        }
    }

    #[test]
    fn max_prefixed_number_from_names_and_slugs() {
        let mut sites = HashMap::new();
        sites.insert("relay1".into(), site("Relay 1"));
        sites.insert("relay5".into(), site("Relay 5"));
        sites.insert("relay1-2".into(), site("Relay 1"));
        sites.insert("fox-mountain".into(), site("Fox Mountain"));
        assert_eq!(max_existing_prefixed_site_number(&sites, "Relay"), 5);
        assert_eq!(max_existing_prefixed_site_number(&sites, "relay"), 5);
        assert_eq!(max_existing_prefixed_site_number(&sites, "Fox"), 0);
    }

    #[test]
    fn parse_prefixed_name_requires_numeric_suffix() {
        assert_eq!(parse_prefixed_site_name_number("Relay 12", "Relay"), Some(12));
        assert_eq!(parse_prefixed_site_name_number("relay 3", "Relay"), Some(3));
        assert!(parse_prefixed_site_name_number("Relay Peak", "Relay").is_none());
    }

    fn write_convert_fixture(dir: &std::path::Path) -> std::path::PathBuf {
        let config = r#"modem_presets:
  fixture-modem:
    frequency_mhz: 915.0
    bandwidth_khz: 125.0
    spreading_factor: 10
    coding_rate: 5
    implementation_margin_db: 3.0
    power_dbm: 22.0
    sensitivity_dbm: -132.0
environment_presets:
  fixture-desert:
    climate: desert
    polarization: vertical
    clutter_height_m: 1.0
    fresnel_clearance_fraction: 0.25
    coverage_pessimism_db: 0.0
    situation_pct: 95.0
    time_pct: 95.0
simulation:
  radius_km: 50.0
  modem: fixture-modem
  environment: fixture-desert
  transmitter: {height_m: 2.0, gain_dbi: 3.0, loss_db: 2.0}
  receiver: {height_m: 2.0, gain_dbi: 3.0, loss_db: 2.0}
seek:
  peak_bin_size_m: 1500
  max_candidates: 4
  plan:
    start: start
    goal: [40.25, -119.25]
    hops:
      - loc: [40.05, -119.45]
sites:
  start:
    name: Start
    loc: [40.0, -119.5]
    tags: [installed]
links: []
"#;
        let path = dir.join("config.yaml");
        std::fs::write(&path, config).expect("config");
        path
    }

    #[test]
    fn convert_loc_hop_becomes_site_and_keeps_path() {
        let tmp = tempfile::TempDir::new().expect("tempdir");
        let path = write_convert_fixture(tmp.path());
        let result =
            convert_seek_plan_locs_to_sites(&path, "Relay", &["planned".into()]).expect("convert");
        assert_eq!(result["converted"], 1);
        let created = result["created_slugs"]
            .as_array()
            .expect("created_slugs");
        assert_eq!(created.len(), 1);
        let slug = created[0].as_str().expect("slug");
        let path_slugs = result["path_slugs"].as_array().expect("path_slugs");
        assert!(path_slugs.iter().any(|v| v.as_str() == Some("start")));
        assert!(path_slugs.iter().any(|v| v.as_str() == Some(slug)));

        let preset = load_preset(&path).expect("reload");
        let site = preset.sites.get(slug).expect("created site");
        assert_eq!(site.loc, [40.05, -119.45]);
        assert!(site.tags.contains(&"planned".to_string()));
        let plan = preset.seek.plan.expect("plan");
        assert_eq!(plan.hops.len(), 1);
        assert_eq!(plan.hops[0].site.as_deref(), Some(slug));
        assert!(plan.hops[0].loc.is_none());
    }
}
