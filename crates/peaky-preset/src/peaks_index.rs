//! Derived peaks catalog under ``.peaky/cache/peaks/<prefs_key>/index.json``.

use std::collections::{HashMap, HashSet};
use std::fs;
use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};

use crate::compute_key::{probe_key, probe_prefs_key, ACCESS_ALGO_VERSION, PEAK_ALGO_VERSION};
use crate::difficulty::access_difficulty_from_stored;
use crate::{classify_hike, classify_jeep};
use crate::io::load_preset;
use crate::model::{AccessMeta, AccessProbe, PeakAccessRules, PeaksDifficultyConfig};
use crate::project::ProjectLayout;
use crate::yaml_io::{read_preset_document, write_preset_value};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum PeakKind {
    Peak,
    Site,
}

impl Default for PeakKind {
    fn default() -> Self {
        Self::Peak
    }
}

/// One row in the derived eligible catalog (``index.json``).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct PeakIndexRow {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,
    pub loc: [f64; 2],
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub elev_m: Option<f64>,
    pub source: String,
    #[serde(default)]
    pub kind: PeakKind,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub point_key: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub road_m: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub hike_m: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub jeep_m: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub trail_m: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub paved_loc: Option<[f64; 2]>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub road_loc: Option<[f64; 2]>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub hike_difficulty: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub jeep_difficulty: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub access_difficulty: Option<String>,
    #[serde(default = "default_true")]
    pub eligible: bool,
}

fn default_true() -> bool {
    true
}

impl PeakIndexRow {
    pub fn lat(&self) -> f64 {
        self.loc[0]
    }

    pub fn lon(&self) -> f64 {
        self.loc[1]
    }
}

