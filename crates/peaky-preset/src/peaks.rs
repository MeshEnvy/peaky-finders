//! Read/write sharded eligible-peaks catalog (`peaks/<slug>.yaml` + `peaks/_meta.yaml`).

use std::collections::{HashMap, HashSet};
use std::fs;
use std::path::Path;

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use serde_yaml::Value;

use crate::access::{delete_access, load_access, load_access_meta, upsert_access};
use crate::model::{PeakAccessRules, PeakCatalogEntry, PeaksCatalog};
use crate::project::ProjectLayout;
use crate::yaml_io::{read_preset_document, write_preset_value};

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
struct PeaksMeta {
    #[serde(default)]
    pub generated_at: String,
    #[serde(default)]
    pub rules: PeakAccessRules,
}

fn yaml_key(s: &str) -> Value {
    Value::from(s)
}

pub fn peaks_dir(config_path: &Path) -> Result<std::path::PathBuf> {
    Ok(ProjectLayout::from_config_path(config_path)?.peaks_dir())
}

/// Directory path used by CLI summary output (sharded peaks root).
pub fn peaks_catalog_path(config_path: &Path) -> Result<std::path::PathBuf> {
    peaks_dir(config_path)
}

pub fn list_peak_slugs(config_path: &Path) -> Result<Vec<String>> {
    let layout = ProjectLayout::from_config_path(config_path)?;
    let dir = layout.peaks_dir();
    if !dir.is_dir() {
        return Ok(Vec::new());
    }
    let mut slugs = Vec::new();
    for entry in fs::read_dir(&dir).with_context(|| format!("read peaks dir: {}", dir.display()))?
    {
        let entry = entry?;
        let path = entry.path();
        if !path.is_file() {
            continue;
        }
        let Some(name) = path.file_name().and_then(|s| s.to_str()) else {
            continue;
        };
        if name == "_meta.yaml" || name == "_meta.yml" || name.starts_with('.') {
            continue;
        }
        let Some(stem) = path.file_stem().and_then(|s| s.to_str()) else {
            continue;
        };
        if stem.starts_with('_') {
            continue;
        }
        let ext = path
            .extension()
            .and_then(|s| s.to_str())
            .unwrap_or("")
            .to_ascii_lowercase();
        if ext != "yaml" && ext != "yml" {
            continue;
        }
        slugs.push(stem.to_string());
    }
    slugs.sort();
    Ok(slugs)
}

pub fn load_peak(config_path: &Path, slug: &str) -> Result<Option<PeakCatalogEntry>> {
    let path = ProjectLayout::from_config_path(config_path)?.peak_entry_path(slug);
    if !path.is_file() {
        return Ok(None);
    }
    let doc = read_preset_document(&path)?;
    let entry: PeakCatalogEntry =
        serde_yaml::from_value(Value::Mapping(doc)).context("parse peak yaml")?;
    // Peak files are thin; join access/ so in-memory catalog keeps hike/jeep paths.
    let entry = entry.thin();
    if let Some(access) = load_access(config_path, slug)? {
        return Ok(Some(entry.with_access(&access)));
    }
    Ok(Some(entry))
}

pub fn upsert_peak(config_path: &Path, slug: &str, entry: &PeakCatalogEntry) -> Result<()> {
    let layout = ProjectLayout::from_config_path(config_path)?;
    fs::create_dir_all(layout.peaks_dir())
        .with_context(|| format!("create peaks dir: {}", layout.peaks_dir().display()))?;
    let path = layout.peak_entry_path(slug);
    let value = serde_yaml::to_value(entry.thin()).context("serialize peak")?;
    write_preset_value(&path, value)
}

pub fn delete_peak(config_path: &Path, slug: &str) -> Result<()> {
    let path = ProjectLayout::from_config_path(config_path)?.peak_entry_path(slug);
    if path.is_file() {
        fs::remove_file(&path).with_context(|| format!("delete peak: {}", path.display()))?;
    }
    Ok(())
}

fn write_peaks_meta(config_path: &Path, meta: &PeaksMeta) -> Result<()> {
    let layout = ProjectLayout::from_config_path(config_path)?;
    fs::create_dir_all(layout.peaks_dir())
        .with_context(|| format!("create peaks dir: {}", layout.peaks_dir().display()))?;
    let value = serde_yaml::to_value(meta).context("serialize peaks meta")?;
    write_preset_value(&layout.peaks_meta_path(), value)
}

