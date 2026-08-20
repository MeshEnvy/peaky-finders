//! Preset YAML read/write.

use std::collections::HashSet;
use std::fs;
use std::path::Path;

use anyhow::{Context, Result};
use serde_yaml::{Mapping, Value};

use crate::model::{
    validate_preset, validate_project_preset_document, Preset, PresetValidationError, SiteEntry,
};
use crate::sites::normalize_site_tags;

const PRESET_EXTENSIONS: [&str; 2] = [".yaml", ".yml"];

pub fn require_preset_yaml_path(path: &Path) -> Result<()> {
    let suffix = path
        .extension()
        .and_then(|s| s.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    if suffix == "json" {
        anyhow::bail!(
            "Peaky preset paths must end with `.yaml` or `.yml` (not `.json`); legacy JSON presets are unsupported: {}",
            path.display()
        );
    }
    if !PRESET_EXTENSIONS.iter().any(|ext| suffix == ext.trim_start_matches('.')) {
        anyhow::bail!(
            "preset path must end with `.yaml` or `.yml` (got suffix {suffix:?}): {}",
            path.display()
        );
    }
    Ok(())
}

pub fn read_preset_document(path: &Path) -> Result<Mapping> {
    let path = path.canonicalize().unwrap_or_else(|_| path.to_path_buf());
    require_preset_yaml_path(&path)?;
    let text = fs::read_to_string(&path)
        .with_context(|| format!("read preset YAML: {}", path.display()))?;
    let root: serde_yaml::Value = serde_yaml::from_str(&text)
        .with_context(|| format!("parse preset YAML: {}", path.display()))?;
    match root {
        serde_yaml::Value::Mapping(map) => Ok(map),
        _ => anyhow::bail!("preset YAML root must be a mapping at {}", path.display()),
    }
}

/// Load preset file as raw YAML for partial updates.
pub fn load_preset_raw(path: &Path) -> Result<serde_yaml::Value> {
    let map = read_preset_document(path)?;
    Ok(serde_yaml::Value::Mapping(map))
}

pub fn parse_preset_dict(raw: Mapping) -> Result<Preset, PresetValidationError> {
    validate_project_preset_document(&raw)?;
    let value = serde_yaml::Value::Mapping(raw);
    let preset: Preset = serde_yaml::from_value(value)
        .map_err(|e| PresetValidationError::Message(e.to_string()))?;
    validate_preset(&preset)?;
    Ok(preset)
}

pub fn load_preset(path: &Path) -> Result<Preset> {
    let raw = read_preset_document(path)?;
    parse_preset_dict(raw).map_err(|e| anyhow::anyhow!(e))
}

pub fn save_preset(path: &Path, preset: &Preset) -> Result<()> {
    validate_preset(preset).map_err(|e| anyhow::anyhow!(e))?;
    let value = serde_yaml::to_value(preset).context("serialize preset")?;
    write_preset_value(path, value)
}

pub fn write_preset_document(path: &Path, payload: &Mapping) -> Result<()> {
    write_preset_value(path, serde_yaml::Value::Mapping(payload.clone()))
}

fn write_preset_value(path: &Path, value: Value) -> Result<()> {
    require_preset_yaml_path(path)?;
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .with_context(|| format!("create preset parent dir: {}", parent.display()))?;
    }

    let text = serde_yaml::to_string(&value).context("encode preset YAML")?;
    let tmp = path.with_file_name(format!(
        "{}.partial",
        path.file_name()
            .and_then(|s| s.to_str())
            .unwrap_or("config.yaml")
    ));
    fs::write(&tmp, text).with_context(|| format!("write preset temp file: {}", tmp.display()))?;
    fs::rename(&tmp, path).with_context(|| format!("replace preset file: {}", path.display()))?;
    Ok(())
}

fn preset_root_mut(raw: &mut Value) -> Result<&mut Mapping> {
    raw.as_mapping_mut()
        .context("preset YAML root must be a mapping")
}

fn preset_sites_mut(map: &mut Mapping) -> Result<&mut Mapping> {
    let sites_val = map
        .get_mut(Value::from("sites"))
        .context("preset missing sites")?;
    sites_val
        .as_mapping_mut()
        .context("sites must be a mapping")
}

