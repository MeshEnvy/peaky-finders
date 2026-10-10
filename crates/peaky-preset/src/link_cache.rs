//! Per-pair RF link cache under ``<project>/links/<a>/<b>.json``.

use std::collections::HashSet;
use std::fs;
use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};

use crate::project::ProjectLayout;

/// Bumps when cached leg semantics or fingerprint inputs change.
pub const LINKS_MODEL: &str = "p2p-v8";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum LinkCacheKind {
    TwoWay,
    OneWay,
    None,
}

impl LinkCacheKind {
    pub fn as_str(self) -> &'static str {
        match self {
            LinkCacheKind::TwoWay => "two_way",
            LinkCacheKind::OneWay => "one_way",
            LinkCacheKind::None => "none",
        }
    }

    pub fn renders_on_map(self) -> bool {
        matches!(self, LinkCacheKind::TwoWay | LinkCacheKind::OneWay)
    }

    pub fn mesh_linked(self, manual: bool) -> bool {
        manual || self == LinkCacheKind::TwoWay
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct PairLinkCacheRecord {
    pub a: String,
    pub b: String,
    pub fingerprint: String,
    pub links_model: String,
    #[serde(default)]
    pub forward_margin_db: Option<f64>,
    #[serde(default)]
    pub reverse_margin_db: Option<f64>,
    pub kind: LinkCacheKind,
    pub distance_km: f64,
    pub at: String,
}

pub fn canonical_link_pair(slug_a: &str, slug_b: &str) -> (String, String) {
    let a = slug_a.trim();
    let b = slug_b.trim();
    if a <= b {
        (a.to_string(), b.to_string())
    } else {
        (b.to_string(), a.to_string())
    }
}

pub fn pair_cache_path(layout: &ProjectLayout, slug_a: &str, slug_b: &str) -> PathBuf {
    let (a, b) = canonical_link_pair(slug_a, slug_b);
    layout.links_dir().join(a).join(format!("{b}.json"))
}

pub fn read_pair_cache(layout: &ProjectLayout, slug_a: &str, slug_b: &str) -> Result<Option<PairLinkCacheRecord>> {
    let path = pair_cache_path(layout, slug_a, slug_b);
    if !path.is_file() {
        return Ok(None);
    }
    let text = fs::read_to_string(&path)
        .with_context(|| format!("read link cache: {}", path.display()))?;
    let record: PairLinkCacheRecord = serde_json::from_str(&text)
        .with_context(|| format!("parse link cache: {}", path.display()))?;
    Ok(Some(record))
}

pub fn write_pair_cache(layout: &ProjectLayout, record: &PairLinkCacheRecord) -> Result<()> {
    let path = pair_cache_path(layout, &record.a, &record.b);
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .with_context(|| format!("create link cache dir: {}", parent.display()))?;
    }
    let tmp = path.with_extension("json.partial");
    let text = serde_json::to_string_pretty(record).context("serialize link cache")?;
    fs::write(&tmp, format!("{text}\n"))
        .with_context(|| format!("write link cache temp: {}", tmp.display()))?;
    fs::rename(&tmp, &path).with_context(|| format!("replace link cache: {}", path.display()))?;
    Ok(())
}

/// Remove cache files for slugs no longer in the book.
pub fn prune_link_cache(layout: &ProjectLayout, site_slugs: &HashSet<String>) -> Result<()> {
    let root = layout.links_dir();
    if !root.is_dir() {
        return Ok(());
    }
    for entry in fs::read_dir(&root).with_context(|| format!("read links dir: {}", root.display()))? {
        let entry = entry?;
        let path = entry.path();
        if !path.is_dir() {
            continue;
        }
        let slug_a = entry
            .file_name()
            .to_str()
            .unwrap_or("")
            .to_string();
        if !site_slugs.contains(&slug_a) {
            fs::remove_dir_all(&path)
                .with_context(|| format!("remove stale link cache dir: {}", path.display()))?;
            continue;
        }
        for file in fs::read_dir(&path).with_context(|| format!("read link cache subdir: {}", path.display()))? {
            let file = file?;
            let file_path = file.path();
            if !file_path.is_file() {
                continue;
            }
            let slug_b = file_path
                .file_stem()
                .and_then(|s| s.to_str())
                .unwrap_or("");
            if !site_slugs.contains(slug_b) {
                fs::remove_file(&file_path)
                    .with_context(|| format!("remove stale link cache file: {}", file_path.display()))?;
            }
        }
    }
    Ok(())
}

pub fn classify_link_kind(forward: Option<f64>, reverse: Option<f64>) -> LinkCacheKind {
    let f = forward.is_some_and(|m| m.is_finite());
    let r = reverse.is_some_and(|m| m.is_finite());
    if f && r {
        LinkCacheKind::TwoWay
    } else if f || r {
        LinkCacheKind::OneWay
    } else {
        LinkCacheKind::None
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashSet;

    #[test]
    fn canonical_pair_orders() {
        assert_eq!(
            canonical_link_pair("b", "a"),
            ("a".to_string(), "b".to_string())
        );
    }

    #[test]
    fn classify_legs() {
        assert_eq!(
            classify_link_kind(Some(3.0), Some(1.0)),
            LinkCacheKind::TwoWay
        );
        assert_eq!(
            classify_link_kind(Some(3.0), None),
            LinkCacheKind::OneWay
        );
        assert_eq!(classify_link_kind(None, None), LinkCacheKind::None);
    }

    #[test]
    fn pair_cache_round_trip() {
        let dir = tempfile::tempdir().unwrap();
        fs::write(dir.path().join("config.yaml"), "simulation: {}\n").unwrap();
        let layout = ProjectLayout::from_config_path(&dir.path().join("config.yaml")).unwrap();
        let record = PairLinkCacheRecord {
            a: "alpha".into(),
            b: "beta".into(),
            fingerprint: "fp1".into(),
            links_model: LINKS_MODEL.into(),
            forward_margin_db: Some(2.0),
            reverse_margin_db: Some(1.0),
            kind: LinkCacheKind::TwoWay,
            distance_km: 12.3,
            at: "2026-10-09".into(),
        };
        write_pair_cache(&layout, &record).unwrap();
        let read = read_pair_cache(&layout, "beta", "alpha").unwrap().unwrap();
        assert_eq!(read.a, "alpha");
        assert_eq!(read.b, "beta");
        assert_eq!(read.kind, LinkCacheKind::TwoWay);
    }

    #[test]
    fn prune_drops_unknown_slug() {
        let dir = tempfile::tempdir().unwrap();
        fs::write(dir.path().join("config.yaml"), "simulation: {}\n").unwrap();
        let layout = ProjectLayout::from_config_path(&dir.path().join("config.yaml")).unwrap();
        let record = PairLinkCacheRecord {
            a: "gone".into(),
            b: "stay".into(),
            fingerprint: "fp".into(),
            links_model: LINKS_MODEL.into(),
            forward_margin_db: None,
            reverse_margin_db: None,
            kind: LinkCacheKind::None,
            distance_km: 0.0,
            at: "2026-10-09".into(),
        };
        write_pair_cache(&layout, &record).unwrap();
        let mut slugs = HashSet::new();
        slugs.insert("stay".into());
        prune_link_cache(&layout, &slugs).unwrap();
        assert!(!layout.links_dir().join("gone").exists());
    }
}
