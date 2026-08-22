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

pub fn install_filegdb_zip(data: &[u8], dest_gdb: &Path) -> Result<()> {
    if let Some(parent) = dest_gdb.parent() {
        fs::create_dir_all(parent)?;
    }
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
    let gdb = find_first_gdb(tmp.path())?;
    remove_dir_all_if_exists(dest_gdb)?;
    copy_dir_all(&gdb, dest_gdb)?;
    Ok(())
}

fn write_feature_collection(dest: &Path, features: &[Feature], name: &str) -> Result<()> {
    if let Some(parent) = dest.parent() {
        fs::create_dir_all(parent)?;
    }
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
    serde_json::to_writer_pretty(&mut file, &value).context("write geojson")?;
    file.write_all(b"\n").ok();
    Ok(())
}

pub fn download_featureserver_geojson(service_layer_url: &str, dest: &Path, verbose: bool) -> Result<()> {
    let mut base = service_layer_url.trim_end_matches('/').to_string();
    if !base.ends_with("/0") {
        base.push_str("/0");
    }
    let query_base = format!("{base}/query");
    let mut page_size: u32 = 500;
    let mut offset: u32 = 0;
    let mut features: Vec<Feature> = Vec::new();

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
        if verbose {
            eprintln!("    fetched {offset} features");
        }
        if (batch_len as u32) < page_size {
            break;
        }
        thread::sleep(Duration::from_millis(150));
    }

    let stem = dest
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("layer");
    write_feature_collection(dest, &features, stem)
}

pub fn refresh_land_source_file(
    project_dir: &Path,
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
        LandDownloadKind::Featureserver => download_featureserver_geojson(download_url, &dest, verbose),
        LandDownloadKind::Geojson => {
            let data = download_bytes(download_url, 300)?;
            if let Some(parent) = dest.parent() {
                fs::create_dir_all(parent)?;
            }
            fs::write(&dest, &data).with_context(|| format!("write {}", dest.display()))
        }
        LandDownloadKind::Filegdb => {
            let data = download_bytes(download_url, 300)?;
            install_filegdb_zip(&data, &dest)
        }
        LandDownloadKind::Manual => bail!("manual download kind cannot be refreshed automatically"),
    }
}
