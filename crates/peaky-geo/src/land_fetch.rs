//! Download land sources from ArcGIS FeatureServer, hub GeoJSON, or FileGDB zips.

use std::fs;
use std::io::{Cursor, Write};
use std::path::{Path, PathBuf};
use std::thread;
use std::time::Duration;

use anyhow::{bail, Context, Result};
use geojson::{Feature, FeatureCollection};
use peaky_preset::LandDownloadKind;
use reqwest::blocking::Client;
use serde_json::{json, Value};
use zip::ZipArchive;

const USER_AGENT: &str = "peaky-land-refresh/1";

fn client() -> Result<Client> {
    Client::builder()
        .user_agent(USER_AGENT)
        .build()
        .context("build HTTP client")
}

fn format_bytes(n: u64) -> String {
    const MIB: u64 = 1024 * 1024;
    const KIB: u64 = 1024;
    if n >= MIB {
        format!("{:.1} MB", n as f64 / MIB as f64)
    } else if n >= KIB {
        format!("{:.1} KB", n as f64 / KIB as f64)
    } else {
        format!("{n} B")
    }
}

fn log_refresh_progress(
    source_id: &str,
    path: &str,
    fetched: u32,
    total: Option<u64>,
    last_logged_pct: &mut u8,
) {
    match total {
        Some(total) if total > 0 => {
            let pct = ((fetched as u64 * 100) / total).min(100) as u8;
            if *last_logged_pct == 0
                || pct >= last_logged_pct.saturating_add(5)
                || (pct == 100 && *last_logged_pct < 100)
            {
                tracing::info!(
                    source_id = %source_id,
                    path = %path,
                    fetched,
                    total,
                    pct,
                    "land refresh: progress"
                );
                *last_logged_pct = pct;
            }
        }
        _ => {
            let bucket = fetched / 5000;
            if fetched <= 500 || bucket > (*last_logged_pct as u32) {
                tracing::info!(
                    source_id = %source_id,
                    path = %path,
                    fetched,
                    "land refresh: progress"
                );
                *last_logged_pct = bucket.min(255) as u8;
            }
        }
    }
}

fn featureserver_total_features(query_base: &str, layer_base: &str) -> Option<u64> {
    let http = client().ok()?;
    let count_url = format!("{query_base}?where=1%3D1&returnCountOnly=true&f=json");
    if let Ok(resp) = http
        .get(&count_url)
        .timeout(Duration::from_secs(120))
        .send()
    {
        if resp.status().is_success() {
            if let Ok(body) = resp.bytes() {
                if let Ok(value) = serde_json::from_slice::<Value>(&body) {
                    if let Some(count) = value.get("count").and_then(|v| v.as_u64()) {
                        return Some(count);
                    }
                }
            }
        }
    }
    let meta_url = format!("{layer_base}?f=json");
    let resp = http.get(&meta_url).timeout(Duration::from_secs(120)).send().ok()?;
    if !resp.status().is_success() {
        return None;
    }
    let body = resp.bytes().ok()?;
    let value: Value = serde_json::from_slice(&body).ok()?;
    value.get("count").and_then(|v| v.as_u64())
}

pub fn download_bytes(url: &str, timeout_secs: u64) -> Result<Vec<u8>> {
    let client = client()?;
    let resp = client
        .get(url)
        .timeout(Duration::from_secs(timeout_secs))
        .send()
        .with_context(|| format!("GET {url}"))?
        .error_for_status()
        .with_context(|| format!("HTTP error for {url}"))?;
    resp.bytes()
        .context("read response body")
        .map(|b| b.to_vec())
}

fn download_bytes_with_progress(
    source_id: &str,
    path: &str,
    url: &str,
    timeout_secs: u64,
) -> Result<Vec<u8>> {
    let client = client()?;
    let resp = client
        .get(url)
        .timeout(Duration::from_secs(timeout_secs))
        .send()
        .with_context(|| format!("GET {url}"))?
        .error_for_status()
        .with_context(|| format!("HTTP error for {url}"))?;
    if let Some(len) = resp.content_length() {
        tracing::info!(
            source_id = %source_id,
            path = %path,
            size = %format_bytes(len),
            "land refresh: downloading"
        );
    } else {
        tracing::info!(
            source_id = %source_id,
            path = %path,
            "land refresh: downloading (size unknown)"
        );
    }
    let data = resp.bytes().context("read response body")?.to_vec();
    tracing::info!(
        source_id = %source_id,
        path = %path,
        size = %format_bytes(data.len() as u64),
        pct = 100,
        "land refresh: progress"
    );
    Ok(data)
}

