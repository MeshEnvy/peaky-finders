//! Download land sources from ArcGIS FeatureServer, hub GeoJSON, or FileGDB zips.

use std::fs;
use std::io::{Cursor, Write};
use std::path::{Path, PathBuf};
use std::thread;
use std::time::Duration;

use anyhow::{bail, Context, Result};
use peaky_preset::LandDownloadKind;
use reqwest::blocking::Client;
use serde_json::Value;
use zip::ZipArchive;

const USER_AGENT: &str = "peaky-land-refresh/1";
const FEATURESERVER_PAGE_SIZE: u32 = 500;

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

fn log_page_progress(
    source_id: &str,
    path: &str,
    fetched: u32,
    page_len: u32,
    total: Option<u64>,
) {
    let pct = total.filter(|t| *t > 0).map(|t| ((fetched as u64 * 100) / t).min(100));
    tracing::info!(
        source_id = %source_id,
        path = %path,
        fetched,
        page = page_len,
        total,
        pct,
        "land refresh: progress"
    );
}

fn featureserver_part_path(dest: &Path) -> PathBuf {
    dest.with_extension("geojson.part")
}

fn featureserver_offset_path(dest: &Path) -> PathBuf {
    dest.with_extension("geojson.offset")
}

fn read_featureserver_offset(path: &Path) -> Option<u32> {
    let raw = fs::read_to_string(path).ok()?;
    raw.trim().parse().ok()
}

fn write_featureserver_offset(path: &Path, offset: u32) -> Result<()> {
    fs::write(path, format!("{offset}\n")).with_context(|| format!("write {}", path.display()))
}

struct StreamingGeoJsonWriter {
    source_id: String,
    rel_path: String,
    part_path: PathBuf,
    dest_path: PathBuf,
    offset_path: PathBuf,
    file: fs::File,
    first_feature: bool,
    feature_count: u32,
}

impl StreamingGeoJsonWriter {
    fn create(source_id: &str, rel_path: &str, dest: &Path, name: &str) -> Result<Self> {
        if let Some(parent) = dest.parent() {
            fs::create_dir_all(parent)?;
        }
        let part_path = featureserver_part_path(dest);
        let offset_path = featureserver_offset_path(dest);
        let resume_offset = if part_path.is_file() {
            read_featureserver_offset(&offset_path)
        } else {
            None
        };

        if let Some(offset) = resume_offset {
            tracing::info!(
                source_id = %source_id,
                path = %rel_path,
                offset,
                dest = %dest.display(),
                "land refresh: resume partial geojson"
            );
            let file = fs::OpenOptions::new()
                .append(true)
                .open(&part_path)
                .with_context(|| format!("append {}", part_path.display()))?;
            return Ok(Self {
                source_id: source_id.to_string(),
                rel_path: rel_path.to_string(),
                part_path,
                dest_path: dest.to_path_buf(),
                offset_path,
                file,
                first_feature: false,
                feature_count: offset,
            });
        }

        if part_path.is_file() {
            fs::remove_file(&part_path)
                .with_context(|| format!("remove stale {}", part_path.display()))?;
        }
        if offset_path.is_file() {
            fs::remove_file(&offset_path)
                .with_context(|| format!("remove stale {}", offset_path.display()))?;
        }

        tracing::info!(
            source_id = %source_id,
            path = %rel_path,
            dest = %dest.display(),
            "land refresh: writing geojson"
        );
        let mut file =
            fs::File::create(&part_path).with_context(|| format!("create {}", part_path.display()))?;
        let name_json = serde_json::to_string(name).context("serialize geojson name")?;
        write!(
            file,
            r#"{{"type":"FeatureCollection","name":{name_json},"features":["#
        )
        .context("write geojson prefix")?;
        file.flush().ok();

        Ok(Self {
            source_id: source_id.to_string(),
            rel_path: rel_path.to_string(),
            part_path,
            dest_path: dest.to_path_buf(),
            offset_path,
            file,
            first_feature: true,
            feature_count: 0,
        })
    }

