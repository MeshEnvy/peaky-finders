//! Public Skadi SRTM tile fetch (``elevation-tiles-prod`` on AWS S3).

use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::thread;
use std::time::Duration;

use anyhow::{bail, Context, Result};

const SKADI_BUCKET: &str = "elevation-tiles-prod";
const SKADI_PREFIX: &str = "v2/skadi";
const DEFAULT_FETCH_TIMEOUT: Duration = Duration::from_secs(180);
const DEFAULT_CONNECT_TIMEOUT: Duration = Duration::from_secs(30);
const DEFAULT_FETCH_RETRIES: u32 = 5;
const MAX_BACKOFF: Duration = Duration::from_secs(60);

fn fetch_timeout() -> Duration {
    env_u64("PEAKY_SKADI_FETCH_TIMEOUT_SECS")
        .map(|s| Duration::from_secs(s.max(10)))
        .unwrap_or(DEFAULT_FETCH_TIMEOUT)
}

fn connect_timeout() -> Duration {
    env_u64("PEAKY_SKADI_FETCH_CONNECT_TIMEOUT_SECS")
        .map(|s| Duration::from_secs(s.max(5)))
        .unwrap_or(DEFAULT_CONNECT_TIMEOUT)
}

fn fetch_retries() -> u32 {
    env_u64("PEAKY_SKADI_FETCH_RETRIES")
        .map(|n| n.min(20) as u32)
        .unwrap_or(DEFAULT_FETCH_RETRIES)
}

fn env_u64(name: &str) -> Option<u64> {
    std::env::var(name)
        .ok()
        .and_then(|v| v.trim().parse().ok())
}

fn retry_backoff(attempt: u32) -> Duration {
    let base_ms = 2_000u64.saturating_mul(1u64 << attempt.min(5));
    let capped = base_ms.min(MAX_BACKOFF.as_millis() as u64);
    let jitter = (std::process::id() as u64 + attempt as u64 * 7919) % 500;
    Duration::from_millis(capped + jitter)
}

fn is_retryable_http(status: u16) -> bool {
    matches!(status, 408 | 429 | 500 | 502 | 503 | 504)
}

fn is_retryable_error(err: &anyhow::Error) -> bool {
    let msg = err.to_string().to_ascii_lowercase();
    msg.contains("timeout")
        || msg.contains("timed out")
        || msg.contains("connection")
        || msg.contains("dns")
        || msg.contains("network")
        || msg.contains("broken pipe")
        || msg.contains("reset")
        || msg.contains("http 408")
        || msg.contains("http 429")
        || msg.contains("http 500")
        || msg.contains("http 502")
        || msg.contains("http 503")
        || msg.contains("http 504")
}

/// Ensure one ``*.hgt.gz`` exists under ``mirror_root``; download from Skadi S3 on miss (sync).
pub fn ensure_mirror_tile(mirror_root: &Path, tile_name: &str, verbose: bool) -> Result<PathBuf> {
    if mirror_root.join(tile_name).is_file() {
        validate_tile_name(tile_name)?;
        return Ok(mirror_root.join(tile_name));
    }
    fetch_and_write_mirror_tile(mirror_root, tile_name, verbose)?;
    Ok(mirror_root.join(tile_name))
}

/// Download from Skadi and write mirror file (used by [`crate::dem_mirror::DemMirror`] worker).
pub fn fetch_and_write_mirror_tile(
    mirror_root: &Path,
    tile_name: &str,
    verbose: bool,
) -> Result<()> {
    validate_tile_name(tile_name)?;
    mirror_root
        .try_exists()
        .with_context(|| format!("mirror root {}", mirror_root.display()))?;
    fs::create_dir_all(mirror_root)
        .with_context(|| format!("create mirror root {}", mirror_root.display()))?;

    let path = mirror_root.join(tile_name);
    if path.is_file() {
        return Ok(());
    }
    remove_partial(&path);

    if verbose {
        eprintln!("[splatter] DEM fetch {tile_name} (Skadi S3)");
    }
    let bytes = fetch_skadi_hgt_gzip_bytes(tile_name, verbose)
        .with_context(|| format!("fetch Skadi tile {tile_name}"))?;
    if let Err(e) = write_bytes_atomic(&path, &bytes) {
        remove_partial(&path);
        return Err(e);
    }
    if verbose {
        eprintln!(
            "[splatter] DEM fetch wrote {} ({} bytes)",
            path.display(),
            bytes.len()
        );
    }
    Ok(())
}

/// Read gzipped HGT bytes from the mirror, fetching from Skadi on miss.
pub fn read_mirror_tile_gz_bytes(mirror_root: &Path, tile_name: &str, verbose: bool) -> Result<Vec<u8>> {
    let path = ensure_mirror_tile(mirror_root, tile_name, verbose)?;
    std::fs::read(&path).with_context(|| format!("read mirror tile {}", path.display()))
}

/// Tile names in ``bounds`` that are absent or empty in ``mirror_root``.
pub fn missing_mirror_tiles(mirror_root: &Path, tile_names: &[String]) -> Result<Vec<String>> {
    let mut missing = Vec::new();
    for name in tile_names {
        validate_tile_name(name)?;
        let path = mirror_root.join(name);
        let absent = match path.metadata() {
            Ok(meta) => !meta.is_file() || meta.len() == 0,
            Err(_) => true,
        };
        if absent {
            missing.push(name.clone());
        }
    }
    Ok(missing)
}