pub fn preset_site_slugs(map: &Mapping) -> HashSet<String> {
    map.get(&Value::from("sites"))
        .and_then(|v| v.as_mapping())
        .map(|sites| {
            sites
                .keys()
                .filter_map(|k| k.as_str().map(str::to_string))
                .collect()
        })
        .unwrap_or_default()
}

/// Append one site to the on-disk preset without rewriting unrelated sections.
pub fn insert_preset_site(path: &Path, slug: &str, entry: &SiteEntry) -> Result<()> {
    let mut raw = load_preset_raw(path)?;
    let map = preset_root_mut(&mut raw)?;
    let sites = preset_sites_mut(map)?;
    sites.insert(
        Value::from(slug),
        serde_yaml::to_value(entry).context("serialize site entry")?,
    );
    write_preset_document(path, map)
}

/// Patch fields on one site entry in place (preserves key order elsewhere in the file).
pub fn patch_preset_site(
    path: &Path,
    slug: &str,
    name: Option<&str>,
    loc: Option<[f64; 2]>,
    tags: Option<&[String]>,
    height_m: Option<Option<f64>>,
) -> Result<()> {
    let mut raw = load_preset_raw(path)?;
    let map = preset_root_mut(&mut raw)?;
    let sites = preset_sites_mut(map)?;
    let site_val = sites
        .get_mut(&Value::from(slug))
        .with_context(|| format!("site not found: {slug}"))?;
    let site_map = site_val
        .as_mapping_mut()
        .with_context(|| format!("sites.{slug} must be a mapping"))?;

    if let Some(name) = name {
        site_map.insert(Value::from("name"), Value::from(name));
    }
    if let Some([lat, lon]) = loc {
        site_map.insert(
            Value::from("loc"),
            Value::Sequence(vec![Value::from(lat), Value::from(lon)]),
        );
    }
    if let Some(tags) = tags {
        let normalized = normalize_site_tags(Some(&Value::Sequence(
            tags.iter().cloned().map(Value::from).collect(),
        )))
        .map_err(|e| anyhow::anyhow!(e))?;
        site_map.insert(
            Value::from("tags"),
            Value::Sequence(normalized.into_iter().map(Value::from).collect()),
        );
    }
    if let Some(height_m) = height_m {
        site_map.insert(
            Value::from("height_m"),
            height_m.map(Value::from).unwrap_or(Value::Null),
        );
    }

    write_preset_document(path, map)
}

pub fn patch_preset_sites_tags(
    path: &Path,
    slugs: &[String],
    add_tags: &[String],
    remove_tags: &[String],
) -> Result<()> {
    let mut raw = load_preset_raw(path)?;
    let map = preset_root_mut(&mut raw)?;
    let sites = preset_sites_mut(map)?;
    for slug in slugs {
        let Some(site_val) = sites.get_mut(&Value::from(slug.as_str())) else {
            continue;
        };
        let site_map = site_val
            .as_mapping_mut()
            .with_context(|| format!("sites.{slug} must be a mapping"))?;
        let current = site_map.get(&Value::from("tags"));
        let mut tags = normalize_site_tags(current).map_err(|e| anyhow::anyhow!(e))?;
        tags.retain(|t| !remove_tags.iter().any(|r| r == t));
        for t in add_tags {
            if !tags.iter().any(|x| x == t) {
                tags.push(t.clone());
            }
        }
        site_map.insert(
            Value::from("tags"),
            Value::Sequence(tags.into_iter().map(Value::from).collect()),
        );
    }
    write_preset_document(path, map)
}

pub fn patch_preset_site_tags(
    path: &Path,
    slug: &str,
    add_tags: &[String],
    remove_tags: &[String],
) -> Result<()> {
    let mut raw = load_preset_raw(path)?;
    let map = preset_root_mut(&mut raw)?;
    let sites = preset_sites_mut(map)?;
    let Some(site_val) = sites.get_mut(&Value::from(slug)) else {
        return Ok(());
    };
    let site_map = site_val
        .as_mapping_mut()
        .with_context(|| format!("sites.{slug} must be a mapping"))?;

    let current = site_map.get(&Value::from("tags"));
    let mut tags = normalize_site_tags(current)
        .map_err(|e| anyhow::anyhow!(e))?;
    tags.retain(|t| !remove_tags.iter().any(|r| r == t));
    for t in add_tags {
        if !tags.iter().any(|x| x == t) {
            tags.push(t.clone());
        }
    }
    site_map.insert(
        Value::from("tags"),
        Value::Sequence(tags.into_iter().map(Value::from).collect()),
    );
    write_preset_document(path, map)
}

