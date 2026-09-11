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

/// Access rules stamped into ``peaks/_meta.yaml`` when ``peaky peaks`` runs.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct PeakAccessRules {
    pub max_hike_m: f64,
    /// Max segment grade on road-to-summit profile (percent; rise/run × 100).
    #[serde(default = "default_max_slope_grade_pct")]
    pub max_slope_grade_pct: f64,
    pub max_slope_deg: f64,
    #[serde(default = "default_jeep_highways")]
    pub road_highways: Vec<String>,
    /// Max horizontal jeep route from paved road to park point (20 mi default).
    #[serde(default = "default_max_jeep_m")]
    pub max_jeep_m: f64,
    #[serde(default = "default_paved_highways")]
    pub paved_highways: Vec<String>,
}

fn default_max_jeep_m() -> f64 {
    DEFAULT_MAX_JEEP_M
}

fn default_max_slope_grade_pct() -> f64 {
    30.0
}

fn default_max_slope_deg() -> f64 {
    (default_max_slope_grade_pct() / 100.0).atan().to_degrees()
}

fn default_jeep_highways() -> Vec<String> {
    vec![
        "track".into(),
        "unclassified".into(),
        "service".into(),
        "residential".into(),
        "tertiary".into(),
    ]
}

fn default_paved_highways() -> Vec<String> {
    vec![
        "primary".into(),
        "secondary".into(),
        "tertiary".into(),
        "trunk".into(),
        "motorway".into(),
        "unclassified".into(),
        "residential".into(),
    ]
}

pub const DEFAULT_MAX_JEEP_M: f64 = 32_187.0;

/// Default place/site road search radius (meters) when ``access/_meta.yaml`` is absent.
pub const DEFAULT_PLACE_ROAD_SEARCH_M: f64 = 20_000.0;

/// Default DEM sample spacing along hike/jeep polylines (meters).
pub const DEFAULT_ACCESS_PROFILE_SAMPLE_M: f64 = 30.0;

/// Default OSM jeep-road sample spacing when indexing the PBF (meters).
pub const DEFAULT_OSM_ROAD_SAMPLE_STEP_M: f64 = 40.0;

impl Default for PeakAccessRules {
    fn default() -> Self {
        Self {
            max_hike_m: 805.0,
            max_slope_grade_pct: default_max_slope_grade_pct(),
            max_slope_deg: default_max_slope_deg(),
            road_highways: default_jeep_highways(),
            max_jeep_m: DEFAULT_MAX_JEEP_M,
            paved_highways: default_paved_highways(),
        }
    }
}

/// Bump when hike/jeep routing or DEM profile semantics change (forces recompute).
/// Written into ``access/_meta.yaml`` as ``algo_version``.
pub const ACCESS_ALGO_VERSION: u32 = 1;

/// Bump when peak eligibility / summit snap / universe filter semantics change.
pub const PEAK_ALGO_VERSION: u32 = 1;

/// Pathfinding settings in ``access/_meta.yaml`` (jeep/hike routing + algo stamp).
///
/// Peak eligibility gates (``max_hike_m``, slope) stay in ``peaks/_meta.yaml``.
/// Changing these fields or ``algo_version`` invalidates ``compute_key`` on access files.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct AccessMeta {
    /// Bump when hike/jeep routing or DEM profile semantics change (forces recompute).
    pub algo_version: u32,
    pub profile_sample_m: f64,
    pub road_sample_step_m: f64,
    pub max_jeep_m: f64,
    pub road_highways: Vec<String>,
    pub paved_highways: Vec<String>,
    /// How far site/place warm searches for a jeep-class park point.
    pub place_road_search_m: f64,
}

impl Default for AccessMeta {
    fn default() -> Self {
        Self {
            algo_version: ACCESS_ALGO_VERSION,
            profile_sample_m: DEFAULT_ACCESS_PROFILE_SAMPLE_M,
            road_sample_step_m: DEFAULT_OSM_ROAD_SAMPLE_STEP_M,
            max_jeep_m: DEFAULT_MAX_JEEP_M,
            road_highways: default_jeep_highways(),
            paved_highways: default_paved_highways(),
            place_road_search_m: DEFAULT_PLACE_ROAD_SEARCH_M,
        }
    }
}

impl AccessMeta {
    /// Copy jeep/highway knobs into peak eligibility rules (stamp / routing share).
    pub fn apply_to_peak_rules(&self, rules: &mut PeakAccessRules) {
        rules.max_jeep_m = self.max_jeep_m;
        rules.road_highways = self.road_highways.clone();
        rules.paved_highways = self.paved_highways.clone();
    }
}

/// Precomputed road-to-summit hike profile (stored in ``access/<slug>.yaml``).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct PeakHikeProfilePoint {
    pub dist_m: f64,
    pub elev_m: f64,
    pub lat: f64,
    pub lon: f64,
}

/// Distance-weighted grade bucket for hike histogram.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct PeakHikeGradeBucket {
    pub label: String,
    pub min_grade_pct: f64,
    pub max_grade_pct: f64,
    pub dist_m: f64,
    pub pct_of_route: f64,
}

/// Full hike stats + path, frozen at catalog build time.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct PeakHikeProfile {
    pub hike_m_3d: f64,
    pub horiz_m: f64,
    pub gain_m: f64,
    pub loss_m: f64,
    pub max_slope_deg: f64,
    pub max_grade_pct: f64,
    pub avg_grade_pct: f64,
    /// ``easy`` | ``medium`` | ``difficult`` | ``extreme``
    pub difficulty: String,
    pub profile: Vec<PeakHikeProfilePoint>,
    pub histogram: Vec<PeakHikeGradeBucket>,
}

