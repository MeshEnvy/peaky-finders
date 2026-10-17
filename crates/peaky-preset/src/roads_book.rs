//! Project ``roads.yaml``: external ``sources``, unified ``rules`` (match or custom coords).

use std::collections::hash_map::DefaultHasher;
use std::fs;
use std::hash::{Hash, Hasher};
use std::path::Path;

use anyhow::{Context, Result};
use indexmap::IndexMap;
use serde::{Deserialize, Serialize};
use serde_yaml::Value;

use crate::project::ProjectLayout;
use crate::yaml_io::{read_preset_document, write_preset_value};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum RoadRole {
    Paved,
    Jeep,
    Hiking,
    Other,
    Private,
}

impl RoadRole {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Paved => "paved",
            Self::Jeep => "jeep",
            Self::Hiking => "hiking",
            Self::Other => "other",
            Self::Private => "private",
        }
    }

    pub fn parse(s: &str) -> Option<Self> {
        match s.trim().to_ascii_lowercase().as_str() {
            "paved" => Some(Self::Paved),
            "jeep" => Some(Self::Jeep),
            "hiking" | "hike" => Some(Self::Hiking),
            "other" => Some(Self::Other),
            "private" => Some(Self::Private),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct RoadMatch {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub kind: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub surface: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub grade: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub access: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RoadRule {
    #[serde(default, rename = "match", skip_serializing_if = "Option::is_none")]
    pub match_rule: Option<RoadMatch>,
    pub role: RoadRole,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub coords: Option<Vec<[f64; 2]>>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RoadCustom {
    pub id: String,
    pub role: RoadRole,
    pub coords: Vec<[f64; 2]>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct RoadsBook {
    #[serde(default)]
    pub sources: IndexMap<String, Value>,
    #[serde(default)]
    pub rules: Vec<RoadRule>,
}

const DRIVABLE_KINDS: &[&str] = &[
    "motorway",
    "trunk",
    "primary",
    "secondary",
    "motorway_link",
    "trunk_link",
    "primary_link",
    "secondary_link",
    "tertiary",
    "unclassified",
    "residential",
    "service",
    "track",
];

const WALKABLE_KINDS: &[&str] = &["foot", "path", "cycleway", "bridleway"];

pub fn is_paved_surface_tag(surface: Option<&str>) -> bool {
    matches!(
        surface,
        Some("paved") | Some("asphalt") | Some("concrete") | Some("chipseal") | Some("tarmac")
    )
}

fn kind_matches(list: &[&str], kind: &str) -> bool {
    list.iter().any(|k| *k == kind)
}

impl RoadsBook {
    pub fn has_unified_sources(&self) -> bool {
        !self.sources.is_empty()
    }

    pub fn match_rules_fingerprint(&self) -> String {
        let mut rules: Vec<&RoadRule> = self
            .rules
            .iter()
            .filter(|r| r.match_rule.is_some())
            .collect();
        fingerprint_yaml(&rules)
    }

    pub fn coords_rules_fingerprint(&self) -> String {
        let mut coords: Vec<&RoadRule> = self.rules.iter().filter(|r| r.coords.is_some()).collect();
        fingerprint_yaml(&coords)
    }

    pub fn default_role_for_segment(
        &self,
        id: &str,
        kind: &str,
        surface: &str,
        grade: &str,
        access: &str,
    ) -> RoadRole {
        let _ = (id, grade);
        if access == "private" {
            return RoadRole::Private;
        }
        let surface = if surface.is_empty() {
            "unknown"
        } else {
            surface
        };
        if kind_matches(DRIVABLE_KINDS, kind) {
            if surface == "paved" || is_paved_surface_tag(Some(surface)) {
                return RoadRole::Paved;
            }
            return RoadRole::Jeep;
        }
        if kind_matches(WALKABLE_KINDS, kind) {
            return RoadRole::Hiking;
        }
        RoadRole::Other
    }

    pub fn resolve_role(
        &self,
        id: &str,
        kind: &str,
        surface: &str,
        grade: &str,
        access: &str,
    ) -> RoadRole {
        let mut role = self.default_role_for_segment(id, kind, surface, grade, access);
        for rule in &self.rules {
            if let Some(m) = &rule.match_rule {
                if rule_matches(m, id, kind, surface, grade, access) {
                    role = rule.role;
                }
            } else if let Some(rule_id) = &rule.id {
                if rule_id == id {
                    role = rule.role;
                }
            }
        }
        role
    }

    pub fn coord_rules(&self) -> impl Iterator<Item = &RoadRule> {
        self.rules.iter().filter(|r| r.coords.is_some())
    }

    pub fn match_rules_only(&self) -> impl Iterator<Item = &RoadRule> {
        self.rules.iter().filter(|r| r.match_rule.is_some())
    }

    pub fn classify_osm_way(
        &self,
        osm_id: i64,
        highway: &str,
        tracktype: Option<&str>,
        surface: Option<&str>,
        _fourwd_only: bool,
    ) -> RoadRole {
        let grade = tracktype
            .and_then(|t| t.strip_prefix("grade"))
            .unwrap_or("unknown");
        let surf = surface.unwrap_or("unknown");
        self.resolve_role(&format!("osm:{osm_id}"), highway, surf, grade, "unknown")
    }

    pub fn classify_gtlf_line(
        &self,
        _layer_id: &str,
        _coords: &[(f64, f64)],
        foot_layer: bool,
    ) -> RoadRole {
        if foot_layer {
            RoadRole::Hiking
        } else {
            RoadRole::Jeep
        }
    }

    pub fn customs(&self) -> Vec<RoadCustom> {
        self.rules
            .iter()
            .filter_map(|r| {
                let coords = r.coords.clone()?;
                Some(RoadCustom {
                    id: r.id.clone().unwrap_or_else(|| "custom-anon".to_string()),
                    role: r.role,
                    coords,
                })
            })
            .collect()
    }
}

fn rule_matches(
    m: &RoadMatch,
    id: &str,
    kind: &str,
    surface: &str,
    grade: &str,
    access: &str,
) -> bool {
    if let Some(want) = &m.id {
        if want != id {
            return false;
        }
    }
    if let Some(want) = &m.kind {
        if want != kind {
            return false;
        }
    }
    if let Some(want) = &m.surface {
        if want == "paved" {
            if surface != "paved" && !is_paved_surface_tag(Some(surface)) {
                return false;
            }
        } else if want != surface {
            return false;
        }
    }
    if let Some(want) = &m.grade {
        if want != grade {
            return false;
        }
    }
    if let Some(want) = &m.access {
        if want != access {
            return false;
        }
    }
    m.id.is_some() || m.kind.is_some() || m.surface.is_some() || m.grade.is_some() || m.access.is_some()
}

pub fn gtlf_coord_hash(layer_id: &str, coords: &[(f64, f64)]) -> String {
    let mut hasher = DefaultHasher::new();
    layer_id.hash(&mut hasher);
    for &(lat, lon) in coords {
        format!("{lat:.5},{lon:.5}").hash(&mut hasher);
    }
    format!("{:016x}", hasher.finish())
}

fn fingerprint_yaml<T: Serialize>(value: &T) -> String {
    let bytes = serde_yaml::to_string(value).unwrap_or_default();
    let mut hasher = DefaultHasher::new();
    bytes.hash(&mut hasher);
    format!("{:016x}", hasher.finish())
}

pub fn roads_fingerprint(book: &RoadsBook) -> String {
    fingerprint_yaml(book)
}

pub fn roads_global_fingerprint(book: &RoadsBook) -> String {
    book.match_rules_fingerprint()
}

const EARTH_RADIUS_M: f64 = 6_371_000.0;

fn haversine_m(lat1: f64, lon1: f64, lat2: f64, lon2: f64) -> f64 {
    let dlat = (lat2 - lat1).to_radians();
    let dlon = (lon2 - lon1).to_radians();
    let a = (dlat / 2.0).sin().powi(2)
        + lat1.to_radians().cos() * lat2.to_radians().cos() * (dlon / 2.0).sin().powi(2);
    EARTH_RADIUS_M * 2.0 * a.sqrt().asin()
}

fn dist_to_segment_m(dest_lat: f64, dest_lon: f64, a: [f64; 2], b: [f64; 2]) -> f64 {
    let mut best = f64::INFINITY;
    for step in 0..=4 {
        let t = step as f64 / 4.0;
        let lat = a[0] + t * (b[0] - a[0]);
        let lon = a[1] + t * (b[1] - a[1]);
        best = best.min(haversine_m(dest_lat, dest_lon, lat, lon));
    }
    best
}

fn custom_intersects_jeep_disk(
    coords: &[[f64; 2]],
    dest_lat: f64,
    dest_lon: f64,
    max_jeep_m: f64,
) -> bool {
    if coords.len() < 2 {
        return false;
    }
    for &[lat, lon] in coords {
        if haversine_m(dest_lat, dest_lon, lat, lon) <= max_jeep_m + 1.0 {
            return true;
        }
    }
    for w in coords.windows(2) {
        if dist_to_segment_m(dest_lat, dest_lon, w[0], w[1]) <= max_jeep_m + 1.0 {
            return true;
        }
    }
    false
}

pub fn roads_local_fingerprint(
    book: &RoadsBook,
    dest_lat: f64,
    dest_lon: f64,
    max_jeep_m: f64,
) -> String {
    let mut locals: Vec<&RoadRule> = book
        .rules
        .iter()
        .filter(|r| {
            r.coords
                .as_ref()
                .is_some_and(|c| matches!(r.role, RoadRole::Paved | RoadRole::Jeep))
                && custom_intersects_jeep_disk(
                    r.coords.as_ref().unwrap(),
                    dest_lat,
                    dest_lon,
                    max_jeep_m,
                )
        })
        .collect();
    if locals.is_empty() {
        return "local-empty".to_string();
    }
    locals.sort_by(|a, b| a.id.cmp(&b.id));
    fingerprint_yaml(&locals)
}

pub fn load_roads_book(path: &Path) -> Result<RoadsBook> {
    if !path.is_file() {
        return Ok(RoadsBook::default());
    }
    let doc = read_preset_document(path).with_context(|| format!("read {}", path.display()))?;
    serde_yaml::from_value(Value::Mapping(doc)).context("parse roads.yaml")
}

pub fn save_roads_book(path: &Path, book: &RoadsBook) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).context("create roads.yaml parent")?;
    }
    let value = serde_yaml::to_value(book).context("serialize roads.yaml")?;
    write_preset_value(path, value)
}

pub fn ensure_roads_book(config_path: &Path) -> Result<RoadsBook> {
    let layout = ProjectLayout::from_config_path(config_path)?;
    let path = layout.roads_path();
    if !path.is_file() {
        return Ok(RoadsBook::default());
    }
    load_roads_book(&path)
}

pub fn roads_path_for_config(config_path: &Path) -> Result<std::path::PathBuf> {
    Ok(ProjectLayout::from_config_path(config_path)?.roads_path())
}

pub fn source_config_yaml(book: &RoadsBook, source_key: &str) -> Result<String> {
    let cfg = book
        .sources
        .get(source_key)
        .context("unknown roads source")?;
    serde_yaml::to_string(cfg).context("serialize source config")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_drivable_unpaved_is_jeep() {
        let book = RoadsBook::default();
        assert_eq!(
            book.resolve_role("osm:1", "tertiary", "unpaved", "unknown", "unknown"),
            RoadRole::Jeep
        );
    }

    #[test]
    fn default_drivable_paved_is_paved() {
        let book = RoadsBook::default();
        assert_eq!(
            book.resolve_role("osm:1", "tertiary", "paved", "unknown", "unknown"),
            RoadRole::Paved
        );
    }

    #[test]
    fn bottom_match_rule_wins() {
        let book = RoadsBook {
            rules: vec![
                RoadRule {
                    match_rule: Some(RoadMatch {
                        kind: Some("path".into()),
                        ..Default::default()
                    }),
                    role: RoadRole::Jeep,
                    id: None,
                    coords: None,
                },
                RoadRule {
                    match_rule: Some(RoadMatch {
                        id: Some("osm:99".into()),
                        ..Default::default()
                    }),
                    role: RoadRole::Paved,
                    id: None,
                    coords: None,
                },
            ],
            ..Default::default()
        };
        assert_eq!(
            book.resolve_role("osm:99", "path", "unpaved", "unknown", "unknown"),
            RoadRole::Paved
        );
    }

    #[test]
    fn private_access_wins_over_a_paved_surface_rule() {
        let book: RoadsBook = serde_yaml::from_str(
            "rules:\n  - match: { kind: service, surface: paved }\n    role: paved\n  - match: { access: private }\n    role: private\n",
        )
        .unwrap();
        assert_eq!(
            book.resolve_role("osm:1", "service", "paved", "unknown", "private"),
            RoadRole::Private
        );
        assert_eq!(
            book.resolve_role("osm:1", "service", "paved", "unknown", "unknown"),
            RoadRole::Paved
        );
    }

    #[test]
    fn sources_keep_file_order() {
        let book: RoadsBook = serde_yaml::from_str(
            "sources:\n  osm: {cache_key: osm}\n  blm: {cache_key: blm}\n  usfs: {cache_key: usfs}\n",
        )
        .unwrap();
        let keys: Vec<_> = book.sources.keys().cloned().collect();
        assert_eq!(keys, vec!["osm", "blm", "usfs"]);
    }
}
