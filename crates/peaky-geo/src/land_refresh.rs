//! Land source refresh metadata — staleness audit from preset YAML + on-disk mtime.

use std::path::Path;
use std::sync::Mutex;

use anyhow::{Context, Result};
use peaky_preset::{
    load_preset, patch_land_source_last_updated, LandDownloadKind, LandSourceEntry, Preset,
};
use rayon::prelude::*;
use serde::Serialize;

use crate::land_boot::{land_boot_pool, land_boot_workers};
use crate::land_cache::ensure_land_caches_for_preset;
use crate::land_fetch::refresh_land_source_file;
use crate::land_validate::validate_land_source;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum LandRefreshStatusKind {
    Ok,
    Stale,
    Missing,
    Unknown,
    FileNewer,
    Invalid,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LandSourceRefreshRow {
    pub source_id: String,
    pub path: String,
    pub status: LandRefreshStatusKind,
    pub interval_days: u32,
    pub last_updated: Option<String>,
    pub next_due: Option<String>,
    pub file_mtime: Option<String>,
    pub source_url: Option<String>,
    pub download_url: Option<String>,
    pub download_kind: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LandRefreshReport {
    pub project: String,
    pub default_interval_days: u32,
    pub rows: Vec<LandSourceRefreshRow>,
}

#[derive(Debug, Clone, Default, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LandRefreshRunSummary {
    pub refreshed: Vec<String>,
    pub skipped: Vec<String>,
    pub failed: Vec<LandRefreshFailure>,
    pub cache: Option<crate::land_cache::LandCacheWarmStats>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LandRefreshFailure {
    pub source_id: String,
    pub error: String,
}

pub fn today_utc_ymd_string() -> String {
    let (y, m, d) = today_utc_ymd();
    format_ymd(y, m, d)
}

fn parse_ymd(s: &str) -> Option<(i32, u32, u32)> {
    let s = s.trim();
    let mut parts = s.split('-');
    let y: i32 = parts.next()?.parse().ok()?;
    let m: u32 = parts.next()?.parse().ok()?;
    let d: u32 = parts.next()?.parse().ok()?;
    if parts.next().is_some() {
        return None;
    }
    if !(1..=12).contains(&m) || d == 0 || d > 31 {
        return None;
    }
    Some((y, m, d))
}

fn days_from_civil(y: i32, m: u32, d: u32) -> Option<i32> {
    if !(1..=12).contains(&m) || d == 0 || d > 31 {
        return None;
    }
    let y_adj = y - if m <= 2 { 1 } else { 0 };
    let era = (if y_adj >= 0 { y_adj } else { y_adj - 399 }) / 400;
    let yoe = y_adj - era * 400;
    let doy = (153 * (m as i32 + if m > 2 { -3 } else { 9 }) + 2) / 5 + d as i32 - 1;
    Some(yoe * 365 + yoe / 4 - yoe / 100 + doy + era * 146097)
}

fn civil_from_days(z: i32) -> (i32, u32, u32) {
    let era = (if z >= 0 { z } else { z - 146096 }) / 146097;
    let doe = z - era * 146097;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = mp + if mp < 10 { 3 } else { -9 };
    let y = y + if m <= 2 { 1 } else { 0 };
    (y, m as u32, d as u32)
}

fn format_ymd(y: i32, m: u32, d: u32) -> String {
    format!("{y:04}-{m:02}-{d:02}")
}

fn today_utc_ymd() -> (i32, u32, u32) {
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    civil_from_days((secs / 86_400) as i32)
}

fn add_days(y: i32, m: u32, d: u32, delta: u32) -> Option<String> {
    let base = days_from_civil(y, m, d)?;
    let (yy, mm, dd) = civil_from_days(base + delta as i32);
    Some(format_ymd(yy, mm, dd))
}

fn file_mtime_ymd(project_dir: &Path, rel_path: &str) -> Option<String> {
    let normalized = rel_path.trim().replace('\\', "/");
    let path = project_dir.join(&normalized);
    let meta = std::fs::metadata(&path).ok()?;
    let modified = meta.modified().ok()?;
    let secs = modified
        .duration_since(std::time::UNIX_EPOCH)
        .ok()?
        .as_secs();
    let (y, m, d) = civil_from_days((secs / 86_400) as i32);
    Some(format_ymd(y, m, d))
}

fn source_path_exists(project_dir: &Path, rel_path: &str) -> bool {
    let normalized = rel_path.trim().replace('\\', "/");
    let path = project_dir.join(&normalized);
    path.is_file() || path.is_dir()
}

fn interval_for(entry: &LandSourceEntry, preset: &Preset) -> u32 {
    entry
        .refresh
        .as_ref()
        .and_then(|r| r.interval_days)
        .unwrap_or(preset.land.refresh_interval_days)
}

fn audit_one(
    source_id: &str,
    entry: &LandSourceEntry,
    preset: &Preset,
    project_dir: &Path,
) -> LandSourceRefreshRow {
    if !entry.is_enabled() {
        return LandSourceRefreshRow {
            source_id: source_id.to_string(),
            path: entry.path.clone(),
            status: LandRefreshStatusKind::Ok,
            interval_days: interval_for(entry, preset),
            last_updated: entry.refresh.as_ref().and_then(|r| r.last_updated.clone()),
            next_due: None,
            file_mtime: None,
            source_url: entry.refresh.as_ref().and_then(|r| r.source_url.clone()),
            download_url: entry.refresh.as_ref().and_then(|r| r.download_url.clone()),
            download_kind: entry
                .refresh
                .as_ref()
                .and_then(|r| r.download_kind)
                .map(|k| format!("{k:?}").to_lowercase()),
        };
    }

    let refresh = entry.refresh.as_ref();
    let interval_days = interval_for(entry, preset);
    let last_updated = refresh.and_then(|r| r.last_updated.clone());
    let file_mtime = file_mtime_ymd(project_dir, &entry.path);
    let source_url = refresh.and_then(|r| r.source_url.clone());
    let download_url = refresh.and_then(|r| r.download_url.clone());
    let download_kind = refresh
        .and_then(|r| r.download_kind)
        .map(|k| format!("{k:?}").to_lowercase());

    let mut next_due = None;
    let status = if !source_path_exists(project_dir, &entry.path) {
        LandRefreshStatusKind::Missing
    } else if validate_land_source(project_dir, entry).is_err() {
        LandRefreshStatusKind::Invalid
    } else if let Some(ref last) = last_updated {
            if let Some((y, m, d)) = parse_ymd(last) {
            next_due = add_days(y, m, d, interval_days);
            if let Some(ref due) = next_due {
                let due_days = parse_ymd(due).and_then(|(dy, dm, dd)| days_from_civil(dy, dm, dd));
                let (cy, cm, cd) = today_utc_ymd();
                let today_days = days_from_civil(cy, cm, cd);
                if let (Some(due_ord), Some(today_ord)) = (due_days, today_days) {
                    if today_ord > due_ord {
                        LandRefreshStatusKind::Stale
                    } else if file_mtime.as_deref().is_some_and(|fm| {
                        parse_ymd(fm)
                            .and_then(|(fy, fm_m, fd)| days_from_civil(fy, fm_m, fd))
                            .zip(days_from_civil(y, m, d))
                            .is_some_and(|(fo, lo)| fo > lo)
                    }) {
                        LandRefreshStatusKind::FileNewer
                    } else {
                        LandRefreshStatusKind::Ok
                    }
                } else {
                    LandRefreshStatusKind::Unknown
                }
            } else {
                LandRefreshStatusKind::Unknown
            }
        } else {
            LandRefreshStatusKind::Unknown
        }
    } else {
        LandRefreshStatusKind::Unknown
    };

    LandSourceRefreshRow {
        source_id: source_id.to_string(),
        path: entry.path.clone(),
        status,
        interval_days,
        last_updated,
        next_due,
        file_mtime,
        source_url,
        download_url,
        download_kind,
    }
}

pub fn audit_land_refresh_for_preset(preset_path: &Path) -> Result<LandRefreshReport> {
    let preset = load_preset(preset_path).context("load preset")?;
    let project_dir = preset_path
        .parent()
        .context("preset path must have parent")?;
    let project = project_dir
        .file_name()
        .and_then(|s| s.to_str())
        .unwrap_or("project")
        .to_string();

    let mut rows: Vec<LandSourceRefreshRow> = preset
        .land
        .sources
        .iter()
        .map(|(id, entry)| audit_one(id, entry, &preset, project_dir))
        .collect();
    rows.sort_by(|a, b| a.source_id.cmp(&b.source_id));

    Ok(LandRefreshReport {
        project,
        default_interval_days: preset.land.refresh_interval_days,
        rows,
    })
}

pub fn audit_all_projects(projects_dir: &Path) -> Result<Vec<LandRefreshReport>> {
    let mut out = Vec::new();
    if !projects_dir.is_dir() {
        return Ok(out);
    }
    for entry in std::fs::read_dir(projects_dir).context("read projects dir")? {
        let entry = entry?;
        if !entry.file_type().map(|t| t.is_dir()).unwrap_or(false) {
            continue;
        }
        let config = entry.path().join("config.yaml");
        if config.is_file() {
            out.push(audit_land_refresh_for_preset(&config)?);
        }
    }
    out.sort_by(|a, b| a.project.cmp(&b.project));
    Ok(out)
}

pub fn refresh_row_for_source(
    preset: &Preset,
    source_id: &str,
    entry: &LandSourceEntry,
    project_dir: &Path,
) -> LandSourceRefreshRow {
    audit_one(source_id, entry, preset, project_dir)
}

fn refreshable(row: &LandSourceRefreshRow, entry: &LandSourceEntry, force: bool) -> bool {
    if !entry.is_enabled() {
        return false;
    }
    let refresh = entry.refresh.as_ref();
    let kind = refresh.and_then(|r| r.download_kind);
    if matches!(kind, Some(LandDownloadKind::Manual)) {
        return false;
    }
    let has_url = refresh
        .and_then(|r| r.download_url.as_ref())
        .is_some_and(|u| !u.trim().is_empty());
    if !has_url {
        return false;
    }
    if force {
        return true;
    }
    matches!(
        row.status,
        LandRefreshStatusKind::Stale
            | LandRefreshStatusKind::Missing
            | LandRefreshStatusKind::Invalid
    )
}

struct LandRefreshJob {
    source_id: String,
    path: String,
    url: String,
    kind: LandDownloadKind,
}

fn log_audit_row(row: &LandSourceRefreshRow, entry: &LandSourceEntry) {
    if !entry.is_enabled() {
        tracing::info!(
            source_id = %row.source_id,
            path = %row.path,
            "land audit: disabled"
        );
        return;
    }
    tracing::info!(
        source_id = %row.source_id,
        path = %row.path,
        status = ?row.status,
        last_updated = row.last_updated.as_deref().unwrap_or("-"),
        "land audit"
    );
}

pub fn refresh_due_land_sources(
    preset_path: &Path,
    force: bool,
    verbose: bool,
) -> Result<LandRefreshRunSummary> {
    let preset = load_preset(preset_path).context("load preset")?;
    let project_dir = preset_path
        .parent()
        .context("preset path must have parent")?;
    let report = audit_land_refresh_for_preset(preset_path)?;
    let today = today_utc_ymd_string();
    let workers = land_boot_workers();

    tracing::info!(sources = report.rows.len(), workers, "land: audit complete");

    let mut summary = LandRefreshRunSummary::default();
    let mut jobs: Vec<LandRefreshJob> = Vec::new();

    for row in &report.rows {
        let Some(entry) = preset.land.sources.get(&row.source_id) else {
            continue;
        };
        log_audit_row(row, entry);
        if !entry.is_enabled() {
            summary.skipped.push(row.source_id.clone());
            continue;
        }
        if !refreshable(row, entry, force) {
            summary.skipped.push(row.source_id.clone());
            continue;
        }
        let refresh = entry.refresh.as_ref().context("refresh metadata missing")?;
        let url = refresh.download_url.as_ref().context("download_url missing")?;
        jobs.push(LandRefreshJob {
            source_id: row.source_id.clone(),
            path: entry.path.clone(),
            url: url.clone(),
            kind: refresh.download_kind.unwrap_or(LandDownloadKind::Filegdb),
        });
    }

    if jobs.is_empty() {
        tracing::info!("land refresh: nothing due");
    } else {
        tracing::info!(count = jobs.len(), workers, "land refresh: downloading");
        let preset_path = preset_path.to_path_buf();
        let project_dir = project_dir.to_path_buf();
        let results: Vec<(String, Result<()>)> = land_boot_pool().install(|| {
            jobs.par_iter()
                .map(|job| {
                    tracing::info!(
                        source_id = %job.source_id,
                        path = %job.path,
                        kind = ?job.kind,
                        "land refresh: start"
                    );
                    let result = refresh_land_source_file(
                        &project_dir,
                        &job.source_id,
                        &job.path,
                        &job.url,
                        job.kind,
                        verbose,
                    );
                    match &result {
                        Ok(()) => tracing::info!(
                            source_id = %job.source_id,
                            path = %job.path,
                            "land refresh: done"
                        ),
                        Err(e) => tracing::warn!(
                            source_id = %job.source_id,
                            path = %job.path,
                            error = %e,
                            "land refresh: failed"
                        ),
                    }
                    (job.source_id.clone(), result)
                })
                .collect()
        });

        for (source_id, result) in results {
            match result {
                Ok(()) => {
                    tracing::info!(source_id = %source_id, "land refresh: stamping lastUpdated");
                    patch_land_source_last_updated(&preset_path, &source_id, &today)
                        .with_context(|| format!("stamp lastUpdated for {source_id}"))?;
                    summary.refreshed.push(source_id);
                }
                Err(e) => summary.failed.push(LandRefreshFailure {
                    source_id,
                    error: format!("{e:#}"),
                }),
            }
        }
    }

    tracing::info!("land: warming layer cache (ogr2ogr exports)");
    summary.cache = Some(ensure_land_caches_for_preset(preset_path, verbose)?);
    Ok(summary)
}

fn format_land_boot_wedge(failures: &[(String, String)]) -> String {
    let mut lines = vec![
        "Peaky cannot start: one or more enabled land sources are missing or invalid.".to_string(),
        String::new(),
    ];
    for (source_id, err) in failures {
        lines.push(format!("  [{source_id}] {err}"));
    }
    lines.push(String::new());
    lines.push(
        "Set `enabled: false` on the failing source(s) in land.yaml to start without them, \
         then fix the file or restore a working `refresh.downloadUrl`."
            .to_string(),
    );
    lines.join("\n")
}

/// Refresh stale/missing/invalid enabled sources, validate all enabled sources, warm land cache.
/// Fails boot when any enabled source is still invalid after refresh attempts.
pub fn prepare_land_at_boot(preset_path: &Path, verbose: bool) -> Result<LandRefreshRunSummary> {
    let preset = load_preset(preset_path).context("load preset")?;
    let project_dir = preset_path
        .parent()
        .context("preset path must have parent")?;

    let mut summary = refresh_due_land_sources(preset_path, false, verbose)?;

    let enabled: Vec<(String, LandSourceEntry)> = preset
        .land
        .sources
        .iter()
        .filter(|(_, entry)| entry.is_enabled())
        .map(|(id, entry)| (id.clone(), entry.clone()))
        .collect();
    let workers = land_boot_workers();
    tracing::info!(count = enabled.len(), workers, "land validate: checking enabled sources");

    let failures: Mutex<Vec<(String, String)>> = Mutex::new(Vec::new());
    land_boot_pool().install(|| {
        enabled.par_iter().for_each(|(source_id, entry)| {
            match validate_land_source(project_dir, entry) {
                Ok(()) => tracing::info!(
                    source_id = %source_id,
                    path = %entry.path,
                    "land validate: ok"
                ),
                Err(e) => {
                    tracing::warn!(
                        source_id = %source_id,
                        path = %entry.path,
                        error = %e,
                        "land validate: failed"
                    );
                    failures
                        .lock()
                        .expect("land validate failure list")
                        .push((source_id.clone(), e.to_string()));
                }
            }
        });
    });

    let failures = failures.into_inner().expect("land validate failure list");
    if !failures.is_empty() {
        anyhow::bail!("{}", format_land_boot_wedge(&failures));
    }

    if summary.cache.is_none() {
        summary.cache = Some(ensure_land_caches_for_preset(preset_path, verbose)?);
    }

    let cache = summary.cache.as_ref();
    tracing::info!(
        refreshed = summary.refreshed.len(),
        skipped = summary.skipped.len(),
        failed = summary.failed.len(),
        cache_exported = cache.map(|c| c.exported).unwrap_or(0),
        cache_copied = cache.map(|c| c.copied).unwrap_or(0),
        "land boot complete"
    );

    Ok(summary)
}

pub fn prepare_land_for_serve(preset_path: &Path, refresh: bool, verbose: bool) -> Result<LandRefreshRunSummary> {
    if refresh {
        prepare_land_at_boot(preset_path, verbose)
    } else {
        Ok(LandRefreshRunSummary {
            cache: Some(ensure_land_caches_for_preset(preset_path, verbose)?),
            ..LandRefreshRunSummary::default()
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn add_days_crosses_month() {
        assert_eq!(add_days(2026, 8, 21, 90), Some("2026-11-19".to_string()));
    }

    #[test]
    fn parse_ymd_rejects_garbage() {
        assert!(parse_ymd("2026-13-01").is_none());
    }
}
