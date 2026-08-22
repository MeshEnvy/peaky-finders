//! Project-local modem and environment preset catalogs.

use std::collections::HashMap;
use std::path::Path;

use anyhow::Result;
use serde_yaml::Mapping;

use crate::yaml_io::read_preset_document;
use crate::model::Preset;

pub fn modem_catalog_from_preset(preset: &Preset) -> HashMap<String, Mapping> {
    preset.modem_presets.clone()
}

pub fn environment_catalog_from_preset(preset: &Preset) -> HashMap<String, Mapping> {
    preset.environment_presets.clone()
}

pub fn load_modem_catalog(preset_path: &Path) -> Result<HashMap<String, Mapping>> {
    let raw = read_preset_document(preset_path)?;
    Ok(extract_named_catalog(&raw, "modem_presets"))
}

pub fn load_environment_catalog(preset_path: &Path) -> Result<HashMap<String, Mapping>> {
    let raw = read_preset_document(preset_path)?;
    Ok(extract_named_catalog(&raw, "environment_presets"))
}

fn extract_named_catalog(raw: &Mapping, catalog_key: &str) -> HashMap<String, Mapping> {
    let mut out = HashMap::new();
    let Some(presets) = raw
        .get(&serde_yaml::Value::from(catalog_key))
        .and_then(|v| v.as_mapping())
    else {
        return out;
    };
    for (k, v) in presets {
        let key = k.as_str().unwrap_or("").to_string();
        if key.is_empty() {
            continue;
        }
        if let serde_yaml::Value::Mapping(entry) = v {
            out.insert(key, entry.clone());
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    #[test]
    fn load_modem_catalog_reads_project_yaml() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("config.yaml");
        let yaml = r#"
modem_presets:
  meshcore-us:
    frequency_mhz: 915.0
    spreading_factor: 10
simulation:
  radius_km: 50
  viewshed_quality: 3
sites:
  a:
    name: A
    loc: [39.0, -119.0]
"#;
        std::fs::File::create(&path)
            .unwrap()
            .write_all(yaml.as_bytes())
            .unwrap();
        let catalog = load_modem_catalog(&path).unwrap();
        assert!(catalog.contains_key("meshcore-us"));
        let freq = catalog["meshcore-us"]
            .get(&serde_yaml::Value::from("frequency_mhz"))
            .and_then(|v| v.as_f64());
        assert_eq!(freq, Some(915.0));
    }
}
