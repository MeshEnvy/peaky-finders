//! Per-pair RF link records under ``<project>/links/<a>/<b>.json`` (computed RF + manual overrides).

use std::collections::{HashMap, HashSet};
use std::fs;
use std::path::PathBuf;
use std::sync::{LazyLock, Mutex};
use std::time::SystemTime;

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
    #[serde(default)]
    pub manual: bool,
    #[serde(default, rename = "fieldAt")]
    pub field_at: Option<String>,
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

fn merge_manual_fields(existing: &PairLinkCacheRecord, incoming: &PairLinkCacheRecord) -> PairLinkCacheRecord {
    let mut merged = incoming.clone();
    merged.manual = existing.manual || incoming.manual;
    if merged.field_at.is_none() {
        merged.field_at = existing.field_at.clone();
    }
    merged
}

pub fn write_pair_cache(layout: &ProjectLayout, record: &PairLinkCacheRecord) -> Result<()> {
    let to_write = if let Some(existing) = read_pair_cache(layout, &record.a, &record.b)? {
        merge_manual_fields(&existing, record)
    } else {
        record.clone()
    };
    write_pair_cache_direct(layout, &to_write)
}

fn write_pair_cache_direct(layout: &ProjectLayout, record: &PairLinkCacheRecord) -> Result<()> {
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

fn pair_cache_is_manual_stub(record: &PairLinkCacheRecord) -> bool {
    record.kind == LinkCacheKind::None
        && record.fingerprint.is_empty()
        && record.forward_margin_db.is_none()
        && record.reverse_margin_db.is_none()
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct CachedManualEntry {
    len: u64,
    modified: SystemTime,
    /// Set when the file has ``manual: true``.
    manual_pair: Option<(String, String)>,
}

/// Scan ``links/`` for pair files with ``manual: true`` (re-reads files whose mtime/size changed).
pub fn scan_manual_link_pairs(
    layout: &ProjectLayout,
    cache: &mut HashMap<PathBuf, CachedManualEntry>,
) -> Result<HashSet<(String, String)>> {
    let root = layout.links_dir();
    let mut out = HashSet::new();
    if !root.is_dir() {
        cache.retain(|p, _| p.starts_with(&root));
        return Ok(out);
    }

    let mut seen_paths = HashSet::new();
    for entry in fs::read_dir(&root).with_context(|| format!("read links dir: {}", root.display()))? {
        let entry = entry?;
        let dir_path = entry.path();
        if !dir_path.is_dir() {
            continue;
        }
        for file in fs::read_dir(&dir_path)
            .with_context(|| format!("read link cache subdir: {}", dir_path.display()))?
        {
            let file = file?;
            let file_path = file.path();
            if file_path.extension().and_then(|e| e.to_str()) != Some("json") {
                continue;
            }
            seen_paths.insert(file_path.clone());
            let meta = fs::metadata(&file_path)
                .with_context(|| format!("stat link file: {}", file_path.display()))?;
            let len = meta.len();
            let modified = meta.modified().unwrap_or(SystemTime::UNIX_EPOCH);
            if let Some(cached) = cache.get(&file_path) {
                if cached.len == len && cached.modified == modified {
                    if let Some(pair) = &cached.manual_pair {
                        out.insert(pair.clone());
                    }
                    continue;
                }
            }
            let text = fs::read_to_string(&file_path)
                .with_context(|| format!("read link file: {}", file_path.display()))?;
            let record: PairLinkCacheRecord = serde_json::from_str(&text)
                .with_context(|| format!("parse link file: {}", file_path.display()))?;
            let manual_pair = if record.manual {
                Some((record.a.clone(), record.b.clone()))
            } else {
                None
            };
            cache.insert(
                file_path,
                CachedManualEntry {
                    len,
                    modified,
                    manual_pair: manual_pair.clone(),
                },
            );
            if let Some(pair) = manual_pair {
                out.insert(pair);
            }
        }
    }
    cache.retain(|p, _| seen_paths.contains(p));
    Ok(out)
}

static MANUAL_SCAN_BY_PROJECT: LazyLock<Mutex<HashMap<String, HashMap<PathBuf, CachedManualEntry>>>> =
    LazyLock::new(|| Mutex::new(HashMap::new()));

/// Manual override pairs from ``links/`` (per-project scan cache keyed on file mtime/size).
fn utc_date_iso() -> String {
    let secs = SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs() as i64;
    unix_days_to_iso(secs / 86_400)
}

fn unix_days_to_iso(mut z: i64) -> String {
    z += 719_468;
    let era = (if z >= 0 { z } else { z - 146_096 }) / 146_097;
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = mp + if mp < 10 { 3 } else { -9 };
    let year = y + if m <= 2 { 1 } else { 0 };
    format!("{year:04}-{m:02}-{d:02}")
}

/// Mark a site pair as a manual RF override (creates or updates ``links/<a>/<b>.json``).
pub fn write_manual_link_override(
    layout: &ProjectLayout,
    slug_a: &str,
    slug_b: &str,
    field_at: Option<&str>,
) -> Result<()> {
    let (a, b) = canonical_link_pair(slug_a, slug_b);
    if let Some(existing) = read_pair_cache(layout, &a, &b)? {
        if existing.manual {
            return Ok(());
        }
        let mut updated = existing;
        updated.manual = true;
        if updated.field_at.is_none() {
            updated.field_at = field_at.map(str::to_string);
        }
        return write_pair_cache(layout, &updated);
    }
    let record = PairLinkCacheRecord {
        a,
        b,
        fingerprint: String::new(),
        links_model: LINKS_MODEL.to_string(),
        forward_margin_db: None,
        reverse_margin_db: None,
        kind: LinkCacheKind::None,
        distance_km: 0.0,
        at: utc_date_iso(),
        manual: true,
        field_at: field_at.map(str::to_string),
    };
    write_pair_cache(layout, &record)
}

/// Clear ``manual: true`` on a pair file. Stubs are deleted; RF rows keep margins with ``manual: false``.
pub fn clear_manual_link_override(
    layout: &ProjectLayout,
    slug_a: &str,
    slug_b: &str,
) -> Result<Option<PairLinkCacheRecord>> {
    let (a, b) = canonical_link_pair(slug_a, slug_b);
    let Some(mut existing) = read_pair_cache(layout, &a, &b)? else {
        return Ok(None);
    };
    if !existing.manual {
        anyhow::bail!("pair is not a manual link override");
    }
    existing.manual = false;
    if pair_cache_is_manual_stub(&existing) {
        let path = pair_cache_path(layout, &a, &b);
        if path.is_file() {
            fs::remove_file(&path)
                .with_context(|| format!("remove manual link stub: {}", path.display()))?;
        }
        return Ok(None);
    }
    write_pair_cache_direct(layout, &existing)?;
    Ok(Some(existing))
}

pub fn manual_link_pairs_for_project(layout: &ProjectLayout) -> Result<HashSet<(String, String)>> {
    let key = layout.project_dir.display().to_string();
    let mut projects = MANUAL_SCAN_BY_PROJECT
        .lock()
        .map_err(|_| anyhow::anyhow!("manual link scan cache lock poisoned"))?;
    let file_cache = projects.entry(key).or_default();
    scan_manual_link_pairs(layout, file_cache)
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
            manual: false,
            field_at: None,
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
            manual: false,
            field_at: None,
        };
        write_pair_cache(&layout, &record).unwrap();
        let mut slugs = HashSet::new();
        slugs.insert("stay".into());
        prune_link_cache(&layout, &slugs).unwrap();
        assert!(!layout.links_dir().join("gone").exists());
    }

    #[test]
    fn write_preserves_manual_on_rf_refresh() {
        let dir = tempfile::tempdir().unwrap();
        fs::write(dir.path().join("config.yaml"), "simulation: {}\n").unwrap();
        let layout = ProjectLayout::from_config_path(&dir.path().join("config.yaml")).unwrap();
        let manual = PairLinkCacheRecord {
            a: "alpha".into(),
            b: "beta".into(),
            fingerprint: String::new(),
            links_model: LINKS_MODEL.into(),
            forward_margin_db: None,
            reverse_margin_db: None,
            kind: LinkCacheKind::None,
            distance_km: 0.0,
            at: "2026-09-09".into(),
            manual: true,
            field_at: Some("2026-09-09".into()),
        };
        write_pair_cache(&layout, &manual).unwrap();
        let rf = PairLinkCacheRecord {
            fingerprint: "fp2".into(),
            forward_margin_db: Some(1.0),
            reverse_margin_db: Some(2.0),
            kind: LinkCacheKind::TwoWay,
            distance_km: 5.0,
            at: "2026-10-14".into(),
            manual: false,
            field_at: None,
            ..manual.clone()
        };
        write_pair_cache(&layout, &rf).unwrap();
        let read = read_pair_cache(&layout, "alpha", "beta").unwrap().unwrap();
        assert!(read.manual);
        assert_eq!(read.field_at.as_deref(), Some("2026-09-09"));
        assert_eq!(read.kind, LinkCacheKind::TwoWay);
    }

    #[test]
    fn prune_drops_manual_when_slug_removed() {
        let dir = tempfile::tempdir().unwrap();
        fs::write(dir.path().join("config.yaml"), "simulation: {}\n").unwrap();
        let layout = ProjectLayout::from_config_path(&dir.path().join("config.yaml")).unwrap();
        let record = PairLinkCacheRecord {
            a: "alpha".into(),
            b: "gone".into(),
            fingerprint: String::new(),
            links_model: LINKS_MODEL.into(),
            forward_margin_db: None,
            reverse_margin_db: None,
            kind: LinkCacheKind::None,
            distance_km: 0.0,
            at: "2026-10-09".into(),
            manual: true,
            field_at: None,
        };
        write_pair_cache(&layout, &record).unwrap();
        let mut slugs = HashSet::new();
        slugs.insert("alpha".into());
        prune_link_cache(&layout, &slugs).unwrap();
        assert!(read_pair_cache(&layout, "alpha", "gone").unwrap().is_none());
    }

    #[test]
    fn clear_manual_stays_false_after_rf_refresh() {
        let dir = tempfile::tempdir().unwrap();
        fs::write(dir.path().join("config.yaml"), "simulation: {}\n").unwrap();
        let layout = ProjectLayout::from_config_path(&dir.path().join("config.yaml")).unwrap();
        write_manual_link_override(&layout, "alpha", "beta", None).unwrap();
        clear_manual_link_override(&layout, "alpha", "beta").unwrap();
        assert!(read_pair_cache(&layout, "alpha", "beta").unwrap().is_none());

        write_pair_cache(
            &layout,
            &PairLinkCacheRecord {
                a: "alpha".into(),
                b: "beta".into(),
                fingerprint: "fp2".into(),
                links_model: LINKS_MODEL.into(),
                forward_margin_db: Some(1.0),
                reverse_margin_db: Some(2.0),
                kind: LinkCacheKind::TwoWay,
                distance_km: 5.0,
                at: "2026-10-14".into(),
                manual: true,
                field_at: Some("2026-09-09".into()),
            },
        )
        .unwrap();
        clear_manual_link_override(&layout, "alpha", "beta").unwrap();
        let rf = PairLinkCacheRecord {
            fingerprint: "fp3".into(),
            forward_margin_db: Some(3.0),
            reverse_margin_db: Some(4.0),
            kind: LinkCacheKind::TwoWay,
            distance_km: 6.0,
            at: "2026-10-15".into(),
            manual: false,
            field_at: None,
            ..read_pair_cache(&layout, "alpha", "beta").unwrap().unwrap()
        };
        write_pair_cache(&layout, &rf).unwrap();
        let read = read_pair_cache(&layout, "alpha", "beta").unwrap().unwrap();
        assert!(!read.manual);
        assert_eq!(read.kind, LinkCacheKind::TwoWay);
    }

    #[test]
    fn scan_manual_pairs_finds_flagged_files() {
        let dir = tempfile::tempdir().unwrap();
        fs::write(dir.path().join("config.yaml"), "simulation: {}\n").unwrap();
        let layout = ProjectLayout::from_config_path(&dir.path().join("config.yaml")).unwrap();
        write_pair_cache(
            &layout,
            &PairLinkCacheRecord {
                a: "x".into(),
                b: "y".into(),
                fingerprint: String::new(),
                links_model: LINKS_MODEL.into(),
                forward_margin_db: None,
                reverse_margin_db: None,
                kind: LinkCacheKind::None,
                distance_km: 0.0,
                at: "2026-10-09".into(),
                manual: true,
                field_at: None,
            },
        )
        .unwrap();
        let mut cache = HashMap::new();
        let pairs = scan_manual_link_pairs(&layout, &mut cache).unwrap();
        assert_eq!(pairs.len(), 1);
        assert!(pairs.contains(&("x".to_string(), "y".to_string())));
    }
}
