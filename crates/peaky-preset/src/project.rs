//! Multi-file project layout: ``config.yaml`` plus optional ``sites.yaml`` / ``land.yaml``,
//! optional ``links.json`` (or legacy ``links.yaml``), plus git-tracked ``peaks.yaml``
//! overrides and cache under ``.peaky/cache/``.

use std::fs;
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

    pub fn split_links_yaml_path(&self) -> PathBuf {
        self.project_dir.join("links.yaml")
    }

    pub fn split_links_json_path(&self) -> PathBuf {
        self.project_dir.join("links.json")
    }

    /// Legacy alias for ``links.yaml`` path.
    pub fn split_links_path(&self) -> PathBuf {
        self.split_links_yaml_path()
    }

    pub fn boards_path(&self) -> PathBuf {
        self.project_dir.join("boards.yaml")
    }

    pub fn roads_path(&self) -> PathBuf {
        self.project_dir.join("roads.yaml")
    }

    pub fn peak_overrides_path(&self) -> PathBuf {
        self.project_dir.join("peaks.yaml")
    }

    pub fn probe_cache_dir(&self, prefs_key: &str) -> PathBuf {
        self.project_dir
            .join(".peaky/cache/probe")
            .join(prefs_key)
    }

    pub fn peaks_index_path(&self, prefs_key: &str) -> PathBuf {
        self.project_dir
            .join(".peaky/cache/peaks")
            .join(prefs_key)
            .join("index.json")
    }

    pub fn uses_split_sites(&self) -> bool {
        self.split_sites_path().is_file()
    }

    pub fn uses_split_land(&self) -> bool {
        self.split_land_path().is_file()
    }

    pub fn uses_split_links(&self) -> bool {
        self.split_links_json_path().is_file() || self.split_links_yaml_path().is_file()
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

fn yaml_value_to_json(value: &Value) -> Result<serde_json::Value> {
    let text = serde_yaml::to_string(value).context("encode links for JSON")?;
    serde_json::from_str(&text).context("parse links as JSON")
}

fn json_value_to_yaml(value: serde_json::Value) -> Result<Value> {
    let text = serde_json::to_string(&value).context("encode links JSON")?;
    serde_yaml::from_str(&text).context("parse links JSON into YAML value")
}

fn read_split_links_value(layout: &ProjectLayout) -> Result<Option<Value>> {
    if layout.split_links_json_path().is_file() {
        let path = layout.split_links_json_path();
        let text = fs::read_to_string(&path)
            .with_context(|| format!("read links JSON: {}", path.display()))?;
        let root: serde_json::Value =
            serde_json::from_str(&text).with_context(|| format!("parse links JSON: {}", path.display()))?;
        let Some(links) = root.get("links") else {
            return Ok(None);
        };
        return json_value_to_yaml(links.clone()).map(Some);
    }
    if layout.split_links_yaml_path().is_file() {
        let links_doc = read_preset_document(&layout.split_links_yaml_path())?;
        return Ok(links_doc.get(&yaml_key("links")).cloned());
    }
    Ok(None)
}

fn write_split_links_value(layout: &ProjectLayout, links: Value) -> Result<()> {
    let use_json =
        layout.split_links_json_path().is_file() || !layout.split_links_yaml_path().is_file();
    if use_json {
        let path = layout.split_links_json_path();
        let json_links = yaml_value_to_json(&links)?;
        let mut doc = serde_json::Map::new();
        doc.insert("links".into(), json_links);
        let text = serde_json::to_string_pretty(&serde_json::Value::Object(doc))
            .context("serialize links JSON")?;
        let tmp = path.with_extension("json.partial");
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)
                .with_context(|| format!("create links parent: {}", parent.display()))?;
        }
        fs::write(&tmp, format!("{text}\n"))
            .with_context(|| format!("write links temp: {}", tmp.display()))?;
        fs::rename(&tmp, &path).with_context(|| format!("replace links JSON: {}", path.display()))?;
        return Ok(());
    }
    let mut links_doc = Mapping::new();
    links_doc.insert(yaml_key("links"), links);
    write_preset_value(
        &layout.split_links_yaml_path(),
        Value::Mapping(links_doc),
    )
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
        if layout.uses_split_links() {
            if let Some(links) = read_split_links_value(layout)? {
                merged.insert(yaml_key("links"), links);
            }
        } else if let Some(links) = sites_doc.get(&yaml_key("links")) {
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
        if sites.is_some() {
            let mut sites_doc = Mapping::new();
            if let Some(sites) = sites {
                sites_doc.insert(yaml_key("sites"), sites);
            }
            write_preset_value(
                &layout.split_sites_path(),
                Value::Mapping(sites_doc),
            )?;
        }
        if let Some(links) = links {
            write_split_links_value(layout, links)?;
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
        assert!(!sites_text.contains("links:"));
        let links_path = layout.split_links_json_path();
        assert!(links_path.is_file());
        let links_text = fs::read_to_string(&links_path).unwrap();
        assert!(links_text.contains("\"links\""));
    }

    #[test]
    fn split_layout_reads_links_from_links_json() {
        let dir = tempfile::tempdir().unwrap();
        let config_path = dir.path().join("config.yaml");
        write_yaml_document(&config_path, &Mapping::new()).unwrap();
        fs::write(dir.path().join("sites.yaml"), "sites: {}\n").unwrap();
        fs::write(
            dir.path().join("links.json"),
            r#"{"links": [["x", "y"]]}"#,
        )
        .unwrap();

        let layout = ProjectLayout::from_config_path(&config_path).unwrap();
        let merged = read_merged_document(&layout).unwrap();
        let links = merged.get(&yaml_key("links")).unwrap();
        let pair = links.as_sequence().unwrap().first().unwrap().as_sequence().unwrap();
        assert_eq!(pair[0], yaml_key("x"));
        assert_eq!(pair[1], yaml_key("y"));
    }

    #[test]
    fn split_layout_reads_links_from_links_yaml() {
        let dir = tempfile::tempdir().unwrap();
        let config_path = dir.path().join("config.yaml");
        write_yaml_document(&config_path, &Mapping::new()).unwrap();
        fs::write(dir.path().join("sites.yaml"), "sites: {}\n").unwrap();
        fs::write(
            dir.path().join("links.yaml"),
            "links:\n  - [x, y]\n",
        )
        .unwrap();

        let layout = ProjectLayout::from_config_path(&config_path).unwrap();
        let merged = read_merged_document(&layout).unwrap();
        let links = merged.get(&yaml_key("links")).unwrap();
        let pair = links.as_sequence().unwrap().first().unwrap().as_sequence().unwrap();
        assert_eq!(pair[0], yaml_key("x"));
        assert_eq!(pair[1], yaml_key("y"));
    }

    #[test]
    fn split_layout_reads_links_from_sites_yaml_when_no_links_file() {
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
