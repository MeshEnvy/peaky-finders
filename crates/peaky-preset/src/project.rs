//! Multi-file project layout: ``config.yaml`` plus optional ``sites.yaml`` / ``land.yaml``,
//! plus sharded ``peaks/`` and ``access/`` directories.

use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use serde_yaml::{Mapping, Value};

use crate::yaml_io::{read_preset_document, write_preset_value};

pub struct ProjectLayout {
    pub project_dir: PathBuf,
    pub config_path: PathBuf,
}

impl ProjectLayout {
    pub fn from_config_path(config_path: &Path) -> Result<Self> {
        let project_dir = config_path
            .parent()
            .context("config path has no parent directory")?
            .to_path_buf();
        Ok(Self {
            project_dir,
            config_path: config_path.to_path_buf(),
        })
    }

    pub fn split_sites_path(&self) -> PathBuf {
        self.project_dir.join("sites.yaml")
    }

    pub fn split_land_path(&self) -> PathBuf {
        self.project_dir.join("land.yaml")
    }

    pub fn boards_path(&self) -> PathBuf {
        self.project_dir.join("boards.yaml")
    }

    pub fn peaks_dir(&self) -> PathBuf {
        self.project_dir.join("peaks")
    }

    pub fn peaks_meta_path(&self) -> PathBuf {
        self.peaks_dir().join("_meta.yaml")
    }

    pub fn peak_entry_path(&self, slug: &str) -> PathBuf {
        self.peaks_dir().join(format!("{slug}.yaml"))
    }

    pub fn access_dir(&self) -> PathBuf {
        self.project_dir.join("access")
    }

    pub fn access_entry_path(&self, slug: &str) -> PathBuf {
        self.access_dir().join(format!("{slug}.yaml"))
    }

    pub fn access_meta_path(&self) -> PathBuf {
        self.access_dir().join("_meta.yaml")
    }

    pub fn uses_split_sites(&self) -> bool {
        self.split_sites_path().is_file()
    }

    pub fn uses_split_land(&self) -> bool {
        self.split_land_path().is_file()
    }

    pub fn uses_sharded_peaks(&self) -> bool {
        self.peaks_meta_path().is_file() || self.peaks_dir().is_dir()
    }

    pub fn sites_document_path(&self) -> PathBuf {
        if self.uses_split_sites() {
            self.split_sites_path()
        } else {
            self.config_path.clone()
        }
    }

    pub fn land_document_path(&self) -> PathBuf {
        if self.uses_split_land() {
            self.split_land_path()
        } else {
            self.config_path.clone()
        }
    }
}

fn yaml_key(s: &str) -> Value {
    Value::from(s)
}

/// Merge ``config.yaml`` with optional ``sites.yaml`` / ``land.yaml``.
pub fn read_merged_document(layout: &ProjectLayout) -> Result<Mapping> {
    let mut merged = read_preset_document(&layout.config_path)?;

    if layout.uses_split_sites() {
        merged.remove(&yaml_key("sites"));
        merged.remove(&yaml_key("links"));
        let sites_doc = read_preset_document(&layout.sites_document_path())?;
        if let Some(sites) = sites_doc.get(&yaml_key("sites")) {
            merged.insert(yaml_key("sites"), sites.clone());
        }
        if let Some(links) = sites_doc.get(&yaml_key("links")) {
            merged.insert(yaml_key("links"), links.clone());
        }
    }

    if layout.uses_split_land() {
        merged.remove(&yaml_key("land"));
        let land_doc = read_preset_document(&layout.land_document_path())?;
        if let Some(land) = land_doc.get(&yaml_key("land")) {
            merged.insert(yaml_key("land"), land.clone());
        }
    }

    Ok(merged)
}