fn load_peaks_meta(config_path: &Path) -> Result<PeaksMeta> {
    let path = ProjectLayout::from_config_path(config_path)?.peaks_meta_path();
    if !path.is_file() {
        return Ok(PeaksMeta::default());
    }
    let doc = read_preset_document(&path)?;
    serde_yaml::from_value(Value::Mapping(doc)).context("parse peaks/_meta.yaml")
}

/// Greenfield: ignore and delete leftover monolithic ``peaks.yaml`` (never migrate).
fn scrap_monolith_peaks_yaml(config_path: &Path) -> Result<()> {
    let path = ProjectLayout::from_config_path(config_path)?.project_dir.join("peaks.yaml");
    if path.is_file() {
        fs::remove_file(&path)
            .with_context(|| format!("remove obsolete peaks.yaml: {}", path.display()))?;
    }
    Ok(())
}

pub fn load_peaks_catalog(config_path: &Path) -> Result<PeaksCatalog> {
    scrap_monolith_peaks_yaml(config_path)?;
    let layout = ProjectLayout::from_config_path(config_path)?;
    if !layout.uses_sharded_peaks() {
        return Ok(PeaksCatalog::default());
    }
    let meta = load_peaks_meta(config_path)?;
    let mut entries = HashMap::new();
    for slug in list_peak_slugs(config_path)? {
        if let Some(entry) = load_peak(config_path, &slug)? {
            entries.insert(slug, entry);
        }
    }
    Ok(PeaksCatalog {
        generated_at: meta.generated_at,
        rules: meta.rules,
        entries,
    })
}

/// Upsert one peak row + its access file (profiles extracted from the entry).
///
/// Never clobber existing `hike`/`jeep` path blobs with a scalar-only write
/// (e.g. after a thin catalog reload).
pub fn upsert_peak_with_access(
    config_path: &Path,
    slug: &str,
    entry: &PeakCatalogEntry,
) -> Result<()> {
    let brought_profiles = entry.hike.is_some() || entry.jeep.is_some();
    let mut access = entry.to_place_access();
    if let Some(existing) = load_access(config_path, slug)? {
        if access.hike.is_none() {
            access.hike = existing.hike;
        }
        if access.jeep.is_none() {
            access.jeep = existing.jeep;
        }
        if access.paved_loc.is_none() {
            access.paved_loc = existing.paved_loc;
        }
        if access.road_loc.is_none() {
            access.road_loc = existing.road_loc;
        }
        if access.jeep_m.is_none() {
            access.jeep_m = existing.jeep_m;
        }
        if access.hike_m.is_none() {
            access.hike_m = existing.hike_m;
        }
        if access.max_slope_deg.is_none() {
            access.max_slope_deg = existing.max_slope_deg;
        }
        if !brought_profiles && access.compute_key.is_none() {
            access.compute_key = existing.compute_key;
        }
    }
    if brought_profiles {
        let access_meta = load_access_meta(config_path).unwrap_or_default();
        let peak_meta = load_peaks_meta(config_path).unwrap_or_default();
        access.compute_key = Some(crate::compute_key::peak_access_compute_key(
            &access_meta,
            peak_meta.rules.max_hike_m,
        ));
    } else if access.compute_key.is_none()
        && (access.hike.is_some() || access.jeep.is_some())
    {
        let access_meta = load_access_meta(config_path).unwrap_or_default();
        let peak_meta = load_peaks_meta(config_path).unwrap_or_default();
        access.compute_key = Some(crate::compute_key::peak_access_compute_key(
            &access_meta,
            peak_meta.rules.max_hike_m,
        ));
    }
    upsert_peak(config_path, slug, entry)?;
    if access.jeep.is_some()
        || access.hike.is_some()
        || access.paved_loc.is_some()
        || access.road_loc.is_some()
    {
        upsert_access(config_path, slug, &access)?;
    }
    Ok(())
}

pub fn write_peaks_catalog(config_path: &Path, catalog: &PeaksCatalog) -> Result<()> {
    write_peaks_meta(
        config_path,
        &PeaksMeta {
            generated_at: catalog.generated_at.clone(),
            rules: catalog.rules.clone(),
        },
    )?;
    let existing: HashSet<String> = list_peak_slugs(config_path)?.into_iter().collect();
    let keep: HashSet<String> = catalog.entries.keys().cloned().collect();
    for slug in existing.difference(&keep) {
        delete_peak(config_path, slug)?;
        // Only delete access when removing a peak row (site-owned access kept).
        delete_access(config_path, slug)?;
    }
    for (slug, entry) in &catalog.entries {
        upsert_peak_with_access(config_path, slug, entry)?;
    }
    scrap_monolith_peaks_yaml(config_path)?;
    Ok(())
}