fn copy_dir_all(src: &Path, dst: &Path) -> Result<()> {
    fs::create_dir_all(dst)?;
    for entry in fs::read_dir(src).with_context(|| format!("read dir {}", src.display()))? {
        let entry = entry?;
        let ty = entry.file_type()?;
        let from = entry.path();
        let to = dst.join(entry.file_name());
        if ty.is_dir() {
            copy_dir_all(&from, &to)?;
        } else {
            fs::copy(&from, &to).with_context(|| format!("copy {} -> {}", from.display(), to.display()))?;
        }
    }
    Ok(())
}

fn remove_dir_all_if_exists(path: &Path) -> Result<()> {
    if path.exists() {
        fs::remove_dir_all(path).with_context(|| format!("remove {}", path.display()))?;
    }
    Ok(())
}

fn find_first_gdb(root: &Path) -> Result<PathBuf> {
    fn walk(dir: &Path) -> Result<Option<PathBuf>> {
        for entry in fs::read_dir(dir).with_context(|| format!("read dir {}", dir.display()))? {
            let entry = entry?;
            let path = entry.path();
            if path.extension().and_then(|s| s.to_str()) == Some("gdb") && path.is_dir() {
                return Ok(Some(path));
            }
            if path.is_dir() {
                if let Some(found) = walk(&path)? {
                    return Ok(Some(found));
                }
            }
        }
        Ok(None)
    }
    walk(root)?.context("no .gdb found in download zip")
}

pub fn install_filegdb_zip(
    source_id: &str,
    path: &str,
    data: &[u8],
    dest_gdb: &Path,
) -> Result<()> {
    if let Some(parent) = dest_gdb.parent() {
        fs::create_dir_all(parent)?;
    }
    tracing::info!(
        source_id = %source_id,
        path = %path,
        size = %format_bytes(data.len() as u64),
        "land refresh: extracting zip"
    );
    let cursor = Cursor::new(data);
    let mut archive = match ZipArchive::new(cursor) {
        Ok(a) => a,
        Err(_) => {
            let payload: Value = serde_json::from_slice(data).context("parse download as JSON")?;
            if payload.get("status").and_then(|v| v.as_str()) == Some("ExportingData") {
                bail!("hub export still generating; retry in a few minutes");
            }
            bail!(
                "unexpected download payload: {}",
                payload.to_string().chars().take(200).collect::<String>()
            );
        }
    };

    let tmp = tempfile::tempdir().context("tempdir for gdb extract")?;
    archive
        .extract(tmp.path())
        .context("extract filegdb zip")?;
    tracing::info!(
        source_id = %source_id,
        path = %path,
        pct = 100,
        "land refresh: progress"
    );
    let gdb = find_first_gdb(tmp.path())?;
    remove_dir_all_if_exists(dest_gdb)?;
    copy_dir_all(&gdb, dest_gdb)?;
    Ok(())
}

fn write_feature_collection(
    source_id: &str,
    rel_path: &str,
    dest: &Path,
    features: &[Feature],
    name: &str,
) -> Result<()> {
    if let Some(parent) = dest.parent() {
        fs::create_dir_all(parent)?;
    }
    tracing::info!(
        source_id = %source_id,
        path = %rel_path,
        features = features.len(),
        dest = %dest.display(),
        "land refresh: writing geojson"
    );
    let fc = FeatureCollection {
        bbox: None,
        features: features.to_vec(),
        foreign_members: None,
    };
    let mut value = serde_json::to_value(&fc).context("serialize FeatureCollection")?;
    if let Value::Object(ref mut map) = value {
        map.insert("name".to_string(), json!(name));
    }
    let mut file = fs::File::create(dest).with_context(|| format!("create {}", dest.display()))?;
    serde_json::to_writer(&mut file, &value).context("write geojson")?;
    file.write_all(b"\n").ok();
    let size = fs::metadata(dest)
        .with_context(|| format!("stat {}", dest.display()))?
        .len();
    tracing::info!(
        source_id = %source_id,
        path = %rel_path,
        size = %format_bytes(size),
        "land refresh: write done"
    );
    Ok(())
}