fn validate_tile_name(tile_name: &str) -> Result<()> {
    if tile_name.is_empty()
        || tile_name.contains('/')
        || tile_name.contains('\\')
        || tile_name.starts_with('.')
        || !tile_name.ends_with(".hgt.gz")
    {
        bail!("unsafe or invalid Skadi tile mirror key: {tile_name:?}");
    }
    Ok(())
}

fn skadi_s3_urls(tile_name: &str) -> [String; 2] {
    let tile_dir_prefix = &tile_name[..3.min(tile_name.len())];
    [
        format!(
            "https://{SKADI_BUCKET}.s3.amazonaws.com/{SKADI_PREFIX}/{tile_dir_prefix}/{tile_name}"
        ),
        format!(
            "https://{SKADI_BUCKET}.s3.amazonaws.com/skadi/{tile_dir_prefix}/{tile_name}"
        ),
    ]
}

fn fetch_skadi_hgt_gzip_bytes(tile_name: &str, verbose: bool) -> Result<Vec<u8>> {
    validate_tile_name(tile_name)?;
    let client = reqwest::blocking::Client::builder()
        .connect_timeout(connect_timeout())
        .timeout(fetch_timeout())
        .build()
        .context("build HTTP client")?;

    let urls = skadi_s3_urls(tile_name);
    let max_attempts = fetch_retries();
    let mut last_err: Option<anyhow::Error> = None;

    for attempt in 0..max_attempts {
        if attempt > 0 {
            let wait = retry_backoff(attempt - 1);
            if verbose {
                eprintln!(
                    "[splatter] DEM fetch retry {tile_name} attempt {}/{} in {:.1}s",
                    attempt + 1,
                    max_attempts,
                    wait.as_secs_f64()
                );
            }
            thread::sleep(wait);
        }

        match fetch_skadi_once(&client, tile_name, &urls) {
            Ok(bytes) => return Ok(bytes),
            Err(e) => {
                let retryable = is_retryable_error(&e);
                last_err = Some(e);
                if !retryable || attempt + 1 >= max_attempts {
                    break;
                }
            }
        }
    }

    Err(last_err.unwrap_or_else(|| anyhow::anyhow!("no Skadi URLs tried for {tile_name}")))
        .with_context(|| format!("Skadi tile {tile_name} not found in public S3 mirror"))
}

fn fetch_skadi_once(
    client: &reqwest::blocking::Client,
    tile_name: &str,
    urls: &[String],
) -> Result<Vec<u8>> {
    let mut last_err: Option<anyhow::Error> = None;
    for url in urls {
        match client.get(url).send() {
            Ok(resp) if resp.status().is_success() => {
                return resp
                    .bytes()
                    .context("read Skadi tile body")
                    .map(|b| b.to_vec());
            }
            Ok(resp) if resp.status().as_u16() == 404 => {
                last_err = Some(anyhow::anyhow!("HTTP 404 for {url}"));
            }
            Ok(resp) => {
                let status = resp.status().as_u16();
                last_err = Some(anyhow::anyhow!("HTTP {status} for {url}"));
                if is_retryable_http(status) {
                    break;
                }
            }
            Err(e) => {
                last_err = Some(e.into());
                break;
            }
        }
    }
    Err(last_err.unwrap_or_else(|| anyhow::anyhow!("no Skadi URLs tried for {tile_name}")))
}

fn write_bytes_atomic(path: &Path, data: &[u8]) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let tmp = partial_path(path);
    let write_result = (|| -> Result<()> {
        let mut f = fs::File::create(&tmp)?;
        f.write_all(data)?;
        f.sync_all()?;
        Ok(())
    })();
    if let Err(e) = write_result {
        let _ = fs::remove_file(&tmp);
        return Err(e);
    }
    fs::rename(&tmp, path)?;
    Ok(())
}

fn partial_path(path: &Path) -> PathBuf {
    path.with_file_name(format!(
        "{}.partial",
        path.file_name()
            .and_then(|s| s.to_str())
            .unwrap_or("tile")
    ))
}

fn remove_partial(path: &Path) {
    let _ = fs::remove_file(partial_path(path));
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn retry_backoff_grows_and_caps() {
        let a = retry_backoff(0);
        let b = retry_backoff(3);
        assert!(b >= a);
        assert!(retry_backoff(10).as_secs() <= 60);
    }

    #[test]
    fn retryable_http_codes() {
        assert!(is_retryable_http(429));
        assert!(is_retryable_http(503));
        assert!(!is_retryable_http(404));
    }

    #[test]
    fn tile_name_validation_rejects_traversal() {
        assert!(validate_tile_name("../N40W119.hgt.gz").is_err());
        assert!(validate_tile_name("N40W119.hgt").is_err());
        assert!(validate_tile_name("N40W119.hgt.gz").is_ok());
    }

    #[test]
    fn missing_mirror_tiles_detects_absent() {
        let dir = std::env::temp_dir().join(format!("splatter-missing-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let names = vec!["N40W119.hgt.gz".to_string()];
        let missing = missing_mirror_tiles(&dir, &names).unwrap();
        assert_eq!(missing, names);
        std::fs::write(dir.join("N40W119.hgt.gz"), b"x").unwrap();
        let missing = missing_mirror_tiles(&dir, &names).unwrap();
        assert!(missing.is_empty());
        let _ = std::fs::remove_dir_all(&dir);
    }
}
