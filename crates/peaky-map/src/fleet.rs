//! Deployed fleet sites from peaky-nevada ``nodes.yaml`` + ``sites.yaml``.

use std::path::{Path, PathBuf};

use anyhow::{bail, Context, Result};
use peaky_preset::SiteEntry;
use serde_yaml::Value;
use tracing::{debug, warn};

#[derive(Debug, Clone)]
pub struct FleetSite {
    pub slug: String,
    pub site: SiteEntry,
}

pub fn resolve_peaky_dir(path: &Path) -> Result<PathBuf> {
    let peaky = path.canonicalize().unwrap_or_else(|_| path.to_path_buf());
    let nodes = peaky.join("nodes.yaml");
    let sites = peaky.join("sites.yaml");
    if !nodes.is_file() {
        bail!("fleet book missing nodes.yaml: {}", nodes.display());
    }
    if !sites.is_file() {
        bail!("fleet book missing sites.yaml: {}", sites.display());
    }
    Ok(peaky)
}

fn node_key(raw: &Value) -> Option<String> {
    raw.as_str()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(|s| s.to_ascii_lowercase())
}

/// True when `decommissioned` is a real stamp (epoch). A null/empty key is the book placeholder for live units.
fn node_decommissioned(raw: &serde_yaml::Mapping) -> bool {
    match raw.get(serde_yaml::Value::from("decommissioned")) {
        None | Some(Value::Null) | Some(Value::Bool(false)) => false,
        Some(Value::String(s)) if s.trim().is_empty() => false,
        Some(Value::Number(n)) if n.as_i64() == Some(0) || n.as_u64() == Some(0) => false,
        Some(_) => true,
    }
}

fn site_coords(site_slug: &str, entry: &serde_yaml::Mapping) -> Option<[f64; 2]> {
    let loc = entry.get(serde_yaml::Value::from("loc"))?;
    match loc {
        Value::Sequence(seq) if seq.len() >= 2 => {
            let lat = seq[0].as_f64()?;
            let lon = seq[1].as_f64()?;
            Some([lat, lon])
        }
        _ => {
            warn!("fleet site {site_slug}: no valid loc in sites.yaml");
            None
        }
    }
}

fn site_entry_from_mapping(slug: &str, entry: &serde_yaml::Mapping) -> Option<SiteEntry> {
    let loc = site_coords(slug, entry)?;
    let name = entry
        .get(serde_yaml::Value::from("name"))
        .and_then(|v| v.as_str())
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .unwrap_or(slug)
        .to_string();
    let height_m = entry
        .get(serde_yaml::Value::from("height_m"))
        .and_then(|v| v.as_f64())
        .filter(|h| *h > 0.0);
    let description = entry
        .get(serde_yaml::Value::from("description"))
        .and_then(|v| v.as_str())
        .map(str::to_string);
    let tags: Vec<String> = entry
        .get(serde_yaml::Value::from("tags"))
        .and_then(|v| v.as_sequence())
        .map(|seq| {
            seq.iter()
                .filter_map(|t| t.as_str().map(str::to_string))
                .collect()
        })
        .unwrap_or_default();
    let node = entry
        .get(serde_yaml::Value::from("node"))
        .and_then(node_key);
    Some(SiteEntry {
        name,
        loc,
        height_m,
        description,
        tags,
        node,
    })
}

pub fn load_fleet_sites(peaky_dir: &Path) -> Result<Vec<FleetSite>> {
    let peaky = resolve_peaky_dir(peaky_dir)?;
    let nodes_path = peaky.join("nodes.yaml");
    let sites_path = peaky.join("sites.yaml");

    let book: Value = serde_yaml::from_str(
        &std::fs::read_to_string(&nodes_path).with_context(|| nodes_path.display().to_string())?,
    )?;
    let sites_doc: Value = serde_yaml::from_str(
        &std::fs::read_to_string(&sites_path).with_context(|| sites_path.display().to_string())?,
    )?;

    let nodes_raw = book
        .get("nodes")
        .and_then(|v| v.as_mapping())
        .context("nodes.yaml: nodes must be a mapping")?;
    let sites_raw = sites_doc
        .get("sites")
        .and_then(|v| v.as_mapping())
        .context("sites.yaml: sites must be a mapping")?;

    let mut out = Vec::new();
    for (slug_key, site_val) in sites_raw {
        let slug = slug_key.as_str().context("site slug must be string")?;
        let site_entry = site_val.as_mapping().context("site entry must be mapping")?;
        let node_key = match site_entry.get(serde_yaml::Value::from("node")).and_then(node_key) {
            Some(k) => k,
            None => continue,
        };
        let node = nodes_raw
            .get(serde_yaml::Value::from(node_key.as_str()))
            .and_then(|v| v.as_mapping());
        let Some(node) = node else {
            warn!("sites.{slug} node={node_key}: missing from nodes.yaml, skipping");
            continue;
        };
        if node_decommissioned(node) {
            continue;
        }
        let Some(site) = site_entry_from_mapping(slug, site_entry) else {
            continue;
        };
        debug!("fleet site {slug} from node {node_key}");
        out.push(FleetSite {
            slug: slug.to_string(),
            site,
        });
    }

    out.sort_by(|a, b| a.slug.cmp(&b.slug));
    tracing::info!("loaded {} deployed fleet site(s) from {}", out.len(), peaky.display());
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn mapping_with_decommissioned(val: Value) -> serde_yaml::Mapping {
        let mut m = serde_yaml::Mapping::new();
        m.insert(Value::from("decommissioned"), val);
        m
    }

    #[test]
    fn null_decommissioned_is_live() {
        assert!(!node_decommissioned(&mapping_with_decommissioned(Value::Null)));
        assert!(!node_decommissioned(&serde_yaml::Mapping::new()));
        assert!(!node_decommissioned(&mapping_with_decommissioned(Value::from(""))));
    }

    #[test]
    fn epoch_decommissioned_is_pulled() {
        assert!(node_decommissioned(&mapping_with_decommissioned(Value::from(1_787_943_600i64))));
    }
}
