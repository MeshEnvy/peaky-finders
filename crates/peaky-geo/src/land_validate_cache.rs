//! Persist validation fingerprints so unchanged land sources skip re-parse at boot.

use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;

use anyhow::{Context, Result};
use peaky_preset::LandSourceEntry;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
struct ValidateCacheEntry {
    size: u64,
    mtime_secs: u64,
    layers_key: String,
    ok: bool,
}

#[derive(Debug, Default, Serialize, Deserialize)]
struct ValidateCacheFile {
    #[serde(default)]
    entries: HashMap<String, ValidateCacheEntry>,
}

pub struct LandValidateCache {
    path: PathBuf,
    entries: Mutex<HashMap<String, ValidateCacheEntry>>,
    dirty: AtomicBool,
}

impl LandValidateCache {
    pub fn open(project_dir: &Path) -> Result<Self> {
        let cache_root = project_dir.join(".peaky/cache/land");
        fs::create_dir_all(&cache_root)?;
        let path = cache_root.join("validate.json");
        let entries = if path.is_file() {
            fs::read_to_string(&path)
                .ok()
                .and_then(|text| serde_json::from_str::<ValidateCacheFile>(&text).ok())
                .map(|file| file.entries)
                .unwrap_or_default()
        } else {
            HashMap::new()
        };
        Ok(Self {
            path,
            entries: Mutex::new(entries),
            dirty: AtomicBool::new(false),
        })
    }

    pub fn persist(&self) -> Result<()> {
        if !self.dirty.load(Ordering::Relaxed) {
            return Ok(());
        }
        let entries = self
            .entries
            .lock()
            .expect("land validate cache")
            .clone();
        let mut keys: Vec<_> = entries.keys().cloned().collect();
        keys.sort();
        let ordered: HashMap<String, ValidateCacheEntry> = keys
            .into_iter()
            .filter_map(|k| entries.get(&k).map(|v| (k, v.clone())))
            .collect();
        let payload = ValidateCacheFile { entries: ordered };
        fs::write(
            &self.path,
            serde_json::to_string_pretty(&payload)? + "\n",
        )
        .with_context(|| format!("write {}", self.path.display()))?;
        self.dirty.store(false, Ordering::Relaxed);
        Ok(())
    }

    pub fn lookup(
        &self,
        rel_path: &str,
        entry: &LandSourceEntry,
        fingerprint: (u64, u64),
    ) -> bool {
        let key = normalize_rel_path(rel_path);
        let layers_key = layers_key(entry);
        let (size, mtime_secs) = fingerprint;
        let cache = self.entries.lock().expect("land validate cache");
        cache
            .get(&key)
            .is_some_and(|row| row.ok && row.size == size && row.mtime_secs == mtime_secs && row.layers_key == layers_key)
    }

    pub fn remember_ok(&self, rel_path: &str, entry: &LandSourceEntry, fingerprint: (u64, u64)) {
        let key = normalize_rel_path(rel_path);
        let row = ValidateCacheEntry {
            size: fingerprint.0,
            mtime_secs: fingerprint.1,
            layers_key: layers_key(entry),
            ok: true,
        };
        let mut cache = self.entries.lock().expect("land validate cache");
        if cache.get(&key) == Some(&row) {
            return;
        }
        cache.insert(key, row);
        self.dirty.store(true, Ordering::Relaxed);
    }
}

pub fn source_fingerprint(abs: &Path, is_gdb: bool) -> Result<(u64, u64)> {
    if is_gdb {
        gdb_dir_fingerprint(abs)
    } else {
        file_fingerprint(abs)
    }
}

fn normalize_rel_path(rel: &str) -> String {
    rel.trim().replace('\\', "/")
}

fn layers_key(entry: &LandSourceEntry) -> String {
    let mut names: Vec<&str> = entry.layers.iter().map(|layer| layer.name.as_str()).collect();
    names.sort_unstable();
    names.dedup();
    names.join("\0")
}

fn mtime_secs(meta: &fs::Metadata) -> Result<u64> {
    Ok(meta
        .modified()
        .context("source mtime")?
        .duration_since(std::time::UNIX_EPOCH)
        .context("source mtime before epoch")?
        .as_secs())
}

fn file_fingerprint(path: &Path) -> Result<(u64, u64)> {
    let meta = fs::metadata(path).with_context(|| format!("stat {}", path.display()))?;
    Ok((meta.len(), mtime_secs(&meta)?))
}

fn gdb_dir_fingerprint(path: &Path) -> Result<(u64, u64)> {
    let mut total_size = 0u64;
    let mut max_mtime = 0u64;
    walk_files(path, &mut |file_path| {
        let meta = fs::metadata(file_path)?;
        total_size = total_size.saturating_add(meta.len());
        max_mtime = max_mtime.max(mtime_secs(&meta)?);
        Ok(())
    })?;
    Ok((total_size, max_mtime))
}

fn walk_files(path: &Path, visit: &mut dyn FnMut(&Path) -> Result<()>) -> Result<()> {
    if path.is_file() {
        visit(path)?;
        return Ok(());
    }
    for entry in fs::read_dir(path).with_context(|| format!("read dir {}", path.display()))? {
        let entry = entry?;
        let child = entry.path();
        if child.is_dir() {
            walk_files(&child, visit)?;
        } else if child.is_file() {
            visit(&child)?;
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use peaky_preset::LandLayerEntry;

    fn sample_entry(layers: Vec<&str>) -> LandSourceEntry {
        LandSourceEntry {
            path: "data/test.gdb".to_string(),
            layers: layers
                .into_iter()
                .map(|name| LandLayerEntry {
                    name: name.to_string(),
                    id: None,
                    role: None,
                    include: Vec::new(),
                    exclude: Vec::new(),
                    label_field: None,
                    style_field: None,
                    style: None,
                })
                .collect(),
            label: None,
            enabled: true,
            refresh: None,
        }
    }

    #[test]
    fn layers_key_is_order_independent() {
        let entry_a = sample_entry(vec!["b", "a"]);
        let entry_b = sample_entry(vec!["a", "b"]);
        assert_eq!(layers_key(&entry_a), layers_key(&entry_b));
        let entry_dup = sample_entry(vec!["a", "b", "a"]);
        assert_eq!(layers_key(&entry_a), layers_key(&entry_dup));
    }
}
