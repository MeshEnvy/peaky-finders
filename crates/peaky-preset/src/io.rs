//! Preset YAML read/write (monolithic or split project files).

use std::collections::HashSet;
use std::path::Path;

use anyhow::{Context, Result};
use serde_yaml::{Mapping, Value};

use crate::model::{
    validate_preset, validate_project_preset_document, LandLayerEntry, LandSidebar,
    LandSourceEntry, LandSourceRefresh, Preset, PresetValidationError, SiteEntry,
};
use crate::project::{read_merged_document, write_merged_document, ProjectLayout};
use crate::sites::normalize_site_tags;
use crate::yaml_io::{read_preset_document, write_preset_value};

fn layout_for(config_path: &Path) -> Result<ProjectLayout> {
    ProjectLayout::from_config_path(config_path)
}

/// Load preset file as raw YAML for partial updates (merged across split files).
pub fn load_preset_raw(path: &Path) -> Result<Value> {
    let layout = layout_for(path)?;
    Ok(Value::Mapping(read_merged_document(&layout)?))
}

pub fn parse_preset_dict(raw: Mapping) -> Result<Preset, PresetValidationError> {
    validate_project_preset_document(&raw)?;
    let value = Value::Mapping(raw);
    let mut preset: Preset = serde_yaml::from_value(value)
        .map_err(|e| PresetValidationError::Message(e.to_string()))?;
    validate_preset(&preset)?;
    Ok(preset)
}

pub fn load_preset(path: &Path) -> Result<Preset> {
    let layout = layout_for(path)?;
    let raw = read_merged_document(&layout)?;
    parse_preset_dict(raw).map_err(|e| anyhow::anyhow!(e))
}

pub fn save_preset(path: &Path, preset: &Preset) -> Result<()> {
    validate_preset(preset).map_err(|e| anyhow::anyhow!(e))?;
    let layout = layout_for(path)?;
    let value = serde_yaml::to_value(preset).context("serialize preset")?;
    let merged = value
        .as_mapping()
        .context("preset must serialize to a mapping")?;
    write_merged_document(&layout, merged)
}