/// Wipe non-deny peaks (and their access). Keep deny peak files + `_meta` rewrite by caller.
pub fn clean_peaks_catalog(config_path: &Path, keep_denied: &PeaksCatalog) -> Result<()> {
    let existing = list_peak_slugs(config_path)?;
    for slug in existing {
        let deny = keep_denied
            .entries
            .get(&slug)
            .and_then(|e| e.deny)
            .unwrap_or(false);
        if deny {
            continue;
        }
        delete_peak(config_path, &slug)?;
        delete_access(config_path, &slug)?;
    }
    Ok(())
}

/// Merge ``deny: true`` rows from an existing catalog into a freshly built one.
pub fn preserve_denied_entries(
    new_catalog: &mut PeaksCatalog,
    previous: &PeaksCatalog,
) -> usize {
    let mut kept = 0usize;
    for (slug, entry) in &previous.entries {
        if !entry.deny.unwrap_or(false) {
            continue;
        }
        new_catalog.entries.insert(slug.clone(), entry.clone());
        kept += 1;
    }
    kept
}

pub fn denied_slugs(catalog: &PeaksCatalog) -> HashMap<String, PeakCatalogEntry> {
    catalog
        .entries
        .iter()
        .filter(|(_, e)| e.deny.unwrap_or(false))
        .map(|(k, v)| (k.clone(), v.clone()))
        .collect()
}

