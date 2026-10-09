//! Preset YAML schema (serve-only slim schema).

use std::collections::{HashMap, HashSet};
use std::ops::{Deref, DerefMut};

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

pub const DEFAULT_OSM_PBF_URL: &str =
    "https://download.geofabrik.de/north-america/us/nevada-latest.osm.pbf";

pub const DEFAULT_GTLF_MAPSERVER: &str =
    "https://gis.blm.gov/arcgis/rest/services/transportation/BLM_Natl_GTLF_Public_Display/MapServer";

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct SourcesConfig {
    pub osm: OsmSourceConfig,
    pub gnis: GnisSourceConfig,
    pub gtlf: GtlfSourceConfig,
}

impl Default for SourcesConfig {
    fn default() -> Self {
        Self {
            osm: OsmSourceConfig::default(),
            gnis: GnisSourceConfig::default(),
            gtlf: GtlfSourceConfig::default(),
        }
    }
}

/// BLM Ground Transportation Linear Features (GTLF) for jeep + foot-trail routing.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct GtlfSourceConfig {
    pub enabled: bool,
    /// MapServer layer URLs merged into the jeep graph (roads + OHV trails).
    pub jeep_layers: Vec<String>,
    /// MapServer layer URLs for foot-only walk graph.
    pub foot_layers: Vec<String>,
}

