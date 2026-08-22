//! Low-level preset YAML file read/write.

use std::fs;
use std::path::Path;

use anyhow::{Context, Result};
use serde_yaml::{Mapping, Value};

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
    if !PRESET_EXTENSIONS
        .iter()
        .any(|ext| suffix == ext.trim_start_matches('.'))
    {
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
    let root: Value = serde_yaml::from_str(&text)
        .with_context(|| format!("parse preset YAML: {}", path.display()))?;
    match root {
        Value::Mapping(map) => Ok(map),
        _ => anyhow::bail!("preset YAML root must be a mapping at {}", path.display()),
    }
}

pub fn write_preset_value(path: &Path, value: Value) -> Result<()> {
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

pub fn write_yaml_document(path: &Path, payload: &Mapping) -> Result<()> {
    write_preset_value(path, Value::Mapping(payload.clone()))
}