pub fn download_featureserver_geojson(
    source_id: &str,
    path: &str,
    service_layer_url: &str,
    dest: &Path,
    verbose: bool,
) -> Result<()> {
    let mut base = service_layer_url.trim_end_matches('/').to_string();
    if !base.ends_with("/0") {
        base.push_str("/0");
    }
    let query_base = format!("{base}/query");
    let total = featureserver_total_features(&query_base, &base);
    match total {
        Some(count) => tracing::info!(
            source_id = %source_id,
            path = %path,
            total = count,
            "land refresh: feature count"
        ),
        None => tracing::info!(
            source_id = %source_id,
            path = %path,
            "land refresh: feature count unknown"
        ),
    }

    let mut page_size: u32 = 500;
    let mut offset: u32 = 0;
    let mut features: Vec<Feature> = Vec::new();
    let mut last_logged_pct: u8 = 0;

    if verbose {
        eprintln!("  featureserver paginate: {base}");
    }

    loop {
        let url = format!(
            "{query_base}?where=1%3D1&outFields=*&f=geojson&resultRecordCount={page_size}&resultOffset={offset}&outSR=4326"
        );
        let mut last_err: Option<anyhow::Error> = None;
        let mut chunk: Option<Value> = None;
        for attempt in 0..4 {
            let client = client()?;
            match client
                .get(&url)
                .timeout(Duration::from_secs(600))
                .send()
            {
                Ok(resp) => {
                    let status = resp.status();
                    if status.as_u16() == 500 && page_size > 100 {
                        page_size = (page_size / 2).max(100);
                        if verbose {
                            eprintln!("    HTTP 500 at offset {offset}; retry page_size={page_size}");
                        }
                        thread::sleep(Duration::from_millis(500 * (attempt as u64 + 1)));
                        continue;
                    }
                    if !status.is_success() {
                        last_err = Some(anyhow::anyhow!("HTTP {status} for {url}"));
                        thread::sleep(Duration::from_millis(500 * (attempt as u64 + 1)));
                        continue;
                    }
                    match resp.bytes() {
                        Ok(bytes) => match serde_json::from_slice::<Value>(&bytes) {
                            Ok(parsed) => {
                                chunk = Some(parsed);
                                last_err = None;
                                break;
                            }
                            Err(e) => last_err = Some(e.into()),
                        },
                        Err(e) => last_err = Some(e.into()),
                    }
                }
                Err(e) => last_err = Some(e.into()),
            }
            thread::sleep(Duration::from_millis(500 * (attempt as u64 + 1)));
        }
        if let Some(err) = last_err {
            return Err(err);
        }
        let chunk = chunk.context("empty featureserver response")?;
        let batch: Vec<Feature> = chunk
            .get("features")
            .and_then(|v| serde_json::from_value(v.clone()).ok())
            .unwrap_or_default();
        if batch.is_empty() {
            break;
        }
        let batch_len = batch.len();
        features.extend(batch);
        offset += batch_len as u32;
        log_refresh_progress(source_id, path, offset, total, &mut last_logged_pct);
        if verbose {
            eprintln!("    fetched {offset} features");
        }
        if (batch_len as u32) < page_size {
            break;
        }
        thread::sleep(Duration::from_millis(150));
    }

    if last_logged_pct < 100 {
        log_refresh_progress(source_id, path, offset, total.or(Some(offset as u64)), &mut last_logged_pct);
    }

    let stem = dest
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("layer");
    write_feature_collection(source_id, path, dest, &features, stem)
}

pub fn refresh_land_source_file(
    project_dir: &Path,
    source_id: &str,
    rel_path: &str,
    download_url: &str,
    kind: LandDownloadKind,
    verbose: bool,
) -> Result<()> {
    let dest = project_dir.join(rel_path.trim().replace('\\', "/"));
    if verbose {
        eprintln!(
            "refresh {}: {:?} from {}",
            dest.file_name()
                .and_then(|s| s.to_str())
                .unwrap_or(rel_path),
            kind,
            download_url
        );
    }
    match kind {
        LandDownloadKind::Featureserver => {
            download_featureserver_geojson(source_id, rel_path, download_url, &dest, verbose)
        }
        LandDownloadKind::Geojson => {
            let data = download_bytes_with_progress(source_id, rel_path, download_url, 300)?;
            if let Some(parent) = dest.parent() {
                fs::create_dir_all(parent)?;
            }
            fs::write(&dest, &data).with_context(|| format!("write {}", dest.display()))
        }
        LandDownloadKind::Filegdb => {
            let data = download_bytes_with_progress(source_id, rel_path, download_url, 300)?;
            install_filegdb_zip(source_id, rel_path, &data, &dest)
        }
        LandDownloadKind::Manual => bail!("manual download kind cannot be refreshed automatically"),
    }
}