impl Default for GtlfSourceConfig {
    fn default() -> Self {
        let base = DEFAULT_GTLF_MAPSERVER;
        Self {
            enabled: false,
            jeep_layers: vec![
                format!("{base}/0"),
                format!("{base}/1"),
                format!("{base}/2"),
                format!("{base}/3"),
            ],
            foot_layers: vec![format!("{base}/4"), format!("{base}/5")],
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct OsmSourceConfig {
    pub pbf_url: String,
    pub cache_name: String,
}

impl Default for OsmSourceConfig {
    fn default() -> Self {
        Self {
            pbf_url: DEFAULT_OSM_PBF_URL.to_string(),
            cache_name: "nevada-latest.osm.pbf".to_string(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct GnisSourceConfig {
    pub path: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub nested_gdb: Option<String>,
    pub layer: String,
    pub filter: String,
}

impl Default for GnisSourceConfig {
    fn default() -> Self {
        Self {
            path: "data/sources/Gazetteer_National.gdb".to_string(),
            nested_gdb: Some("Gazetteer_National_GDB.gdb".to_string()),
            layer: "Gaz_Features".to_string(),
            filter: "feature_class='Summit'".to_string(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct PeaksSeedConfig {
    pub gnis: bool,
    pub site_tags: Vec<String>,
}

impl Default for PeaksSeedConfig {
    fn default() -> Self {
        Self {
            gnis: true,
            site_tags: vec![
                "eip".into(),
                "alertwildfire".into(),
                "installed".into(),
            ],
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct HikeMediumBands {
    pub min_max_grade_pct: f64,
    pub min_avg_grade_pct: f64,
    pub min_gain_m: f64,
}

impl Default for HikeMediumBands {
    fn default() -> Self {
        Self {
            min_max_grade_pct: 15.0,
            min_avg_grade_pct: 8.0,
            min_gain_m: 40.0,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct HikeDifficultBands {
    pub min_gain_m: f64,
    pub min_avg_grade_pct: f64,
}

impl Default for HikeDifficultBands {
    fn default() -> Self {
        Self {
            min_gain_m: 80.0,
            min_avg_grade_pct: 15.0,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct HikeExtremeBands {
    pub min_gain_m: f64,
    pub min_avg_grade_pct: f64,
    /// Max segment grade on road-to-summit profile (A* / filter reject above this).
    pub max_segment_grade_pct: f64,
}

impl Default for HikeExtremeBands {
    fn default() -> Self {
        Self {
            min_gain_m: 250.0,
            min_avg_grade_pct: 18.0,
            max_segment_grade_pct: 110.0,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct HikeDifficultyBands {
    pub medium: HikeMediumBands,
    pub difficult: HikeDifficultBands,
    pub extreme: HikeExtremeBands,
}

impl Default for HikeDifficultyBands {
    fn default() -> Self {
        Self {
            medium: HikeMediumBands::default(),
            difficult: HikeDifficultBands::default(),
            extreme: HikeExtremeBands::default(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct JeepMediumBands {
    pub highways: Vec<String>,
    pub tracktypes: Vec<String>,
}

impl Default for JeepMediumBands {
    fn default() -> Self {
        Self {
            highways: vec![
                "unclassified".into(),
                "residential".into(),
                "service".into(),
            ],
            tracktypes: vec!["grade2".into()],
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct JeepDifficultBands {
    pub tracktypes: Vec<String>,
}

impl Default for JeepDifficultBands {
    fn default() -> Self {
        Self {
            tracktypes: vec!["grade3".into(), "grade4".into()],
        }
    }
}

fn default_jeep_max_class_m() -> std::collections::HashMap<String, f64> {
    [
        ("grade1", 80_467.0),
        ("grade2", 32_187.0),
        ("grade3", 16_093.0),
        ("grade4", 8_047.0),
        ("grade5", 8_047.0),
        ("unclassified", 8_047.0),
        ("track", 8_047.0),
        ("residential", 32_187.0),
        ("service", 8_047.0),
    ]
    .into_iter()
    .map(|(k, v)| (k.to_string(), v))
    .collect()
}

/// Pessimistic OSM class key for catalog jeep length budgets.
pub fn jeep_budget_class_key(highway: &str, tracktype: Option<&str>) -> String {
    if let Some(tt) = tracktype {
        if !tt.is_empty() {
            return tt.to_string();
        }
    }
    match highway {
        "unclassified" | "track" => "grade5".to_string(),
        other => other.to_string(),
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct JeepExtremeBands {
    pub tracktypes: Vec<String>,
    /// Jeep gate: paved→park route longer than this is reject (20 mi default).
    pub max_route_m: f64,
    /// Per-class horizontal caps on the jeep leg (m). Missing key → grade5 cap.
    #[serde(default = "default_jeep_max_class_m")]
    pub max_class_m: std::collections::HashMap<String, f64>,
}

impl JeepExtremeBands {
    pub fn class_cap_m(&self, highway: &str, tracktype: Option<&str>) -> f64 {
        let key = jeep_budget_class_key(highway, tracktype);
        self.max_class_m
            .get(&key)
            .or_else(|| self.max_class_m.get("grade5"))
            .copied()
            .unwrap_or(self.max_route_m)
    }
}

impl Default for JeepExtremeBands {
    fn default() -> Self {
        Self {
            tracktypes: vec!["grade5".into()],
            max_route_m: DEFAULT_MAX_JEEP_M,
            max_class_m: default_jeep_max_class_m(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct JeepDifficultyBands {
    pub medium: JeepMediumBands,
    pub difficult: JeepDifficultBands,
    pub extreme: JeepExtremeBands,
}

impl Default for JeepDifficultyBands {
    fn default() -> Self {
        Self {
            medium: JeepMediumBands::default(),
            difficult: JeepDifficultBands::default(),
            extreme: JeepExtremeBands::default(),
        }
    }
}

/// Hike + jeep difficulty bands and gates under ``scan.peaks.difficulty``.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct PeaksDifficultyConfig {
    pub hike: HikeDifficultyBands,
    pub jeep: JeepDifficultyBands,
}

impl Default for PeaksDifficultyConfig {
    fn default() -> Self {
        Self {
            hike: HikeDifficultyBands::default(),
            jeep: JeepDifficultyBands::default(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct AccessPreferenceConfig {
    pub w_hike: f64,
    pub w_trail: f64,
    pub w_jeep: f64,
    pub w_difficulty: f64,
}

impl Default for AccessPreferenceConfig {
    fn default() -> Self {
        Self {
            w_hike: 1.0,
            w_trail: 0.001,
            w_jeep: 0.0001,
            w_difficulty: 100.0,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct PeaksScanConfig {
    #[serde(default = "default_max_road_search_m")]
    pub max_road_search_m: f64,
    pub hike_path_max_m: f64,
    pub min_prominence_m: f64,
    pub summit_snap_m: f64,
    pub candidate_dedup_m: f64,
    pub difficulty: PeaksDifficultyConfig,
    #[serde(default, skip_serializing, rename = "max_slope_grade_pct")]
    legacy_max_slope_grade_pct: Option<f64>,
    #[serde(default, skip_serializing, rename = "max_jeep_m")]
    legacy_max_jeep_m: Option<f64>,
    #[serde(default = "default_jeep_highways")]
    pub road_highways: Vec<String>,
    #[serde(default = "default_paved_highways")]
    pub paved_highways: Vec<String>,
    pub profile_sample_m: f64,
    pub road_sample_step_m: f64,
    pub place_road_search_m: f64,
    pub seeds: PeaksSeedConfig,
    pub preference: AccessPreferenceConfig,
}

fn default_max_road_search_m() -> f64 {
    805.0
}

impl PeaksScanConfig {
    /// Apply deprecated top-level slope/jeep caps into ``difficulty`` when present.
    pub fn normalize_legacy(&mut self) {
        if let Some(pct) = self.legacy_max_slope_grade_pct.take() {
            self.difficulty.hike.extreme.max_segment_grade_pct = pct;
        }
        if let Some(m) = self.legacy_max_jeep_m.take() {
            self.difficulty.jeep.extreme.max_route_m = m;
        }
    }

    pub fn hike_segment_gate_grade_pct(&self) -> f64 {
        self.difficulty.hike.extreme.max_segment_grade_pct
    }

    pub fn jeep_route_gate_m(&self) -> f64 {
        self.difficulty.jeep.extreme.max_route_m
    }
}

impl Default for PeaksScanConfig {
    fn default() -> Self {
        Self {
            max_road_search_m: default_max_road_search_m(),
            hike_path_max_m: DEFAULT_HIKE_PATH_MAX_M,
            min_prominence_m: 20.0,
            summit_snap_m: 500.0,
            candidate_dedup_m: 300.0,
            difficulty: PeaksDifficultyConfig::default(),
            legacy_max_slope_grade_pct: None,
            legacy_max_jeep_m: None,
            road_highways: default_jeep_highways(),
            paved_highways: default_paved_highways(),
            profile_sample_m: DEFAULT_ACCESS_PROFILE_SAMPLE_M,
            road_sample_step_m: DEFAULT_OSM_ROAD_SAMPLE_STEP_M,
            place_road_search_m: DEFAULT_PLACE_ROAD_SEARCH_M,
            seeds: PeaksSeedConfig::default(),
            preference: AccessPreferenceConfig::default(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct ScanConfig {
    pub peak_bin_size_m: f64,
    pub max_candidates: u32,
    pub peaks: PeaksScanConfig,
}

impl Default for ScanConfig {
    fn default() -> Self {
        Self {
            peak_bin_size_m: 500.0,
            max_candidates: 48,
            peaks: PeaksScanConfig::default(),
        }
    }
}

/// Access rules stamped into ``peaks/_meta.yaml`` when ``peaky peaks`` runs.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct PeakAccessRules {
    pub max_hike_m: f64,
    /// Max segment grade on road-to-summit profile (percent; rise/run × 100).
    #[serde(default = "default_stamped_max_slope_grade_pct")]
    pub max_slope_grade_pct: f64,
    pub max_slope_deg: f64,
    #[serde(default = "default_jeep_highways")]
    pub road_highways: Vec<String>,
    /// Max horizontal jeep route from paved road to park point (20 mi default).
    #[serde(default = "default_stamped_max_jeep_m")]
    pub max_jeep_m: f64,
    #[serde(default = "default_paved_highways")]
    pub paved_highways: Vec<String>,
    #[serde(default)]
    pub difficulty: PeaksDifficultyConfig,
}

fn default_max_slope_deg_from_pct(pct: f64) -> f64 {
    (pct / 100.0).atan().to_degrees()
}

fn default_stamped_max_slope_grade_pct() -> f64 {
    PeaksDifficultyConfig::default().hike.extreme.max_segment_grade_pct
}

fn default_stamped_max_jeep_m() -> f64 {
    PeaksDifficultyConfig::default().jeep.extreme.max_route_m
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

/// Max 3D length for a grade-safe DEM hike path (1 mi). Crow-flies park→summit may be shorter.
pub const DEFAULT_HIKE_PATH_MAX_M: f64 = 1609.0;

impl Default for PeakAccessRules {
    fn default() -> Self {
        let difficulty = PeaksDifficultyConfig::default();
        let max_slope_grade_pct = difficulty.hike.extreme.max_segment_grade_pct;
        Self {
            max_hike_m: 805.0,
            max_slope_grade_pct,
            max_slope_deg: default_max_slope_deg_from_pct(max_slope_grade_pct),
            road_highways: default_jeep_highways(),
            max_jeep_m: difficulty.jeep.extreme.max_route_m,
            paved_highways: default_paved_highways(),
            difficulty,
        }
    }
}

/// Bump when hike/jeep routing or DEM profile semantics change (forces recompute).
/// Written into ``access/_meta.yaml`` as ``algo_version``.
pub const ACCESS_ALGO_VERSION: u32 = 17;

/// Bump when peak eligibility / summit snap / index schema semantics change.
pub const PEAK_ALGO_VERSION: u32 = 10;

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
    /// Max 3D length for a grade-safe DEM hike path (may exceed crow-flies proximity).
    #[serde(default = "default_hike_path_max_m")]
    pub hike_path_max_m: f64,
}

fn default_hike_path_max_m() -> f64 {
    DEFAULT_HIKE_PATH_MAX_M
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
            hike_path_max_m: DEFAULT_HIKE_PATH_MAX_M,
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

/// Graph vertex on jeep-class edges only (map orange line).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct PeakJeepLinePoint {
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
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub jeep_line: Vec<PeakJeepLinePoint>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Default)]
pub struct BudgetLegReport {
    pub used: f64,
    pub cap: f64,
    pub over_by: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Default)]
pub struct RouteBudgetReport {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub jeep_route: Option<BudgetLegReport>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub jeep_class: Option<BudgetLegReport>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub jeep_class_key: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub hike_path: Option<BudgetLegReport>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub hike_grade: Option<BudgetLegReport>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Default)]
pub struct AccessRoute {
    pub paved_loc: Option<[f64; 2]>,
    pub road_loc: Option<[f64; 2]>,
    pub jeep_m: Option<f64>,
    pub jeep: Option<PeakJeepProfile>,
    pub hike_m: Option<f64>,
    pub hike: Option<PeakHikeProfile>,
    pub trail_loc: Option<[f64; 2]>,
    pub trail_m: Option<f64>,
    pub trail: Option<PeakHikeProfile>,
    pub max_slope_deg: Option<f64>,
    pub hike_difficulty: Option<String>,
    pub jeep_difficulty: Option<String>,
    pub access_difficulty: Option<String>,
    #[serde(default)]
    pub budget: RouteBudgetReport,
}

/// One selectable access path for a site (park + jeep variant).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct SiteAccessOption {
    pub id: String,
    #[serde(default)]
    pub recommended: bool,
    pub passes_budget: bool,
    #[serde(flatten)]
    pub route: AccessRoute,
}

/// Shared paved→park→pad access for a place slug (`access/<slug>.yaml`).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Default)]
pub struct AccessProbe {
    /// Fingerprint of algo version + settings that produced this access.
    #[serde(default, skip_serializing_if = "Option::is_none", alias = "compute_key")]
    pub probe_key: Option<String>,
    #[serde(default = "default_probe_eligible")]
    pub eligible: bool,
    #[serde(flatten)]
    pub recommended: AccessRoute,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub budget_route: Option<AccessRoute>,
    /// Up to 4 parks × 2 jeep paths; computed on site warm only.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub site_options: Option<Vec<SiteAccessOption>>,
    /// Bumped when the site option builder changes. Missing or older caches rebuild.
    #[serde(default, skip_serializing_if = "is_zero_u8")]
    pub site_options_rev: u8,
}

pub type PlaceAccess = AccessProbe;

/// Display route for a site: chosen option or probe recommended.
pub fn resolve_site_display_route(
    probe: &AccessProbe,
    access_choice: Option<&str>,
) -> AccessRoute {
    if let Some(id) = access_choice {
        if let Some(opts) = &probe.site_options {
            if let Some(opt) = opts.iter().find(|o| o.id == id) {
                return opt.route.clone();
            }
        }
        return probe.recommended.clone();
    }
    if let Some(opts) = &probe.site_options {
        if let Some(opt) = opts.iter().find(|o| o.recommended) {
            return opt.route.clone();
        }
        if let Some(opt) = opts.first() {
            return opt.route.clone();
        }
    }
    probe.recommended.clone()
}

fn default_probe_eligible() -> bool {
    true
}

fn is_zero_u8(v: &u8) -> bool {
    *v == 0
}

impl Deref for AccessProbe {
    type Target = AccessRoute;

    fn deref(&self) -> &Self::Target {
        &self.recommended
    }
}

impl DerefMut for AccessProbe {
    fn deref_mut(&mut self) -> &mut Self::Target {
        &mut self.recommended
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
    /// Stable id from site access ``options``; cleared when ``loc`` moves.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub access_choice: Option<String>,
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

/// One site-to-site link in the book (legacy pair or structured provenance row).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(untagged)]
pub enum BookLink {
    Legacy([String; 2]),
    Record {
        a: String,
        b: String,
        #[serde(default)]
        origin: Option<String>,
        #[serde(default)]
        attest: Option<LinkAttest>,
        #[serde(default)]
        rf_at: Option<String>,
        #[serde(default)]
        field_at: Option<String>,
    },
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct LinkAttest {
    #[serde(default)]
    pub manual: bool,
    #[serde(default)]
    pub rf: bool,
    #[serde(default)]
    pub field: bool,
}

impl BookLink {
    pub fn slug_pair(&self) -> PresetResult<[String; 2]> {
        let (a, b) = match self {
            BookLink::Legacy([a, b]) => (a.as_str(), b.as_str()),
            BookLink::Record { a, b, .. } => (a.as_str(), b.as_str()),
        };
        normalize_link_pair(&[a.to_string(), b.to_string()], 0)
    }
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
    pub links: Vec<BookLink>,
    #[serde(default)]
    pub land: LandConfig,
    #[serde(default)]
    pub sources: SourcesConfig,
    #[serde(default)]
    pub scan: ScanConfig,
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
            sources: SourcesConfig::default(),
            scan: ScanConfig::default(),
        }
    }
}

impl Preset {
    /// Peak eligibility + access routing knobs from ``config.yaml`` → ``scan.peaks``.
    pub fn peak_access_rules(&self) -> PeakAccessRules {
        let p = &self.scan.peaks;
        let max_slope_grade_pct = p.hike_segment_gate_grade_pct();
        PeakAccessRules {
            max_hike_m: p.max_road_search_m,
            max_slope_grade_pct,
            max_slope_deg: default_max_slope_deg_from_pct(max_slope_grade_pct),
            road_highways: p.road_highways.clone(),
            max_jeep_m: p.jeep_route_gate_m(),
            paved_highways: p.paved_highways.clone(),
            difficulty: p.difficulty.clone(),
        }
    }

    /// Pathfinding stamp for ``access/_meta.yaml`` (synced by ``ensure_access_meta``).
    pub fn resolved_access_meta(&self) -> AccessMeta {
        let p = &self.scan.peaks;
        AccessMeta {
            algo_version: ACCESS_ALGO_VERSION,
            profile_sample_m: p.profile_sample_m,
            road_sample_step_m: p.road_sample_step_m,
            max_jeep_m: p.jeep_route_gate_m(),
            road_highways: p.road_highways.clone(),
            paved_highways: p.paved_highways.clone(),
            place_road_search_m: p.place_road_search_m,
            hike_path_max_m: p.hike_path_max_m,
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
            if sim_map.contains_key(serde_yaml::Value::String("boards".into())) {
                return Err(err(
                    "simulation.boards is removed; move board params to boards.yaml",
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
    for link in &preset.links {
        let [a, b] = link.slug_pair()?;
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

    Ok(())
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

}
