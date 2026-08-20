//! Global home catalog (modems, environments) from PEAKY_HOME.

use std::collections::HashMap;
use std::fs;

use anyhow::{Context, Result};
use serde_yaml::Mapping;

use crate::paths::peaky_home;

pub fn load_modem_catalog() -> Result<HashMap<String, Mapping>> {
    load_named_catalog("modems.yaml", "modem_presets")
}

pub fn load_environment_catalog() -> Result<HashMap<String, Mapping>> {
    load_named_catalog("environments.yaml", "environment_presets")
}

fn load_named_catalog(filename: &str, catalog_key: &str) -> Result<HashMap<String, Mapping>> {
    let path = peaky_home().join(filename);
    if !path.is_file() {
        return Ok(HashMap::new());
    }
    let text = fs::read_to_string(&path).with_context(|| format!("read {}", path.display()))?;
    let root: serde_yaml::Value = serde_yaml::from_str(&text)?;
    let map = match root {
        serde_yaml::Value::Mapping(m) => m,
        _ => return Ok(HashMap::new()),
    };
    let nested = map
        .get(&serde_yaml::Value::from(catalog_key))
        .and_then(|v| v.as_mapping())
        .cloned();
    let presets = nested.as_ref().unwrap_or(&map);
    let mut out = HashMap::new();
    for (k, v) in presets {
        let key = k.as_str().unwrap_or("").to_string();
        if key.is_empty() {
            continue;
        }
        if let serde_yaml::Value::Mapping(entry) = v {
            out.insert(key, entry.clone());
        }
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn load_named_catalog_reads_nested_presets_key() {
        let dir = tempfile::tempdir().unwrap();
        std::env::set_var("PEAKY_HOME", dir.path());
        let yaml = r#"
modem_presets:
  meshcore-us:
    frequency_mhz: 915.0
    spreading_factor: 10
"#;
        std::fs::write(dir.path().join("modems.yaml"), yaml).unwrap();
        let catalog = load_modem_catalog().unwrap();
        assert!(catalog.contains_key("meshcore-us"));
        let entry = &catalog["meshcore-us"];
        let freq = entry
            .get(&serde_yaml::Value::from("frequency_mhz"))
            .and_then(|v| v.as_f64());
        assert_eq!(freq, Some(915.0));
    }
}

pub fn load_home_simulation() -> Result<Mapping> {
    let path = peaky_home().join("config.yaml");
    if !path.is_file() {
        return Ok(Mapping::new());
    }
    let text = fs::read_to_string(&path)?;
    let root: serde_yaml::Value = serde_yaml::from_str(&text)?;
    match root {
        serde_yaml::Value::Mapping(m) => Ok(m.get(&serde_yaml::Value::from("simulation"))
            .and_then(|v| match v {
                serde_yaml::Value::Mapping(inner) => Some(inner.clone()),
                _ => None,
            })
            .unwrap_or_default()),
        _ => Ok(Mapping::new()),
    }
}