    fn append_page(&mut self, features: &[Value]) -> Result<()> {
        for feature in features {
            if !self.first_feature {
                write!(self.file, ",").context("write geojson feature separator")?;
            }
            serde_json::to_writer(&mut self.file, feature).context("write geojson feature")?;
            self.first_feature = false;
            self.feature_count += 1;
        }
        self.file.flush().context("flush geojson part")?;
        write_featureserver_offset(&self.offset_path, self.feature_count)?;
        Ok(())
    }

    fn finish(mut self) -> Result<u32> {
        write!(self.file, "]}}\n").context("write geojson footer")?;
        self.file
            .sync_all()
            .with_context(|| format!("sync {}", self.part_path.display()))?;
        drop(self.file);
        if self.dest_path.is_file() {
            fs::remove_file(&self.dest_path)
                .with_context(|| format!("replace {}", self.dest_path.display()))?;
        }
        fs::rename(&self.part_path, &self.dest_path)
            .with_context(|| format!("rename {} -> {}", self.part_path.display(), self.dest_path.display()))?;
        if self.offset_path.is_file() {
            fs::remove_file(&self.offset_path).ok();
        }
        let size = fs::metadata(&self.dest_path)
            .with_context(|| format!("stat {}", self.dest_path.display()))?
            .len();
        tracing::info!(
            source_id = %self.source_id,
            path = %self.rel_path,
            features = self.feature_count,
            size = %format_bytes(size),
            "land refresh: write done"
        );
        Ok(self.feature_count)
    }
}

fn featureserver_total_features(client: &Client, query_base: &str, layer_base: &str) -> Option<u64> {
    let http = client;
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

fn featureserver_layer_base(service_layer_url: &str) -> String {
    service_layer_url.trim_end_matches('/').to_string()
}

fn fetch_featureserver_page(
    client: &Client,
    query_base: &str,
    url_suffix: &str,
    page_size: u32,
    offset: u32,
    verbose: bool,
) -> Result<(Vec<Value>, u32)> {
    let url = format!(
        "{query_base}?{url_suffix}&resultRecordCount={page_size}&resultOffset={offset}&outSR=4326"
    );
    let mut last_err: Option<anyhow::Error> = None;
    let mut page_size = page_size;
    for attempt in 0..4 {
        let full_url = format!(
            "{query_base}?{url_suffix}&resultRecordCount={page_size}&resultOffset={offset}&outSR=4326"
        );
        match client
            .get(&full_url)
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
                    last_err = Some(anyhow::anyhow!("HTTP {status} for {full_url}"));
                    thread::sleep(Duration::from_millis(500 * (attempt as u64 + 1)));
                    continue;
                }
                let bytes = resp.bytes().context("read featureserver body")?;
                let chunk: Value =
                    serde_json::from_slice(&bytes).context("parse featureserver JSON")?;
                let batch: Vec<Value> = chunk
                    .get("features")
                    .and_then(|v| v.as_array())
                    .map(|arr| arr.to_vec())
                    .unwrap_or_default();
                return Ok((batch, page_size));
            }
            Err(e) => last_err = Some(e.into()),
        }
        thread::sleep(Duration::from_millis(500 * (attempt as u64 + 1)));
    }
    Err(last_err.unwrap_or_else(|| anyhow::anyhow!("featureserver fetch failed for {url}")))
}

fn paginate_featureserver_to_path(
    source_id: &str,
    path: &str,
    query_base: &str,
    layer_base: &str,
    url_suffix: &str,
    dest: &Path,
    name: &str,
    verbose: bool,
) -> Result<u32> {
    let client = client()?;
    let total = featureserver_total_features(&client, query_base, layer_base);
    match total {
        Some(count) => tracing::info!(
            source_id = %source_id,
            path = %path,
            total = count,
            "gtlf fetch: feature count"
        ),
        None => tracing::info!(
            source_id = %source_id,
            path = %path,
            "gtlf fetch: feature count unknown"
        ),
    }

    let mut writer = StreamingGeoJsonWriter::create(source_id, path, dest, name)?;
    let mut page_size: u32 = FEATURESERVER_PAGE_SIZE;
    let mut offset = writer.feature_count;

    loop {
        let (batch, used_page) = fetch_featureserver_page(
            &client,
            query_base,
            url_suffix,
            page_size,
            offset,
            verbose,
        )?;
        page_size = used_page;
        if batch.is_empty() {
            break;
        }
        let batch_len = batch.len() as u32;
        writer.append_page(&batch)?;
        offset += batch_len;
        log_page_progress(source_id, path, offset, batch_len, total);
        if verbose {
            eprintln!("    fetched {offset} features");
        }
        if batch_len < page_size {
            break;
        }
        thread::sleep(Duration::from_millis(150));
    }

    writer.finish()
}