/// All place slugs that must stay unique: sites ∪ peaks.
pub fn place_slugs(config_path: &Path) -> Result<HashSet<String>> {
    let mut out = HashSet::new();
    let layout = ProjectLayout::from_config_path(config_path)?;
    if let Ok(merged) = crate::project::read_merged_document(&layout) {
        if let Some(Value::Mapping(sites)) = merged.get(&yaml_key("sites")) {
            for key in sites.keys() {
                if let Some(s) = key.as_str() {
                    out.insert(s.to_string());
                }
            }
        }
    }
    out.extend(list_peak_slugs(config_path)?);
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::PeakAccessRules;

    #[test]
    fn write_and_load_peaks_catalog_roundtrip_sharded() {
        let dir = tempfile::tempdir().unwrap();
        let config = dir.path().join("config.yaml");
        std::fs::write(&config, "simulation:\n  radius_km: 50\n").unwrap();
        let catalog = PeaksCatalog {
            generated_at: "2026-09-10".into(),
            rules: PeakAccessRules::default(),
            entries: HashMap::from([(
                "peak-test".into(),
                PeakCatalogEntry {
                    name: Some("Test".into()),
                    loc: [38.0, -117.0],
                    elev_m: Some(2100.0),
                    source: "gnis".into(),
                    compute_key: None,
                    road_m: Some(120.0),
                    road_loc: Some([38.0, -117.01]),
                    hike_m: Some(80.0),
                    max_slope_deg: Some(12.0),
                    hike: None,
                    paved_loc: Some([37.99, -117.02]),
                    jeep_m: Some(1500.0),
                    jeep: None,
                    deny: None,
                },
            )]),
        };
        write_peaks_catalog(&config, &catalog).unwrap();
        assert!(dir.path().join("peaks/_meta.yaml").is_file());
        assert!(dir.path().join("peaks/peak-test.yaml").is_file());
        let loaded = load_peaks_catalog(&config).unwrap();
        assert_eq!(loaded.generated_at, "2026-09-10");
        assert_eq!(loaded.entries.len(), 1);
        assert_eq!(loaded.entries["peak-test"].source, "gnis");
        assert!(loaded.entries["peak-test"].hike.is_none());
        assert!(loaded.entries["peak-test"].jeep.is_none());
    }

    #[test]
    fn scrap_monolith_peaks_yaml_deletes_without_migrate() {
        let dir = tempfile::tempdir().unwrap();
        let config = dir.path().join("config.yaml");
        std::fs::write(&config, "simulation:\n  radius_km: 50\n").unwrap();
        let legacy = dir.path().join("peaks.yaml");
        std::fs::write(
            &legacy,
            r#"
peaks:
  entries:
    dem-1:
      loc: [38.0, -117.0]
      source: dem
"#,
        )
        .unwrap();
        let loaded = load_peaks_catalog(&config).unwrap();
        assert!(!legacy.is_file());
        assert!(loaded.entries.is_empty());
    }

    #[test]
    fn catalog_rewrite_preserves_access_profiles() {
        use crate::model::{PeakHikeProfile, PeakHikeProfilePoint};

        let dir = tempfile::tempdir().unwrap();
        let config = dir.path().join("config.yaml");
        std::fs::write(&config, "simulation:\n  radius_km: 50\n").unwrap();
        let entry = PeakCatalogEntry {
            name: Some("Test".into()),
            loc: [38.0, -117.0],
            elev_m: Some(2100.0),
            source: "dem".into(),
            compute_key: None,
            road_m: Some(120.0),
            road_loc: Some([38.01, -117.0]),
            hike_m: Some(80.0),
            max_slope_deg: Some(12.0),
            hike: Some(PeakHikeProfile {
                hike_m_3d: 80.0,
                horiz_m: 75.0,
                gain_m: 10.0,
                loss_m: 0.0,
                max_slope_deg: 12.0,
                max_grade_pct: 20.0,
                avg_grade_pct: 5.0,
                difficulty: "easy".into(),
                profile: vec![PeakHikeProfilePoint {
                    dist_m: 0.0,
                    elev_m: 2000.0,
                    lat: 38.01,
                    lon: -117.0,
                }],
                histogram: vec![],
            }),
            paved_loc: Some([37.99, -117.02]),
            jeep_m: Some(1500.0),
            jeep: None,
            deny: None,
        };
        upsert_peak_with_access(&config, "peak-test", &entry).unwrap();
        let access = load_access(&config, "peak-test").unwrap().unwrap();
        assert!(access.hike.is_some());
        assert_eq!(access.hike.as_ref().unwrap().profile.len(), 1);

        // Simulate peaks-build start: load thin+join, then rewrite whole catalog.
        let loaded = load_peaks_catalog(&config).unwrap();
        assert!(loaded.entries["peak-test"].hike.is_some());
        write_peaks_catalog(&config, &loaded).unwrap();
        let access2 = load_access(&config, "peak-test").unwrap().unwrap();
        assert_eq!(
            access2.hike.as_ref().map(|h| h.profile.len()),
            Some(1),
            "rewrite must not strip hike path"
        );
        // Scalar-only upsert must not wipe profiles either.
        let thin = entry.thin();
        upsert_peak_with_access(&config, "peak-test", &thin).unwrap();
        let access3 = load_access(&config, "peak-test").unwrap().unwrap();
        assert!(access3.hike.is_some());
    }

    #[test]
    fn preserve_denied_entries_keeps_operator_overrides() {
        let mut prev = PeaksCatalog::default();
        prev.entries.insert(
            "skip-ridge".into(),
            PeakCatalogEntry {
                name: Some("Skip".into()),
                loc: [38.0, -117.0],
                elev_m: Some(2000.0),
                source: "dem".into(),
                    compute_key: None,
                road_m: None,
                road_loc: None,
                hike_m: None,
                max_slope_deg: None,
                hike: None,
                paved_loc: None,
                jeep_m: None,
                jeep: None,
                deny: Some(true),
            },
        );
        let mut next = PeaksCatalog {
            generated_at: "2026-09-10".into(),
            rules: PeakAccessRules::default(),
            entries: HashMap::new(),
        };
        next.entries.insert(
            "good-peak".into(),
            PeakCatalogEntry {
                name: Some("Good".into()),
                loc: [38.1, -117.1],
                elev_m: Some(2100.0),
                source: "gnis".into(),
                    compute_key: None,
                road_m: Some(200.0),
                road_loc: Some([38.1, -117.11]),
                hike_m: Some(150.0),
                max_slope_deg: Some(12.0),
                hike: None,
                paved_loc: None,
                jeep_m: None,
                jeep: None,
                deny: None,
            },
        );
        let n = preserve_denied_entries(&mut next, &prev);
        assert_eq!(n, 1);
        assert!(next.entries.contains_key("skip-ridge"));
        assert!(next.entries.contains_key("good-peak"));
        assert_eq!(next.entries["skip-ridge"].deny, Some(true));
    }
}