pub fn write_preset_document(path: &Path, payload: &Mapping) -> Result<()> {
    let layout = layout_for(path)?;
    write_merged_document(&layout, payload)
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

fn sites_map_mut(doc: &mut Mapping) -> Result<&mut Mapping> {
    let sites_val = doc
        .entry(Value::from("sites"))
        .or_insert_with(|| Value::Mapping(Mapping::new()));
    sites_val
        .as_mapping_mut()
        .context("sites must be a mapping")
}

fn read_sites_document_root(config_path: &Path) -> Result<(ProjectLayout, Mapping)> {
    let layout = layout_for(config_path)?;
    let mut doc = read_preset_document(&layout.sites_document_path())?;
    if !doc.contains_key(Value::from("sites")) {
        doc.insert(Value::from("sites"), Value::Mapping(Mapping::new()));
    }
    Ok((layout, doc))
}

fn write_sites_document(layout: &ProjectLayout, doc: &Mapping) -> Result<()> {
    write_preset_value(
        &layout.sites_document_path(),
        Value::Mapping(doc.clone()),
    )
}

/// Append one site to the on-disk preset without rewriting unrelated sections.
pub fn insert_preset_site(path: &Path, slug: &str, entry: &SiteEntry) -> Result<()> {
    let (layout, mut doc) = read_sites_document_root(path)?;
    let sites = sites_map_mut(&mut doc)?;
    sites.insert(
        Value::from(slug),
        serde_yaml::to_value(entry).context("serialize site entry")?,
    );
    write_sites_document(&layout, &doc)
}

/// Patch fields on one site entry in place (preserves key order elsewhere in the file).
/// Load one site from ``sites.yaml`` (or merged preset) without validating links/config.
pub fn load_site_entry(path: &Path, slug: &str) -> Result<SiteEntry> {
    let (_, doc) = read_sites_document_root(path)?;
    let sites = doc
        .get(&Value::from("sites"))
        .and_then(|v| v.as_mapping())
        .context("sites must be a mapping")?;
    let site_val = sites
        .get(&Value::from(slug))
        .with_context(|| format!("site not found: {slug}"))?;
    serde_yaml::from_value(site_val.clone()).with_context(|| format!("parse sites.{slug}"))
}

pub fn patch_preset_site(
    path: &Path,
    slug: &str,
    name: Option<&str>,
    loc: Option<[f64; 2]>,
    tags: Option<&[String]>,
    height_m: Option<Option<f64>>,
) -> Result<()> {
    let (layout, mut doc) = read_sites_document_root(path)?;
    let sites = sites_map_mut(&mut doc)?;
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

    write_sites_document(&layout, &doc)
}

pub fn patch_preset_sites_tags(
    path: &Path,
    slugs: &[String],
    add_tags: &[String],
    remove_tags: &[String],
) -> Result<()> {
    let (layout, mut doc) = read_sites_document_root(path)?;
    let sites = sites_map_mut(&mut doc)?;
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
    write_sites_document(&layout, &doc)
}

pub fn patch_preset_site_tags(
    path: &Path,
    slug: &str,
    add_tags: &[String],
    remove_tags: &[String],
) -> Result<()> {
    patch_preset_sites_tags(path, &[slug.to_string()], add_tags, remove_tags)
}

pub fn remove_preset_site(path: &Path, slug: &str) -> Result<()> {
    let (layout, mut doc) = read_sites_document_root(path)?;
    let sites = sites_map_mut(&mut doc)?;
    sites
        .remove(&Value::from(slug))
        .with_context(|| format!("site not found: {slug}"))?;
    write_sites_document(&layout, &doc)
}

fn unique_land_source_id(base: &str, existing: &HashSet<String>) -> String {
    if !existing.contains(base) {
        return base.to_string();
    }
    let mut n = 2;
    loop {
        let candidate = format!("{base}-{n}");
        if !existing.contains(&candidate) {
            return candidate;
        }
        n += 1;
    }
}

pub fn land_source_id_for_path(path: &str, existing: &HashSet<String>) -> String {
    let stem = std::path::Path::new(path)
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("source");
    let base = crate::model::slugify_files_segment(stem);
    unique_land_source_id(&base, existing)
}

fn save_land_from_preset(config_path: &Path, land: &crate::model::LandConfig) -> Result<()> {
    let layout = layout_for(config_path)?;
    let land_value = serde_yaml::to_value(land).context("serialize land")?;
    if layout.uses_split_land() {
        let mut doc = read_preset_document(&layout.land_document_path())?;
        doc.insert(Value::from("land"), land_value);
        write_preset_value(&layout.land_document_path(), Value::Mapping(doc))?;
    } else {
        let mut merged = read_merged_document(&layout)?;
        merged.insert(Value::from("land"), land_value);
        write_merged_document(&layout, &merged)?;
    }
    Ok(())
}

pub fn import_land_source(
    path: &Path,
    rel_path: String,
    label: Option<String>,
    layers: Vec<LandLayerEntry>,
) -> Result<(String, LandSourceEntry)> {
    let mut preset = load_preset(path)?;
    let existing: HashSet<String> = preset.land.sources.keys().cloned().collect();
    let source_id = land_source_id_for_path(&rel_path, &existing);
    let entry = LandSourceEntry {
        path: rel_path,
        label: label.or_else(|| Some(source_id.clone())),
        layers,
        enabled: true,
        refresh: None,
    };
    preset.land.sources.insert(source_id.clone(), entry.clone());
    save_land_from_preset(path, &preset.land)?;
    Ok((source_id, entry))
}

pub fn patch_land_source(
    path: &Path,
    source_id: &str,
    label: Option<String>,
    layers: Vec<LandLayerEntry>,
) -> Result<LandSourceEntry> {
    let mut preset = load_preset(path)?;
    let entry = preset
        .land
        .sources
        .get_mut(source_id)
        .with_context(|| format!("unknown land source: {source_id}"))?;
    if let Some(label) = label {
        entry.label = Some(label);
    }
    entry.layers = layers;
    let out = entry.clone();
    save_land_from_preset(path, &preset.land)?;
    Ok(out)
}

pub fn patch_land_source_last_updated(path: &Path, source_id: &str, last_updated: &str) -> Result<()> {
    let mut preset = load_preset(path)?;
    let entry = preset
        .land
        .sources
        .get_mut(source_id)
        .with_context(|| format!("unknown land source: {source_id}"))?;
    let refresh = entry.refresh.get_or_insert_with(LandSourceRefresh::default);
    refresh.last_updated = Some(last_updated.to_string());
    save_land_from_preset(path, &preset.land)
}

pub fn delete_land_source(path: &Path, source_id: &str) -> Result<()> {
    let mut preset = load_preset(path)?;
    preset
        .land
        .sources
        .remove(source_id)
        .with_context(|| format!("unknown land source: {source_id}"))?;
    if let Some(sidebar) = preset.land.sidebar.as_mut() {
        for folder in &mut sidebar.folders {
            folder.sources.retain(|sid| sid != source_id);
        }
        sidebar.unfiled_sources.retain(|sid| sid != source_id);
    }
    save_land_from_preset(path, &preset.land)
}

pub fn patch_land_sidebar(path: &Path, sidebar: LandSidebar) -> Result<LandSidebar> {
    let mut preset = load_preset(path)?;
    preset.land.sidebar = Some(sidebar.clone());
    save_land_from_preset(path, &preset.land)?;
    Ok(sidebar)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;
    use std::fs;

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
                node: None,
            },
        )
        .unwrap();

        let text = fs::read_to_string(&path).unwrap();
        assert!(text.find("  first:").unwrap() < text.find("  second:").unwrap());
    }

    #[test]
    fn split_insert_preset_site_writes_sites_yaml() {
        let dir = tempfile::tempdir().unwrap();
        let config_path = dir.path().join("config.yaml");
        write_preset_document(&config_path, &Mapping::new()).unwrap();
        fs::write(
            dir.path().join("sites.yaml"),
            "sites:\n  first:\n    name: First\n    loc: [1.0, 2.0]\n    tags: []\n",
        )
        .unwrap();

        insert_preset_site(
            &config_path,
            "second",
            &SiteEntry {
                name: "Second".into(),
                loc: [3.0, 4.0],
                height_m: None,
                description: None,
                tags: vec![],
                node: None,
            },
        )
        .unwrap();

        let config_text = fs::read_to_string(&config_path).unwrap();
        assert!(!config_text.contains("second:"));
        let sites_text = fs::read_to_string(dir.path().join("sites.yaml")).unwrap();
        assert!(sites_text.contains("second:"));
        let preset = load_preset(&config_path).unwrap();
        assert!(preset.sites.contains_key("second"));
    }

    #[test]
    fn patch_preset_site_clears_height_when_some_none() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("config.yaml");

        let mut site = Mapping::new();
        site.insert(Value::from("name"), Value::from("Tower"));
        site.insert(
            Value::from("loc"),
            Value::Sequence(vec![Value::from(1.0), Value::from(2.0)]),
        );
        site.insert(Value::from("height_m"), Value::from(12.0));
        let mut sites = Mapping::new();
        sites.insert(Value::from("tower"), Value::Mapping(site));
        let mut root = Mapping::new();
        root.insert(Value::from("sites"), Value::Mapping(sites));
        write_preset_document(&path, &root).unwrap();

        patch_preset_site(&path, "tower", None, None, None, Some(None)).unwrap();
        let text = fs::read_to_string(&path).unwrap();
        assert!(text.contains("height_m: null") || !text.contains("height_m: 12"));
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
                node: None,
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
                node: None,
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