/// One point on a jeep access profile (paved → park).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct PeakJeepProfilePoint {
    pub dist_m: f64,
    pub elev_m: f64,
    pub lat: f64,
    pub lon: f64,
}

/// Distance along jeep route colored by OSM road class.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct PeakJeepRoadSegment {
    pub highway: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub tracktype: Option<String>,
    pub dist_m: f64,
}

/// Full jeep stats + path, frozen at catalog build time.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct PeakJeepProfile {
    pub jeep_m_3d: f64,
    pub horiz_m: f64,
    pub gain_m: f64,
    pub loss_m: f64,
    pub max_slope_deg: f64,
    pub max_grade_pct: f64,
    pub avg_grade_pct: f64,
    pub difficulty: String,
    pub profile: Vec<PeakJeepProfilePoint>,
    pub histogram: Vec<PeakHikeGradeBucket>,
    pub segments: Vec<PeakJeepRoadSegment>,
}

/// Shared paved→park→pad access for a place slug (`access/<slug>.yaml`).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Default)]
pub struct PlaceAccess {
    /// Fingerprint of algo version + settings that produced this access.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub compute_key: Option<String>,
    /// Paved-road anchor where jeep route starts `[lat, lon]`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub paved_loc: Option<[f64; 2]>,
    /// Park / leave-vehicle point `[lat, lon]`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub road_loc: Option<[f64; 2]>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub jeep_m: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub jeep: Option<PeakJeepProfile>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub hike_m: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub hike: Option<PeakHikeProfile>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub max_slope_deg: Option<f64>,
}

/// One row in the eligible-peaks catalog (`peaks/<slug>.yaml`).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PeakCatalogEntry {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,
    pub loc: [f64; 2],
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub elev_m: Option<f64>,
    pub source: String,
    /// Fingerprint of peak eligibility algo + rules + land digest at build time.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub compute_key: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub road_m: Option<f64>,
    /// Nearest jeep-road sample `[lat, lon]` (also mirrored in access).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub road_loc: Option<[f64; 2]>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub hike_m: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub max_slope_deg: Option<f64>,
    /// Full hike profile — carried in-memory / via ``access/``; omitted from thin peak rows.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub hike: Option<PeakHikeProfile>,
    /// Paved-road anchor `[lat, lon]` (also mirrored in access).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub paved_loc: Option<[f64; 2]>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub jeep_m: Option<f64>,
    /// Full jeep profile — carried in-memory / via ``access/``; omitted from thin peak rows.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub jeep: Option<PeakJeepProfile>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub deny: Option<bool>,
}

impl PeakCatalogEntry {
    pub fn lat(&self) -> f64 {
        self.loc[0]
    }

    pub fn lon(&self) -> f64 {
        self.loc[1]
    }

    /// Build access payload from embedded or thin fields.
    pub fn to_place_access(&self) -> PlaceAccess {
        PlaceAccess {
            compute_key: None,
            paved_loc: self.paved_loc,
            road_loc: self.road_loc,
            jeep_m: self.jeep_m,
            jeep: self.jeep.clone(),
            hike_m: self.hike_m,
            hike: self.hike.clone(),
            max_slope_deg: self.max_slope_deg,
        }
    }

    /// Peak row written under `peaks/<slug>.yaml` (no profile blobs).
    pub fn thin(&self) -> Self {
        Self {
            name: self.name.clone(),
            loc: self.loc,
            elev_m: self.elev_m,
            source: self.source.clone(),
            compute_key: self.compute_key.clone(),
            road_m: self.road_m,
            road_loc: self.road_loc,
            hike_m: self.hike_m,
            max_slope_deg: self.max_slope_deg,
            hike: None,
            paved_loc: self.paved_loc,
            jeep_m: self.jeep_m,
            jeep: None,
            deny: self.deny,
        }
    }

    /// Apply access coords/profiles onto this peak row (in-memory / API join).
    pub fn with_access(&self, access: &PlaceAccess) -> Self {
        Self {
            name: self.name.clone(),
            loc: self.loc,
            elev_m: self.elev_m,
            source: self.source.clone(),
            compute_key: self.compute_key.clone(),
            road_m: self.road_m,
            road_loc: access.road_loc.or(self.road_loc),
            hike_m: access.hike_m.or(self.hike_m),
            max_slope_deg: access.max_slope_deg.or(self.max_slope_deg),
            hike: access.hike.clone().or_else(|| self.hike.clone()),
            paved_loc: access.paved_loc.or(self.paved_loc),
            jeep_m: access.jeep_m.or(self.jeep_m),
            jeep: access.jeep.clone().or_else(|| self.jeep.clone()),
            deny: self.deny,
        }
    }
}

/// Eligible-peaks catalog (in-memory; on disk as `peaks/` + `access/`).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct PeaksCatalog {
    pub generated_at: String,
    pub rules: PeakAccessRules,
    #[serde(default)]
    pub entries: HashMap<String, PeakCatalogEntry>,
}

impl Default for PeaksCatalog {
    fn default() -> Self {
        Self {
            generated_at: String::new(),
            rules: PeakAccessRules::default(),
            entries: HashMap::new(),
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
