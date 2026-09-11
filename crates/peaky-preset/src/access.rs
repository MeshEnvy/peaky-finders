//! Read/write ``access/<slug>.yaml`` place access profiles and ``access/_meta.yaml``.

use std::fs;
use std::path::Path;

use anyhow::{Context, Result};

use crate::model::{AccessMeta, PlaceAccess, ACCESS_ALGO_VERSION};
use crate::project::ProjectLayout;
use crate::yaml_io::{read_preset_document, write_preset_value};
use serde_yaml::Value;

pub fn access_dir(config_path: &Path) -> Result<std::path::PathBuf> {
    Ok(ProjectLayout::from_config_path(config_path)?.access_dir())
}

pub fn access_meta_path(config_path: &Path) -> Result<std::path::PathBuf> {
    Ok(ProjectLayout::from_config_path(config_path)?.access_meta_path())
}

fn write_access_meta(config_path: &Path, meta: &AccessMeta) -> Result<()> {
    let layout = ProjectLayout::from_config_path(config_path)?;
    fs::create_dir_all(layout.access_dir())
        .with_context(|| format!("create access dir: {}", layout.access_dir().display()))?;
    let value = serde_yaml::to_value(meta).context("serialize access meta")?;
    write_preset_value(&layout.access_meta_path(), value)
}

/// Load ``access/_meta.yaml``, or defaults if missing.
pub fn load_access_meta(config_path: &Path) -> Result<AccessMeta> {
    let path = access_meta_path(config_path)?;
    if !path.is_file() {
        return Ok(AccessMeta::default());
    }
    let doc = read_preset_document(&path)?;
    serde_yaml::from_value(Value::Mapping(doc)).context("parse access/_meta.yaml")
}

/// Ensure ``access/_meta.yaml`` exists; bump ``algo_version`` when code is newer.
pub fn ensure_access_meta(config_path: &Path) -> Result<AccessMeta> {
    let path = access_meta_path(config_path)?;
    let mut meta = if path.is_file() {
        load_access_meta(config_path)?
    } else {
        AccessMeta::default()
    };
    if meta.algo_version < ACCESS_ALGO_VERSION {
        meta.algo_version = ACCESS_ALGO_VERSION;
    }
    write_access_meta(config_path, &meta)?;
    Ok(meta)
}

pub fn access_path(config_path: &Path, slug: &str) -> Result<std::path::PathBuf> {
    Ok(ProjectLayout::from_config_path(config_path)?.access_entry_path(slug))
}

pub fn load_access(config_path: &Path, slug: &str) -> Result<Option<PlaceAccess>> {
    let path = access_path(config_path, slug)?;
    if !path.is_file() {
        return Ok(None);
    }
    let doc = read_preset_document(&path)?;
    let access: PlaceAccess =
        serde_yaml::from_value(Value::Mapping(doc)).context("parse access yaml")?;
    Ok(Some(access))
}

pub fn upsert_access(config_path: &Path, slug: &str, access: &PlaceAccess) -> Result<()> {
    let layout = ProjectLayout::from_config_path(config_path)?;
    fs::create_dir_all(layout.access_dir())
        .with_context(|| format!("create access dir: {}", layout.access_dir().display()))?;
    let path = layout.access_entry_path(slug);
    let value = serde_yaml::to_value(access).context("serialize access")?;
    write_preset_value(&path, value)
}

pub fn delete_access(config_path: &Path, slug: &str) -> Result<()> {
    let path = access_path(config_path, slug)?;
    if path.is_file() {
        fs::remove_file(&path).with_context(|| format!("delete access: {}", path.display()))?;
    }
    Ok(())
}

pub fn list_access_slugs(config_path: &Path) -> Result<Vec<String>> {
    let dir = access_dir(config_path)?;
    if !dir.is_dir() {
        return Ok(Vec::new());
    }
    let mut slugs = Vec::new();
    for entry in fs::read_dir(&dir).with_context(|| format!("read access dir: {}", dir.display()))?
    {
        let entry = entry?;
        let path = entry.path();
        if !path.is_file() {
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::{PeakHikeProfile, ACCESS_ALGO_VERSION};

    #[test]
    fn upsert_and_load_access_roundtrip() {
        let dir = tempfile::tempdir().unwrap();
        let config = dir.path().join("config.yaml");
        std::fs::write(&config, "simulation:\n  radius_km: 50\n").unwrap();
        let access = PlaceAccess {
            compute_key: None,
            paved_loc: Some([38.0, -117.1]),
            road_loc: Some([38.01, -117.11]),
            jeep_m: Some(1200.0),
            jeep: None,
            hike_m: Some(400.0),
            hike: Some(PeakHikeProfile {
                hike_m_3d: 400.0,
                horiz_m: 380.0,
                gain_m: 40.0,
                loss_m: 0.0,
                max_slope_deg: 10.0,
                max_grade_pct: 18.0,
                avg_grade_pct: 8.0,
                difficulty: "medium".into(),
                profile: vec![],
                histogram: vec![],
            }),
            max_slope_deg: Some(10.0),
        };
        upsert_access(&config, "spencer-peak", &access).unwrap();
        let loaded = load_access(&config, "spencer-peak").unwrap().unwrap();
        assert_eq!(loaded.jeep_m, Some(1200.0));
        assert_eq!(loaded.hike.as_ref().unwrap().difficulty, "medium");
        assert_eq!(list_access_slugs(&config).unwrap(), vec!["spencer-peak"]);
    }

    #[test]
    fn ensure_access_meta_writes_defaults() {
        let dir = tempfile::tempdir().unwrap();
        let config = dir.path().join("config.yaml");
        std::fs::write(&config, "simulation:\n  radius_km: 50\n").unwrap();
        let meta = ensure_access_meta(&config).unwrap();
        assert_eq!(meta.algo_version, ACCESS_ALGO_VERSION);
        assert!(access_meta_path(&config).unwrap().is_file());
        let loaded = load_access_meta(&config).unwrap();
        assert_eq!(loaded.max_jeep_m, meta.max_jeep_m);
    }
}