/// Bbox-clipped FeatureServer export (WGS-84 envelope).
pub fn download_featureserver_geojson_bbox(
    source_id: &str,
    path: &str,
    service_layer_url: &str,
    west: f64,
    south: f64,
    east: f64,
    north: f64,
    dest: &Path,
    verbose: bool,
) -> Result<()> {
    let base = featureserver_layer_base(service_layer_url);
    let query_base = format!("{base}/query");
    let geometry = serde_json::json!({
        "xmin": west,
        "ymin": south,
        "xmax": east,
        "ymax": north,
        "spatialReference": { "wkid": 4326 }
    });
    let geometry_json = geometry.to_string();
    let geom_enc = urlencoding::encode(&geometry_json);
    let url_suffix = format!(
        "geometry={geom_enc}&geometryType=esriGeometryEnvelope&inSR=4326&spatialRel=esriSpatialRelIntersects&where=1%3D1&outFields=*&f=geojson"
    );
    if verbose {
        eprintln!("  featureserver bbox: {base} [{west},{south},{east},{north}]");
    }
    let stem = dest
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("layer");
    paginate_featureserver_to_path(
        source_id,
        path,
        &query_base,
        &base,
        &url_suffix,
        dest,
        stem,
        verbose,
    )?;
    Ok(())
}

pub fn download_featureserver_geojson(
    source_id: &str,
    path: &str,
    service_layer_url: &str,
    dest: &Path,
    verbose: bool,
) -> Result<()> {
    let base = featureserver_layer_base(service_layer_url);
    let query_base = format!("{base}/query");
    if verbose {
        eprintln!("  featureserver paginate: {base}");
    }
    let stem = dest
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("layer");
    paginate_featureserver_to_path(
        source_id,
        path,
        &query_base,
        &base,
        "where=1%3D1&outFields=*&f=geojson",
        dest,
        stem,
        verbose,
    )?;
    Ok(())
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

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use tempfile::TempDir;

    #[test]
    fn streaming_geojson_writer_roundtrip() -> Result<()> {
        let dir = TempDir::new()?;
        let dest = dir.path().join("layer.geojson");
        let mut writer =
            StreamingGeoJsonWriter::create("test", "layer.geojson", &dest, "layer")?;
        writer.append_page(&[
            json!({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-117.0, 39.0]},
                "properties": {"id": 1}
            }),
            json!({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-117.1, 39.1]},
                "properties": {"id": 2}
            }),
        ])?;
        assert_eq!(writer.finish()?, 2);

        let parsed: Value = serde_json::from_slice(&fs::read(&dest)?)?;
        let features = parsed
            .get("features")
            .and_then(|v| v.as_array())
            .expect("features array");
        assert_eq!(features.len(), 2);
        assert_eq!(parsed.get("name").and_then(|v| v.as_str()), Some("layer"));
        Ok(())
    }

    #[test]
    fn streaming_geojson_writer_resumes_from_offset() -> Result<()> {
        let dir = TempDir::new()?;
        let dest = dir.path().join("layer.geojson");
        let part = featureserver_part_path(&dest);
        let offset = featureserver_offset_path(&dest);

        fs::write(
            &part,
            r#"{"type":"FeatureCollection","name":"layer","features":[{"type":"Feature","geometry":{"type":"Point","coordinates":[-117.0,39.0]},"properties":{"id":1}}"#,
        )?;
        write_featureserver_offset(&offset, 1)?;

        let mut writer =
            StreamingGeoJsonWriter::create("test", "layer.geojson", &dest, "layer")?;
        assert_eq!(writer.feature_count, 1);
        writer.append_page(&[json!({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [-117.2, 39.2]},
            "properties": {"id": 2}
        })])?;
        assert_eq!(writer.finish()?, 2);
        Ok(())
    }
}
