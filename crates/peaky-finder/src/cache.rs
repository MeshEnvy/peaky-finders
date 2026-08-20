//! Content-addressed disk cache under `<preset>/.peaky/cache/finder/`.

use std::fs;
use std::path::{Path, PathBuf};
use std::time::Instant;

use anyhow::{Context, Result};
use peaky_preset::resolved_preset_cache_dir;
use serde::{de::DeserializeOwned, Serialize};
use sha2::{Digest, Sha256};

use crate::telemetry::CacheLedger;
use crate::CACHE_SCHEMA_VERSION;

pub fn finder_cache_root(preset_path: &Path) -> PathBuf {
    resolved_preset_cache_dir(preset_path).join("finder")
}

/// Content digest for solver-sensitive cache entries (cover/link/run). Includes
/// [`CACHE_SCHEMA_VERSION`] so algorithm bumps invalidate those keys.
pub fn digest_hex(parts: &[&str]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(format!("v{CACHE_SCHEMA_VERSION}").as_bytes());
    for p in parts {
        hasher.update(b"|");
        hasher.update(p.as_bytes());
    }
    format!("{:x}", hasher.finalize())
}

/// Content digest that does **not** include the solver schema version.
/// Use for land/gap artifacts that only depend on their explicit key parts.
pub fn digest_hex_stable(parts: &[&str]) -> String {
    let mut hasher = Sha256::new();
    for p in parts {
        hasher.update(b"|");
        hasher.update(p.as_bytes());
    }
    format!("{:x}", hasher.finalize())
}

pub fn short_digest(full: &str) -> &str {
    &full[..full.len().min(8)]
}

pub struct FinderCache {
    root: PathBuf,
}

impl FinderCache {
    pub fn new(preset_path: &Path) -> Result<Self> {
        let root = finder_cache_root(preset_path);
        fs::create_dir_all(&root).with_context(|| format!("create finder cache {}", root.display()))?;
        Ok(Self { root })
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    pub fn path_for(&self, subdir: &str, key_digest: &str, ext: &str) -> PathBuf {
        self.root.join(subdir).join(format!("{key_digest}.{ext}"))
    }

    pub fn get_or_insert_bool<F>(
        &self,
        ledger: &CacheLedger,
        op: &str,
        subdir: &str,
        key_digest: &str,
        detail: &str,
        build: F,
    ) -> Result<bool>
    where
        F: FnOnce() -> Result<bool>,
    {
        let path = self.path_for(subdir, key_digest, "bool");
        if path.is_file() {
            if let Ok(bytes) = fs::read(&path) {
                if bytes.len() == 1 {
                    ledger.log_hit(op, detail);
                    return Ok(bytes[0] != 0);
                }
            }
            let _ = fs::remove_file(&path);
        }
        let t0 = Instant::now();
        let value = build()?;
        ledger.log_miss(op, detail, t0.elapsed());
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        let tmp = path.with_extension("bool.tmp");
        fs::write(&tmp, [u8::from(value)])?;
        fs::rename(&tmp, &path)?;
        Ok(value)
    }

    pub fn get_or_insert_bytes<F>(
        &self,
        ledger: &CacheLedger,
        op: &str,
        subdir: &str,
        key_digest: &str,
        detail: &str,
        build: F,
    ) -> Result<Vec<u8>>
    where
        F: FnOnce() -> Result<Vec<u8>>,
    {
        let path = self.path_for(subdir, key_digest, "bin");
        if path.is_file() {
            if let Ok(bytes) = fs::read(&path) {
                ledger.log_hit(op, detail);
                return Ok(bytes);
            }
            let _ = fs::remove_file(&path);
        }
        let t0 = Instant::now();
        let bytes = build()?;
        ledger.log_miss(op, detail, t0.elapsed());
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        let tmp = path.with_extension("bin.tmp");
        fs::write(&tmp, &bytes)?;
        fs::rename(&tmp, &path)?;
        Ok(bytes)
    }

    pub fn get_or_insert_json<T, F>(
        &self,
        ledger: &CacheLedger,
        op: &str,
        subdir: &str,
        key_digest: &str,
        detail: &str,
        build: F,
    ) -> Result<T>
    where
        T: Serialize + DeserializeOwned,
        F: FnOnce() -> Result<T>,
    {
        let path = self.path_for(subdir, key_digest, "json");
        if path.is_file() {
            if let Ok(text) = fs::read_to_string(&path) {
                if let Ok(value) = serde_json::from_str(&text) {
                    ledger.log_hit(op, detail);
                    return Ok(value);
                }
            }
            let _ = fs::remove_file(&path);
        }
        let t0 = Instant::now();
        let value = build()?;
        ledger.log_miss(op, detail, t0.elapsed());
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        let text = serde_json::to_string_pretty(&value)?;
        let tmp = path.with_extension("json.tmp");
        fs::write(&tmp, text)?;
        fs::rename(&tmp, &path)?;
        Ok(value)
    }

    pub fn write_ledger(&self, run_digest: &str, ledger: &CacheLedger) -> Result<()> {
        let dir = self.root.join("runs").join(run_digest);
        fs::create_dir_all(&dir)?;
        let path = dir.join("ledger.json");
        let payload = serde_json::json!({
            "stats": ledger.stats(),
            "dem_tiles_fetched": ledger.dem_tiles_fetched(),
            "dem_tiles_loaded": ledger.dem_tiles_loaded(),
        });
        fs::write(&path, serde_json::to_string_pretty(&payload)?)?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn digest_stable_for_same_inputs() {
        let a = digest_hex(&["route", "abc", "installed"]);
        let b = digest_hex(&["route", "abc", "installed"]);
        assert_eq!(a, b);
        assert_ne!(a, digest_hex(&["route", "abc", "planned"]));
    }

    #[test]
    fn digest_hex_stable_omits_schema_version() {
        let stable = digest_hex_stable(&["gap_overview", "v1", "wp", "land"]);
        let schema = digest_hex(&["gap_overview", "v1", "wp", "land"]);
        assert_ne!(stable, schema);
        assert_eq!(stable, digest_hex_stable(&["gap_overview", "v1", "wp", "land"]));
    }
}