/// Write a merged preset mapping back to the correct on-disk file(s).
pub fn write_merged_document(layout: &ProjectLayout, merged: &Mapping) -> Result<()> {
    let mut config_map = merged.clone();

    if layout.uses_split_sites() {
        let sites = config_map.remove(&yaml_key("sites"));
        let links = config_map.remove(&yaml_key("links"));
        if sites.is_some() || links.is_some() {
            let mut sites_doc = Mapping::new();
            if let Some(sites) = sites {
                sites_doc.insert(yaml_key("sites"), sites);
            }
            if let Some(links) = links {
                sites_doc.insert(yaml_key("links"), links);
            }
            write_preset_value(
                &layout.split_sites_path(),
                Value::Mapping(sites_doc),
            )?;
        }
    }

    if layout.uses_split_land() {
        if let Some(land) = config_map.remove(&yaml_key("land")) {
            let mut land_doc = Mapping::new();
            land_doc.insert(yaml_key("land"), land);
            write_preset_value(&layout.split_land_path(), Value::Mapping(land_doc))?;
        }
    }

    write_preset_value(&layout.config_path, Value::Mapping(config_map))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    use crate::yaml_io::write_yaml_document;

    #[test]
    fn split_layout_merges_sites_and_land() {
        let dir = tempfile::tempdir().unwrap();
        let config_path = dir.path().join("config.yaml");
        let mut config = Mapping::new();
        config.insert(yaml_key("simulation"), yaml_key("stub"));
        config.insert(yaml_key("links"), Value::Sequence(vec![]));
        write_yaml_document(&config_path, &config).unwrap();

        let mut sites_doc = Mapping::new();
        let mut sites = Mapping::new();
        sites.insert(yaml_key("hub"), yaml_key("entry"));
        sites_doc.insert(yaml_key("sites"), Value::Mapping(sites));
        write_yaml_document(&dir.path().join("sites.yaml"), &sites_doc).unwrap();

        let mut land_doc = Mapping::new();
        let mut land = Mapping::new();
        land.insert(yaml_key("refreshIntervalDays"), Value::from(90));
        land_doc.insert(yaml_key("land"), Value::Mapping(land));
        write_yaml_document(&dir.path().join("land.yaml"), &land_doc).unwrap();

        let layout = ProjectLayout::from_config_path(&config_path).unwrap();
        let merged = read_merged_document(&layout).unwrap();
        assert!(merged.contains_key(&yaml_key("sites")));
        assert!(merged.contains_key(&yaml_key("land")));
        assert!(merged.contains_key(&yaml_key("simulation")));
        assert!(!merged.contains_key(&yaml_key("mesh")));
    }

    #[test]
    fn split_layout_round_trip_write() {
        let dir = tempfile::tempdir().unwrap();
        let config_path = dir.path().join("config.yaml");
        write_yaml_document(&config_path, &Mapping::new()).unwrap();
        fs::write(dir.path().join("sites.yaml"), "sites: {}\n").unwrap();
        fs::write(dir.path().join("land.yaml"), "land: {}\n").unwrap();

        let layout = ProjectLayout::from_config_path(&config_path).unwrap();
        let mut merged = read_merged_document(&layout).unwrap();
        merged.insert(yaml_key("links"), Value::Sequence(vec![]));
        write_merged_document(&layout, &merged).unwrap();

        let config_text = fs::read_to_string(&config_path).unwrap();
        assert!(!config_text.contains("links:"));
        assert!(!config_text.contains("sites:"));
        let sites_text = fs::read_to_string(layout.split_sites_path()).unwrap();
        assert!(sites_text.contains("sites:"));
        assert!(sites_text.contains("links:"));
    }

    #[test]
    fn split_layout_reads_links_from_sites_yaml() {
        let dir = tempfile::tempdir().unwrap();
        let config_path = dir.path().join("config.yaml");
        let mut config = Mapping::new();
        config.insert(yaml_key("simulation"), yaml_key("stub"));
        config.insert(
            yaml_key("links"),
            Value::Sequence(vec![Value::Sequence(vec![
                yaml_key("a"),
                yaml_key("b"),
            ])]),
        );
        write_yaml_document(&config_path, &config).unwrap();

        let mut sites_doc = Mapping::new();
        sites_doc.insert(yaml_key("sites"), Value::Mapping(Mapping::new()));
        sites_doc.insert(
            yaml_key("links"),
            Value::Sequence(vec![Value::Sequence(vec![
                yaml_key("x"),
                yaml_key("y"),
            ])]),
        );
        write_yaml_document(&dir.path().join("sites.yaml"), &sites_doc).unwrap();

        let layout = ProjectLayout::from_config_path(&config_path).unwrap();
        let merged = read_merged_document(&layout).unwrap();
        let links = merged.get(&yaml_key("links")).unwrap();
        let pair = links.as_sequence().unwrap().first().unwrap().as_sequence().unwrap();
        assert_eq!(pair[0], yaml_key("x"));
        assert_eq!(pair[1], yaml_key("y"));
    }
}