/// Back-compat alias while downstream crates migrate imports.
pub type PeakCatalogEntry = PeakIndexRow;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PeaksManifest {
    pub generated_at: String,
    pub peak_algo_version: u32,
    pub access_algo_version: u32,
    pub prefs_key: String,
    pub pbf_fingerprint: String,
    pub gtlf_fingerprint: String,
    pub land_digest: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub scanned_bbox: Option<[f64; 4]>,
    pub rules: PeakAccessRules,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PeaksIndex {
    pub manifest: PeaksManifest,
    pub peaks: HashMap<String, PeakIndexRow>,
}

impl Default for PeaksIndex {
    fn default() -> Self {
        Self::default_empty("")
    }
}

/// Back-compat alias.
pub type PeaksCatalog = PeaksIndex;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PeakOverride {
    pub loc: [f64; 2],
    pub deny: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub slug: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub note: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct PeakOverridesFile {
    #[serde(default)]
    pub overrides: Vec<PeakOverride>,
}

pub fn load_peaks_index(project_dir: &Path, prefs_key: &str) -> Result<PeaksIndex> {
    let path = ProjectLayout {
        project_dir: project_dir.to_path_buf(),
        config_path: project_dir.join("config.yaml"),
    }
    .peaks_index_path(prefs_key);
    if !path.is_file() {
        return Ok(PeaksIndex::default_empty(prefs_key));
    }
    let bytes = fs::read(&path).with_context(|| format!("read peaks index {}", path.display()))?;
    serde_json::from_slice(&bytes).with_context(|| format!("parse peaks index {}", path.display()))
}

impl PeaksIndex {
    fn default_empty(prefs_key: &str) -> Self {
        Self {
            manifest: PeaksManifest {
                generated_at: String::new(),
                peak_algo_version: PEAK_ALGO_VERSION,
                access_algo_version: ACCESS_ALGO_VERSION,
                prefs_key: prefs_key.to_string(),
                pbf_fingerprint: String::new(),
                gtlf_fingerprint: String::new(),
                land_digest: String::new(),
                scanned_bbox: None,
                rules: PeakAccessRules::default(),
            },
            peaks: HashMap::new(),
        }
    }
}

pub fn write_peaks_index(project_dir: &Path, prefs_key: &str, index: &PeaksIndex) -> Result<()> {
    let layout = ProjectLayout {
        project_dir: project_dir.to_path_buf(),
        config_path: project_dir.join("config.yaml"),
    };
    let path = layout.peaks_index_path(prefs_key);
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .with_context(|| format!("create peaks index dir {}", parent.display()))?;
    }
    let tmp = path.with_extension("json.tmp");
    fs::write(
        &tmp,
        serde_json::to_vec(index).context("serialize peaks index")?,
    )
    .with_context(|| format!("write peaks index {}", tmp.display()))?;
    fs::rename(&tmp, &path).with_context(|| format!("rename peaks index {}", path.display()))?;
    Ok(())
}

pub fn load_peak_overrides(config_path: &Path) -> Result<PeakOverridesFile> {
    let layout = ProjectLayout::from_config_path(config_path)?;
    let path = layout.peak_overrides_path();
    if !path.is_file() {
        return Ok(PeakOverridesFile::default());
    }
    let doc = read_preset_document(&path)?;
    let value = serde_yaml::Value::Mapping(doc);
    serde_yaml::from_value(value).context("parse peaks.yaml overrides")
}

pub fn write_peak_overrides(config_path: &Path, file: &PeakOverridesFile) -> Result<()> {
    let layout = ProjectLayout::from_config_path(config_path)?;
    let path = layout.peak_overrides_path();
    write_preset_value(&path, serde_yaml::to_value(file).context("serialize peaks.yaml")?)
}

/// Resolve prefs key from project config + routing fingerprints.
pub fn prefs_key_for_project(
    config_path: &Path,
    gtlf_fingerprint: &str,
    pbf_fingerprint: &str,
) -> Result<String> {
    let preset = load_preset(config_path)?;
    let meta = preset.resolved_access_meta();
    Ok(probe_prefs_key(
        &meta,
        &preset.scan.peaks,
        gtlf_fingerprint,
        pbf_fingerprint,
    ))
}

pub fn index_row_from_probe(
    name: Option<String>,
    loc: [f64; 2],
    elev_m: Option<f64>,
    source: String,
    kind: PeakKind,
    probe: &AccessProbe,
    difficulty: &PeaksDifficultyConfig,
    meta: &AccessMeta,
    scan: &crate::model::PeaksScanConfig,
    gtlf_fingerprint: &str,
    pbf_fingerprint: &str,
    road_m: Option<f64>,
) -> PeakIndexRow {
    let rec = &probe.recommended;
    let hike_difficulty = rec.hike_difficulty.clone().or_else(|| {
        rec.hike.as_ref().map(|h| {
            classify_hike(
                h.max_grade_pct,
                h.avg_grade_pct,
                h.gain_m,
                &difficulty.hike,
            )
            .to_string()
        })
    });
    let jeep_difficulty = rec.jeep_difficulty.clone().or_else(|| {
        rec.jeep
            .as_ref()
            .map(|j| classify_jeep(&j.segments, &difficulty.jeep).to_string())
    });
    let access_difficulty = rec
        .access_difficulty
        .clone()
        .or_else(|| {
            access_difficulty_from_stored(
                hike_difficulty.as_deref(),
                jeep_difficulty.as_deref(),
            )
        });
    PeakIndexRow {
        name,
        loc,
        elev_m,
        source,
        kind,
        point_key: probe.probe_key.clone().or_else(|| {
            Some(probe_key(
                loc[0],
                loc[1],
                meta,
                scan,
                gtlf_fingerprint,
                pbf_fingerprint,
            ))
        }),
        road_m,
        hike_m: rec.hike_m,
        jeep_m: rec.jeep_m,
        trail_m: rec.trail_m,
        paved_loc: rec.paved_loc,
        road_loc: rec.road_loc,
        hike_difficulty,
        jeep_difficulty,
        access_difficulty,
        eligible: probe.eligible,
    }
}

pub fn build_peaks_list_json(index: &PeaksIndex) -> serde_json::Value {
    let mut peaks: Vec<serde_json::Value> = index
        .peaks
        .iter()
        .filter(|(_, row)| row.eligible)
        .map(|(slug, row)| {
            serde_json::json!({
                "slug": slug,
                "name": row.name,
                "lat": row.lat(),
                "lon": row.lon(),
                "elev_m": row.elev_m,
                "source": row.source,
                "kind": row.kind,
                "road_m": row.road_m,
                "road_lat": row.road_loc.map(|loc| loc[0]),
                "road_lon": row.road_loc.map(|loc| loc[1]),
                "hike_m": row.hike_m,
                "jeep_m": row.jeep_m,
                "trail_m": row.trail_m,
                "paved_lat": row.paved_loc.map(|loc| loc[0]),
                "paved_lon": row.paved_loc.map(|loc| loc[1]),
                "hike_difficulty": row.hike_difficulty,
                "jeep_difficulty": row.jeep_difficulty,
                "access_difficulty": row.access_difficulty,
                "point_key": row.point_key,
            })
        })
        .collect();
    peaks.sort_by(|a, b| {
        a.get("slug")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .cmp(b.get("slug").and_then(|v| v.as_str()).unwrap_or(""))
    });
    serde_json::json!({
        "generated_at": index.manifest.generated_at,
        "rules": index.manifest.rules,
        "prefs_key": index.manifest.prefs_key,
        "peaks": peaks,
    })
}

pub fn load_latest_peaks_index(project_dir: &Path) -> Result<Option<(String, PeaksIndex)>> {
    let peaks_root = project_dir.join(".peaky/cache/peaks");
    if !peaks_root.is_dir() {
        return Ok(None);
    }
    let mut best: Option<(String, u64, PeaksIndex)> = None;
    for entry in fs::read_dir(&peaks_root)? {
        let entry = entry?;
        if !entry.file_type()?.is_dir() {
            continue;
        }
        let prefs_key = entry
            .file_name()
            .to_str()
            .unwrap_or_default()
            .to_string();
        let index_path = entry.path().join("index.json");
        if !index_path.is_file() {
            continue;
        }
        let mtime = fs::metadata(&index_path)
            .ok()
            .and_then(|m| m.modified().ok())
            .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
            .map(|d| d.as_secs())
            .unwrap_or(0);
        let index = load_peaks_index(project_dir, &prefs_key)?;
        if best.as_ref().map(|(_, m, _)| mtime > *m).unwrap_or(true) {
            best = Some((prefs_key, mtime, index));
        }
    }
    Ok(best.map(|(k, _, i)| (k, i)))
}

pub fn place_slugs(config_path: &Path) -> Result<HashSet<String>> {
    let preset = load_preset(config_path)?;
    let layout = ProjectLayout::from_config_path(config_path)?;
    let mut slugs = HashSet::new();
    if let Some((_, index)) = load_latest_peaks_index(&layout.project_dir)? {
        slugs.extend(index.peaks.keys().cloned());
    }
    slugs.extend(preset.sites.keys().cloned());
    Ok(slugs)
}

pub fn near_denied(overrides: &[PeakOverride], lat: f64, lon: f64, max_m: f64) -> bool {
    overrides.iter().any(|o| {
        o.deny && haversine_m(lat, lon, o.loc[0], o.loc[1]) <= max_m
    })
}

fn haversine_m(lat1: f64, lon1: f64, lat2: f64, lon2: f64) -> f64 {
    const R: f64 = 6_371_000.0;
    let d_lat = (lat2 - lat1).to_radians();
    let d_lon = (lon2 - lon1).to_radians();
    let a = (d_lat / 2.0).sin().powi(2)
        + lat1.to_radians().cos() * lat2.to_radians().cos() * (d_lon / 2.0).sin().powi(2);
    2.0 * R * a.sqrt().asin()
}

#[derive(Debug, Clone)]
pub struct StaleCacheDir {
    pub prefs_key: String,
    pub probe_files: usize,
    pub peaks_index: bool,
    pub bytes: u64,
}

pub fn list_stale_cache_dirs(
    project_dir: &Path,
    current_prefs_key: &str,
    keep: usize,
) -> Result<Vec<StaleCacheDir>> {
    let probe_root = project_dir.join(".peaky/cache/probe");
    let peaks_root = project_dir.join(".peaky/cache/peaks");
    let mut keys: HashSet<String> = HashSet::new();
    for root in [&probe_root, &peaks_root] {
        if root.is_dir() {
            for entry in fs::read_dir(root)? {
                let entry = entry?;
                if entry.file_type()?.is_dir() {
                    if let Some(name) = entry.file_name().to_str() {
                        keys.insert(name.to_string());
                    }
                }
            }
        }
    }
    let mut ranked: Vec<(String, u64)> = keys
        .into_iter()
        .filter(|k| k != current_prefs_key)
        .map(|k| (k.clone(), cache_dir_mtime(project_dir, &k)))
        .collect();
    ranked.sort_by(|a, b| b.1.cmp(&a.1));
    let drop_count = ranked.len().saturating_sub(keep.saturating_sub(1));
    let stale: Vec<StaleCacheDir> = ranked
        .into_iter()
        .take(drop_count)
        .map(|(prefs_key, _)| describe_cache_dir(project_dir, &prefs_key))
        .collect::<Result<Vec<_>>>()?;
    Ok(stale)
}

fn cache_dir_mtime(project_dir: &Path, prefs_key: &str) -> u64 {
    let probe = project_dir.join(".peaky/cache/probe").join(prefs_key);
    let peaks = project_dir.join(".peaky/cache/peaks").join(prefs_key);
    [probe, peaks]
        .iter()
        .filter_map(|p| {
            fs::metadata(p)
                .ok()?
                .modified()
                .ok()?
                .duration_since(std::time::UNIX_EPOCH)
                .ok()
                .map(|d| d.as_secs())
        })
        .max()
        .unwrap_or(0)
}

fn describe_cache_dir(project_dir: &Path, prefs_key: &str) -> Result<StaleCacheDir> {
    let probe_dir = project_dir.join(".peaky/cache/probe").join(prefs_key);
    let mut probe_files = 0usize;
    let mut bytes = 0u64;
    if probe_dir.is_dir() {
        for entry in fs::read_dir(&probe_dir)? {
            let entry = entry?;
            if entry.file_type()?.is_file() {
                probe_files += 1;
                bytes += entry.metadata()?.len();
            }
        }
    }
    let peaks_index = project_dir
        .join(".peaky/cache/peaks")
        .join(prefs_key)
        .join("index.json")
        .is_file();
    if peaks_index {
        bytes += fs::metadata(
            project_dir
                .join(".peaky/cache/peaks")
                .join(prefs_key)
                .join("index.json"),
        )
        .map(|m| m.len())
        .unwrap_or(0);
    }
    Ok(StaleCacheDir {
        prefs_key: prefs_key.to_string(),
        probe_files,
        peaks_index,
        bytes,
    })
}

pub fn gc_stale_cache_dirs(
    project_dir: &Path,
    current_prefs_key: &str,
    keep: usize,
    dry_run: bool,
) -> Result<Vec<StaleCacheDir>> {
    let stale = list_stale_cache_dirs(project_dir, current_prefs_key, keep)?;
    if dry_run {
        return Ok(stale);
    }
    for dir in &stale {
        let probe = project_dir.join(".peaky/cache/probe").join(&dir.prefs_key);
        let peaks = project_dir.join(".peaky/cache/peaks").join(&dir.prefs_key);
        if probe.is_dir() {
            fs::remove_dir_all(&probe).with_context(|| format!("remove {}", probe.display()))?;
        }
        if peaks.is_dir() {
            fs::remove_dir_all(&peaks).with_context(|| format!("remove {}", peaks.display()))?;
        }
    }
    Ok(stale)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::{AccessRoute, AccessProbe, RouteBudgetReport};

    #[test]
    fn peaks_index_roundtrip() {
        let dir = tempfile::tempdir().unwrap();
        fs::write(dir.path().join("config.yaml"), "simulation: {}\nscan: {}\n").unwrap();
        let prefs = "abc123";
        let mut index = PeaksIndex::default_empty(prefs);
        index.manifest.generated_at = "2026-01-01".into();
        index.peaks.insert(
            "test-peak".into(),
            PeakIndexRow {
                name: Some("Test".into()),
                loc: [39.0, -117.0],
                elev_m: Some(2000.0),
                source: "dem".into(),
                kind: PeakKind::Peak,
                point_key: Some("pk".into()),
                road_m: None,
                hike_m: None,
                jeep_m: None,
                trail_m: None,
                paved_loc: None,
                road_loc: None,
                hike_difficulty: None,
                jeep_difficulty: None,
                access_difficulty: None,
                eligible: true,
            },
        );
        write_peaks_index(dir.path(), prefs, &index).unwrap();
        let loaded = load_peaks_index(dir.path(), prefs).unwrap();
        assert_eq!(loaded.peaks.len(), 1);
        assert_eq!(loaded.manifest.prefs_key, prefs);
    }

    #[test]
    fn overrides_parse() {
        let dir = tempfile::tempdir().unwrap();
        fs::write(
            dir.path().join("config.yaml"),
            "simulation: {}\nscan: {}\n",
        )
        .unwrap();
        fs::write(
            dir.path().join("peaks.yaml"),
            "overrides:\n  - loc: [39.0, -117.0]\n    deny: true\n    note: test\n",
        )
        .unwrap();
        let loaded = load_peak_overrides(&dir.path().join("config.yaml")).unwrap();
        assert_eq!(loaded.overrides.len(), 1);
        assert!(loaded.overrides[0].deny);
    }

    #[test]
    fn index_row_from_probe_eligible() {
        let preset = load_preset(Path::new("/dev/null")).unwrap_or_default();
        let meta = preset.resolved_access_meta();
        let scan = preset.scan.peaks.clone();
        let probe = AccessProbe {
            probe_key: Some("k".into()),
            eligible: true,
            recommended: AccessRoute {
                hike_m: Some(100.0),
                jeep_m: Some(500.0),
                hike_difficulty: Some("easy".into()),
                jeep_difficulty: Some("medium".into()),
                ..AccessRoute::default()
            },
            budget_route: None,
            site_options: None,
            site_options_rev: 0,
        };
        let row = index_row_from_probe(
            Some("Peak".into()),
            [39.0, -117.0],
            Some(2500.0),
            "dem".into(),
            PeakKind::Peak,
            &probe,
            &scan.difficulty,
            &meta,
            &scan,
            "gtlf",
            "pbf",
            None,
        );
        assert!(row.eligible);
        assert_eq!(row.hike_m, Some(100.0));
    }
}