pub fn remove_preset_site(path: &Path, slug: &str) -> Result<()> {
    let mut raw = load_preset_raw(path)?;
    let map = preset_root_mut(&mut raw)?;
    let sites = preset_sites_mut(map)?;
    sites
        .remove(&Value::from(slug))
        .with_context(|| format!("site not found: {slug}"))?;
    write_preset_document(path, map)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    use crate::model::{Preset, SiteEntry};

    #[test]
    fn write_preset_document_preserves_mapping_key_order() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("config.yaml");

        let mut sites = Mapping::new();
        sites.insert(Value::from("zebra"), Value::from("z"));
        sites.insert(Value::from("alpha"), Value::from("a"));

        let mut root = Mapping::new();
        root.insert(Value::from("simulation"), Value::Mapping(Mapping::new()));
        root.insert(Value::from("sites"), Value::Mapping(sites));

        write_preset_document(&path, &root).unwrap();
        let first = fs::read_to_string(&path).unwrap();
        write_preset_document(&path, &root).unwrap();
        let second = fs::read_to_string(&path).unwrap();
        assert_eq!(first, second);
        assert!(first.find("  zebra:").unwrap() < first.find("  alpha:").unwrap());
    }

    #[test]
    fn insert_preset_site_appends_without_reordering_existing() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("config.yaml");

        let mut sites = Mapping::new();
        sites.insert(Value::from("first"), site_yaml("First", 1.0, 2.0));
        let mut root = Mapping::new();
        root.insert(Value::from("sites"), Value::Mapping(sites));
        write_preset_document(&path, &root).unwrap();

        insert_preset_site(
            &path,
            "second",
            &SiteEntry {
                name: "Second".into(),
                loc: [3.0, 4.0],
                height_m: None,
                description: None,
                tags: vec![],
            },
        )
        .unwrap();

        let text = fs::read_to_string(&path).unwrap();
        assert!(text.find("  first:").unwrap() < text.find("  second:").unwrap());
    }

    #[test]
    fn patch_preset_site_only_changes_target_site() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("config.yaml");

        let mut sites = Mapping::new();
        sites.insert(Value::from("keep"), site_yaml("Keep", 1.0, 2.0));
        sites.insert(Value::from("edit"), site_yaml("Old", 3.0, 4.0));
        let mut root = Mapping::new();
        root.insert(Value::from("sites"), Value::Mapping(sites));
        write_preset_document(&path, &root).unwrap();
        let before = fs::read_to_string(&path).unwrap();

        patch_preset_site(&path, "edit", Some("New"), None, None, None).unwrap();
        let after = fs::read_to_string(&path).unwrap();

        assert_ne!(before, after);
        assert!(after.contains("name: New"));
        assert!(after.contains("name: Keep"));
    }

    fn site_yaml(name: &str, lat: f64, lon: f64) -> Value {
        let mut site = Mapping::new();
        site.insert(Value::from("name"), Value::from(name));
        site.insert(
            Value::from("loc"),
            Value::Sequence(vec![Value::from(lat), Value::from(lon)]),
        );
        site.insert(Value::from("height_m"), Value::Null);
        site.insert(Value::from("description"), Value::Null);
        site.insert(Value::from("tags"), Value::Sequence(vec![]));
        Value::Mapping(site)
    }

    #[test]
    fn save_preset_writes_stable_site_key_order() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("config.yaml");

        let mut sites = HashMap::new();
        sites.insert(
            "zebra".to_string(),
            SiteEntry {
                name: "Zebra".to_string(),
                loc: [1.0, 2.0],
                height_m: None,
                description: None,
                tags: vec![],
            },
        );
        sites.insert(
            "alpha".to_string(),
            SiteEntry {
                name: "Alpha".to_string(),
                loc: [3.0, 4.0],
                height_m: None,
                description: None,
                tags: vec![],
            },
        );

        let preset = Preset {
            sites,
            ..Preset::default()
        };

        save_preset(&path, &preset).unwrap();
        let first = fs::read_to_string(&path).unwrap();
        save_preset(&path, &preset).unwrap();
        let second = fs::read_to_string(&path).unwrap();
        assert_eq!(first, second);
    }
}
