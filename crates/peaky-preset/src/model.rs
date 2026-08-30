//! Preset YAML schema (serve-only slim schema).

use std::collections::{HashMap, HashSet};

use serde::{Deserialize, Serialize};
use serde_yaml::Mapping;
use thiserror::Error;

#[derive(Debug, Error)]
pub enum PresetValidationError {
    #[error("{0}")]
    Message(String),
}

pub type PresetResult<T> = Result<T, PresetValidationError>;

fn err(msg: impl Into<String>) -> PresetValidationError {
    PresetValidationError::Message(msg.into())
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum CoverageProvider {
    #[default]
    Splatter,
    Splat,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct SimulationMaxWorkers {
    pub coverage: u32,
    pub dem: u32,
}

pub const DEFAULT_COVERAGE_MAX_WORKERS: u32 = 2;
pub const DEFAULT_DEM_MAX_WORKERS: u32 = 4;

impl Default for SimulationMaxWorkers {
    fn default() -> Self {
        Self {
            coverage: DEFAULT_COVERAGE_MAX_WORKERS,
            dem: DEFAULT_DEM_MAX_WORKERS,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct SimulationConfig {
    pub provider: CoverageProvider,
    pub radius_km: serde_yaml::Value,
    pub viewshed_quality: u8,
    pub max_workers: SimulationMaxWorkers,
    pub modem: Option<serde_yaml::Value>,
    pub environment: Option<serde_yaml::Value>,
    pub transmitter: HashMap<String, serde_yaml::Value>,
    pub receiver: HashMap<String, serde_yaml::Value>,
}

impl Default for SimulationConfig {
    fn default() -> Self {
        Self {
            provider: CoverageProvider::Splatter,
            radius_km: serde_yaml::Value::Number(serde_yaml::Number::from(50)),
            viewshed_quality: 3,
            max_workers: SimulationMaxWorkers::default(),
            modem: None,
            environment: None,
            transmitter: HashMap::new(),
            receiver: HashMap::new(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ViewshedPolygonStyle {
    pub line: String,
    pub fill: String,
    #[serde(default = "default_true")]
    pub fill_polygons: bool,
    #[serde(default = "default_line_width")]
    pub line_width: f64,
}

fn default_true() -> bool {
    true
}

fn default_line_width() -> f64 {
    2.0
}

pub fn default_viewshed_polygon_style() -> ViewshedPolygonStyle {
    ViewshedPolygonStyle {
        line: "00000000".to_string(),
        fill: "9900ff00".to_string(),
        fill_polygons: true,
        line_width: 0.0,
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct DisplayConfig {
    pub colormap: String,
    pub transparency: f64,
    pub min_dbm: f64,
    pub max_dbm: f64,
    pub polygon: Option<ViewshedPolygonStyle>,
}

impl Default for DisplayConfig {
    fn default() -> Self {
        Self {
            colormap: "plasma".to_string(),
            transparency: 50.0,
            min_dbm: -130.0,
            max_dbm: -80.0,
            polygon: None,
        }
    }
}

pub fn resolved_viewshed_polygon_style(display: &DisplayConfig) -> ViewshedPolygonStyle {
    display.polygon.clone().unwrap_or_else(default_viewshed_polygon_style)
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SeekPlanHop {
    pub site: Option<String>,
    pub loc: Option<[f64; 2]>,
    pub height_m: Option<f64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SeekPlan {
    pub start: String,
    pub goal: [f64; 2],
    #[serde(default)]
    pub complete: bool,
    #[serde(default)]
    pub hops: Vec<SeekPlanHop>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct SeekConfig {
    pub peak_bin_size_m: f64,
    pub max_candidates: u32,
    pub plan: Option<SeekPlan>,
}

impl Default for SeekConfig {
    fn default() -> Self {
        Self {
            peak_bin_size_m: 1500.0,
            max_candidates: 48,
            plan: None,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SiteEntry {
    pub name: String,
    pub loc: [f64; 2],
    pub height_m: Option<f64>,
    pub description: Option<String>,
    #[serde(default)]
    pub tags: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub node: Option<String>,
}

impl SiteEntry {
    pub fn lat(&self) -> f64 {
        self.loc[0]
    }

    pub fn lon(&self) -> f64 {
        self.loc[1]
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LandLayerStyle {
    #[serde(default = "default_layer_color")]
    pub color: String,
    #[serde(default = "default_layer_opacity")]
    pub opacity: f64,
}

fn default_layer_color() -> String {
    "#4a6cf7".to_string()
}

fn default_layer_opacity() -> f64 {
    0.48
}

impl Default for LandLayerStyle {
    fn default() -> Self {
        Self {
            color: default_layer_color(),
            opacity: default_layer_opacity(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LandAttributeFilter {
    pub field: String,
    pub values: Vec<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum LandLayerRole {
    Aoi,
    Include,
    Exclude,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct LandLayerEntry {
    pub name: String,
    pub id: Option<String>,
    pub role: Option<LandLayerRole>,
    #[serde(default)]
    pub include: Vec<LandAttributeFilter>,
    #[serde(default)]
    pub exclude: Vec<LandAttributeFilter>,
    pub label_field: Option<String>,
    pub style_field: Option<String>,
    pub style: Option<LandLayerStyleValue>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(untagged)]
pub enum LandLayerStyleValue {
    Single(LandLayerStyle),
    Map(HashMap<String, LandLayerStyle>),
}

impl LandLayerEntry {
    pub fn layer_key(&self) -> String {
        if let Some(id) = self.id.as_deref() {
            let slug = slugify_files_segment(id);
            if !slug.is_empty() {
                return slug;
            }
        }
        let slug = slugify_files_segment(&self.name);
        if slug.is_empty() {
            "layer".to_string()
        } else {
            slug
        }
    }

    pub fn flat_style(&self) -> LandLayerStyle {
        match &self.style {
            Some(LandLayerStyleValue::Single(s)) => s.clone(),
            Some(LandLayerStyleValue::Map(m)) => m.values().next().cloned().unwrap_or_default(),
            None => LandLayerStyle::default(),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum LandDownloadKind {
    Featureserver,
    Geojson,
    Filegdb,
    Manual,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct LandSourceRefresh {
    /// ISO date ``YYYY-MM-DD`` when ``path`` was last fetched or confirmed.
    pub last_updated: Option<String>,
    /// Human catalog page (ArcGIS Hub explore, agency portal, etc.).
    pub source_url: Option<String>,
    /// Machine fetch endpoint (FeatureServer layer URL, hub GeoJSON export, etc.).
    pub download_url: Option<String>,
    pub download_kind: Option<LandDownloadKind>,
    /// Override ``land.refreshIntervalDays`` for this source.
    pub interval_days: Option<u32>,
}

fn default_land_source_enabled() -> bool {
    true
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LandSourceEntry {
    pub path: String,
    pub layers: Vec<LandLayerEntry>,
    pub label: Option<String>,
    /// When false, source is skipped at boot and omitted from the map.
    #[serde(default = "default_land_source_enabled")]
    pub enabled: bool,
    #[serde(default)]
    pub refresh: Option<LandSourceRefresh>,
}

impl LandSourceEntry {
    pub fn is_enabled(&self) -> bool {
        self.enabled
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LandSidebarFolder {
    pub id: String,
    pub label: String,
    #[serde(default)]
    pub sources: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct LandSidebar {
    #[serde(default)]
    pub folders: Vec<LandSidebarFolder>,
    #[serde(default)]
    pub unfiled_sources: Vec<String>,
}

fn default_land_refresh_interval_days() -> u32 {
    90
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct LandConfig {
    #[serde(default)]
    pub sources: HashMap<String, LandSourceEntry>,
    pub sidebar: Option<LandSidebar>,
    /// Default staleness window for sources with ``refresh.lastUpdated`` set.
    #[serde(default = "default_land_refresh_interval_days", rename = "refreshIntervalDays")]
    pub refresh_interval_days: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct Preset {
    #[serde(default, rename = "modem_presets")]
    pub modem_presets: HashMap<String, Mapping>,
    #[serde(default, rename = "environment_presets")]
    pub environment_presets: HashMap<String, Mapping>,
    pub simulation: SimulationConfig,
    pub display: DisplayConfig,
    pub sites: HashMap<String, SiteEntry>,
    #[serde(default)]
    pub links: Vec<[String; 2]>,
    #[serde(default)]
    pub land: LandConfig,
    #[serde(default)]
    pub seek: SeekConfig,
}

impl Default for Preset {
    fn default() -> Self {
        Self {
            modem_presets: HashMap::new(),
            environment_presets: HashMap::new(),
            simulation: SimulationConfig::default(),
            display: DisplayConfig::default(),
            sites: HashMap::new(),
            links: Vec::new(),
            land: LandConfig::default(),
            seek: SeekConfig::default(),
        }
    }
}

pub fn slugify_files_segment(site_name: &str) -> String {
    let mut out = String::new();
    let mut prev_hyphen = false;
    for ch in site_name.trim().chars() {
        if ch.is_ascii_alphanumeric() || ch == '-' || ch == '_' {
            if ch == '_' {
                if !out.is_empty() && !prev_hyphen {
                    out.push('-');
                    prev_hyphen = true;
                }
            } else if ch.is_whitespace() {
                if !out.is_empty() && !prev_hyphen {
                    out.push('-');
                    prev_hyphen = true;
                }
            } else {
                out.push(ch.to_ascii_lowercase());
                prev_hyphen = false;
            }
        }
    }
    while out.ends_with('-') {
        out.pop();
    }
    if out.is_empty() {
        "site".to_string()
    } else {
        out
    }
}

fn normalize_link_pair(item: &[String; 2], index: usize) -> PresetResult<[String; 2]> {
    let a = item[0].trim();
    let b = item[1].trim();
    if a.is_empty() || b.is_empty() {
        return Err(err(format!("links[{index}] slugs must be non-empty")));
    }
    let mut pair = [a.to_string(), b.to_string()];
    pair.sort();
    Ok(pair)
}

pub fn validate_project_preset_document(raw: &serde_yaml::Mapping) -> PresetResult<()> {
    for key in ["mesh", "suggest", "goals"] {
        if raw.contains_key(serde_yaml::Value::String(key.to_string())) {
            return Err(err(format!("top-level {key}: is removed in serve-only presets")));
        }
    }
    if let Some(sim) = raw.get(&serde_yaml::Value::String("simulation".into())) {
        if let serde_yaml::Value::Mapping(sim_map) = sim {
            if sim_map.contains_key(serde_yaml::Value::String("raster_dimension".into())) {
                return Err(err(
                    "simulation.raster_dimension is removed; use simulation.viewshed_quality (1–5)",
                ));
            }
        }
    }
    if let Some(sites) = raw.get(&serde_yaml::Value::String("sites".into())) {
        if let serde_yaml::Value::Mapping(map) = sites {
            for (slug, site) in map {
                let slug_s = slug.as_str().unwrap_or("<unknown>");
                if let serde_yaml::Value::Mapping(site_map) = site {
                    if site_map.contains_key(serde_yaml::Value::String("sees".into())) {
                        return Err(err(format!(
                            "sites.{slug_s}.sees is removed; use top-level links: [[site-a, site-b], ...]"
                        )));
                    }
                    if site_map.contains_key(serde_yaml::Value::String("type".into())) {
                        return Err(err(format!(
                            "sites.{slug_s}.type is removed; use tags"
                        )));
                    }
                    if site_map.contains_key(serde_yaml::Value::String("elevation_m".into())) {
                        return Err(err(format!(
                            "sites.{slug_s}.elevation_m is removed; terrain comes from DEM at loc"
                        )));
                    }
                }
            }
        }
    }
    Ok(())
}

pub fn validate_preset(preset: &Preset) -> PresetResult<()> {
    let radius: f64 = match &preset.simulation.radius_km {
        serde_yaml::Value::Number(n) => n.as_f64().ok_or_else(|| err("simulation.radius_km must be numeric"))?,
        serde_yaml::Value::String(s) => s.parse().map_err(|_| err("simulation.radius_km must be numeric"))?,
        _ => return Err(err("simulation.radius_km must be numeric")),
    };
    if radius > 100.0 {
        return Err(err("simulation.radius_km must be <= 100"));
    }

    crate::viewshed_quality::validate_viewshed_quality(preset.simulation.viewshed_quality)?;

    let mut seen_links: HashSet<[String; 2]> = HashSet::new();
    for pair in &preset.links {
        let [a, b] = normalize_link_pair(pair, 0)?;
        if a == b {
            return Err(err(format!("links pair [{a:?}, {b:?}] must not link a site to itself")));
        }
        if !preset.sites.contains_key(&a) {
            return Err(err(format!("links references unknown site slug {a:?}")));
        }
        if !preset.sites.contains_key(&b) {
            return Err(err(format!("links references unknown site slug {b:?}")));
        }
        if !seen_links.insert([a.clone(), b.clone()]) {
            return Err(err(format!("duplicate links pair [{a:?}, {b:?}]")));
        }
    }

    if let Some(plan) = &preset.seek.plan {
        if plan.hops.is_empty() {
            return Err(err("seek.plan.hops must contain at least one hop"));
        }
        for (idx, hop) in plan.hops.iter().enumerate() {
            if hop.site.is_some() && hop.loc.is_some() {
                return Err(err(format!(
                    "seek.plan.hops[{idx}] must have either site or loc, not both"
                )));
            }
            if hop.site.is_none() && hop.loc.is_none() {
                return Err(err(format!(
                    "seek.plan.hops[{idx}] must have site or loc"
                )));
            }
        }
        if let Some(site) = seek_plan_unknown_site(plan, &preset.sites) {
            return Err(err(format!(
                "seek.plan references unknown site {site:?}"
            )));
        }
    }

    Ok(())
}

pub fn seek_plan_unknown_site<'a>(
    plan: &'a SeekPlan,
    sites: &HashMap<String, SiteEntry>,
) -> Option<&'a str> {
    if !sites.contains_key(&plan.start) {
        return Some(plan.start.as_str());
    }
    for hop in &plan.hops {
        if let Some(site) = &hop.site {
            if !sites.contains_key(site) {
                return Some(site.as_str());
            }
        }
    }
    None
}

/// Drop a seek plan that names a site that is gone. Load must not fail for that.
pub fn drop_stale_seek_plan(preset: &mut Preset) -> bool {
    let Some(plan) = &preset.seek.plan else {
        return false;
    };
    if plan.hops.is_empty() || seek_plan_unknown_site(plan, &preset.sites).is_some() {
        preset.seek.plan = None;
        return true;
    }
    false
}

fn env_worker_override(var: &str) -> Option<u32> {
    std::env::var(var)
        .ok()
        .and_then(|v| v.parse().ok())
        .filter(|n| *n >= 1)
}

/// Concurrent site viewshed warm jobs (`peaky-serve` coverage queue).
/// Each job uses rayon row parallelism internally — keep this low.
pub fn resolved_coverage_max_workers(preset: &Preset) -> usize {
    env_worker_override("PEAKY_COVERAGE_WORKERS")
        .unwrap_or(preset.simulation.max_workers.coverage.max(1)) as usize
}

/// Concurrent Skadi HGT download workers (`DemMirror` fetch pool).
pub fn resolved_dem_fetch_max_workers(preset: &Preset) -> usize {
    env_worker_override("PEAKY_DEM_FETCH_WORKERS")
        .unwrap_or(preset.simulation.max_workers.dem.max(1))
        .min(16) as usize
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_yaml::Mapping;

    #[test]
    fn rejects_removed_raster_dimension_key() {
        let mut raw = Mapping::new();
        let mut sim = Mapping::new();
        sim.insert(
            serde_yaml::Value::from("raster_dimension"),
            serde_yaml::Value::from(500),
        );
        raw.insert(serde_yaml::Value::from("simulation"), serde_yaml::Value::Mapping(sim));
        let err = validate_project_preset_document(&raw).unwrap_err();
        assert!(err.to_string().contains("viewshed_quality"));
    }

    fn test_site(name: &str) -> SiteEntry {
        SiteEntry {
            name: name.into(),
            loc: [39.0, -119.0],
            height_m: None,
            description: None,
            tags: vec![],
            node: None,
        }
    }

    #[test]
    fn drop_stale_seek_plan_clears_unknown_hop() {
        let mut preset = Preset::default();
        preset.sites.insert("alpha".into(), test_site("Alpha"));
        preset.seek.plan = Some(SeekPlan {
            start: "alpha".into(),
            goal: [40.0, -118.0],
            complete: false,
            hops: vec![
                SeekPlanHop {
                    site: Some("alpha".into()),
                    loc: None,
                    height_m: None,
                },
                SeekPlanHop {
                    site: Some("relaya1".into()),
                    loc: None,
                    height_m: None,
                },
            ],
        });
        assert!(drop_stale_seek_plan(&mut preset));
        assert!(preset.seek.plan.is_none());
    }

    #[test]
    fn validate_preset_rejects_unknown_seek_site() {
        let mut preset = Preset::default();
        preset.sites.insert("alpha".into(), test_site("Alpha"));
        preset.seek.plan = Some(SeekPlan {
            start: "alpha".into(),
            goal: [40.0, -118.0],
            complete: false,
            hops: vec![SeekPlanHop {
                site: Some("relaya1".into()),
                loc: None,
                height_m: None,
            }],
        });
        let err = validate_preset(&preset).unwrap_err();
        assert!(err.to_string().contains("unknown site"));
    }
}
