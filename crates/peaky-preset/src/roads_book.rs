//! Project ``roads.yaml``: highway classifiers, segment overrides, custom polylines.

use std::collections::hash_map::DefaultHasher;
use std::fs;
use std::hash::{Hash, Hasher};
use std::path::Path;

use anyhow::{Context, Result};
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
}

impl RoadRole {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Paved => "paved",
            Self::Jeep => "jeep",
            Self::Hiking => "hiking",
            Self::Other => "other",
        }
    }
}

/// How a class row matches ``surface`` (OSM tag).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RoadSurfaceMatch {
    Exact(String),
    PavedSurfaces,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RoadClassRow {
    pub id: String,
    pub highway: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub tracktype: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub surface: Option<RoadSurfaceMatch>,
    pub role: RoadRole,
    /// Seed default for UI reset (same as initial ``role`` when seeded).
    pub default_role: RoadRole,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct GtlfOverrideKey {
    pub layer: String,
    pub coord_hash: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(untagged)]
pub enum SegmentOverrideKey {
    Osm { osm_id: i64 },
    Gtlf { gtlf: GtlfOverrideKey },
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SegmentOverride {
    #[serde(flatten)]
    pub key: SegmentOverrideKey,
    pub role: RoadRole,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RoadCustom {
    pub id: String,
    pub role: RoadRole,
    /// ``[[lat, lon], ...]``
    pub coords: Vec<[f64; 2]>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct RoadsBook {
    #[serde(default)]
    pub classes: Vec<RoadClassRow>,
    #[serde(default)]
    pub overrides: Vec<SegmentOverride>,
    #[serde(default)]
    pub customs: Vec<RoadCustom>,
}

pub fn is_paved_surface_tag(surface: Option<&str>) -> bool {
    matches!(
        surface,
        Some("paved") | Some("asphalt") | Some("concrete") | Some("chipseal") | Some("tarmac")
    )
}

fn row_matches(row: &RoadClassRow, highway: &str, tracktype: Option<&str>, surface: Option<&str>) -> bool {
    if row.highway != highway {
        return false;
    }
    if let Some(want) = &row.tracktype {
        if tracktype != Some(want.as_str()) {
            return false;
        }
    }
    if let Some(rule) = &row.surface {
        return match rule {
            RoadSurfaceMatch::Exact(s) => surface == Some(s.as_str()),
            RoadSurfaceMatch::PavedSurfaces => is_paved_surface_tag(surface),
        };
    }
    true
}

impl RoadsBook {
    pub fn classify_osm_way(
        &self,
        osm_id: i64,
        highway: &str,
        tracktype: Option<&str>,
        surface: Option<&str>,
        _fourwd_only: bool,
    ) -> RoadRole {
        for ov in &self.overrides {
            if let SegmentOverrideKey::Osm { osm_id: id } = &ov.key {
                if *id == osm_id {
                    return ov.role;
                }
            }
        }
        for row in &self.classes {
            if row_matches(row, highway, tracktype, surface) {
                return row.role;
            }
        }
        RoadRole::Other
    }

    pub fn classify_gtlf_line(
        &self,
        layer_id: &str,
        coords: &[(f64, f64)],
        foot_layer: bool,
    ) -> RoadRole {
        let hash = gtlf_coord_hash(layer_id, coords);
        for ov in &self.overrides {
            if let SegmentOverrideKey::Gtlf { gtlf } = &ov.key {
                if gtlf.layer == layer_id && gtlf.coord_hash == hash {
                    return ov.role;
                }
            }
        }
        if foot_layer {
            RoadRole::Hiking
        } else {
            RoadRole::Jeep
        }
    }

    pub fn reset_class_row(&mut self, id: &str) -> bool {
        let Some(row) = self.classes.iter_mut().find(|r| r.id == id) else {
            return false;
        };
        row.role = row.default_role;
        true
    }
}

pub fn gtlf_coord_hash(layer_id: &str, coords: &[(f64, f64)]) -> String {
    let mut hasher = DefaultHasher::new();
    layer_id.hash(&mut hasher);
    for &(lat, lon) in coords {
        format!("{lat:.5},{lon:.5}").hash(&mut hasher);
    }
    format!("{:016x}", hasher.finish())
}

pub fn seed_roads_book() -> RoadsBook {
    let mut classes = Vec::new();
    let mut push = |id: &str, highway: &str, tracktype: Option<&str>, surface: Option<RoadSurfaceMatch>, role: RoadRole| {
        classes.push(RoadClassRow {
            id: id.to_string(),
            highway: highway.to_string(),
            tracktype: tracktype.map(str::to_string),
            surface,
            role,
            default_role: role,
        });
    };

    push("track_grade1", "track", Some("grade1"), None, RoadRole::Other);
    push("track_grade2", "track", Some("grade2"), None, RoadRole::Other);
    push(
        "track_surface_paved",
        "track",
        None,
        Some(RoadSurfaceMatch::Exact("paved".into())),
        RoadRole::Other,
    );
    push("track_grade3", "track", Some("grade3"), None, RoadRole::Jeep);
    push("track_grade4", "track", Some("grade4"), None, RoadRole::Jeep);
    push("track_grade5", "track", Some("grade5"), None, RoadRole::Jeep);
    push("track", "track", None, None, RoadRole::Jeep);

    push(
        "tertiary_paved_surface",
        "tertiary",
        None,
        Some(RoadSurfaceMatch::PavedSurfaces),
        RoadRole::Paved,
    );
    push(
        "unclassified_paved_surface",
        "unclassified",
        None,
        Some(RoadSurfaceMatch::PavedSurfaces),
        RoadRole::Paved,
    );
    push(
        "residential_paved_surface",
        "residential",
        None,
        Some(RoadSurfaceMatch::PavedSurfaces),
        RoadRole::Paved,
    );
    push("tertiary", "tertiary", None, None, RoadRole::Jeep);
    push("unclassified", "unclassified", None, None, RoadRole::Jeep);
    push("residential", "residential", None, None, RoadRole::Jeep);
    push("service", "service", None, None, RoadRole::Jeep);

    for hw in [
        "motorway",
        "trunk",
        "primary",
        "secondary",
        "motorway_link",
        "trunk_link",
        "primary_link",
        "secondary_link",
    ] {
        push(hw, hw, None, None, RoadRole::Paved);
    }

    push(
        "path_paved",
        "path",
        None,
        Some(RoadSurfaceMatch::PavedSurfaces),
        RoadRole::Other,
    );
    push("path", "path", None, None, RoadRole::Jeep);

    for hw in [
        "road",
        "living_street",
        "footway",
        "cycleway",
        "bridleway",
        "steps",
        "pedestrian",
    ] {
        push(hw, hw, None, None, RoadRole::Other);
    }

    RoadsBook {
        classes,
        overrides: Vec::new(),
        customs: Vec::new(),
    }
}

pub fn roads_fingerprint(book: &RoadsBook) -> String {
    let bytes = serde_yaml::to_string(book).unwrap_or_default();
    let mut hasher = DefaultHasher::new();
    bytes.hash(&mut hasher);
    format!("{:016x}", hasher.finish())
}

#[derive(Serialize)]
struct RoadsGlobalStamp<'a> {
    classes: &'a [RoadClassRow],
    overrides: &'a [SegmentOverride],
}

fn fingerprint_yaml<T: Serialize>(value: &T) -> String {
    let bytes = serde_yaml::to_string(value).unwrap_or_default();
    let mut hasher = DefaultHasher::new();
    bytes.hash(&mut hasher);
    format!("{:016x}", hasher.finish())
}

/// Class table + segment overrides (any change invalidates every probe).
pub fn roads_global_fingerprint(book: &RoadsBook) -> String {
    fingerprint_yaml(&RoadsGlobalStamp {
        classes: &book.classes,
        overrides: &book.overrides,
    })
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

fn custom_intersects_jeep_disk(custom: &RoadCustom, dest_lat: f64, dest_lon: f64, max_jeep_m: f64) -> bool {
    if custom.coords.len() < 2 {
        return false;
    }
    for &[lat, lon] in &custom.coords {
        if haversine_m(dest_lat, dest_lon, lat, lon) <= max_jeep_m + 1.0 {
            return true;
        }
    }
    for w in custom.coords.windows(2) {
        if dist_to_segment_m(dest_lat, dest_lon, w[0], w[1]) <= max_jeep_m + 1.0 {
            return true;
        }
    }
    false
}

/// Jeep/paved customs whose geometry can affect routing to ``(dest_lat, dest_lon)``.
pub fn roads_local_fingerprint(
    book: &RoadsBook,
    dest_lat: f64,
    dest_lon: f64,
    max_jeep_m: f64,
) -> String {
    let mut locals: Vec<RoadCustom> = book
        .customs
        .iter()
        .filter(|c| matches!(c.role, RoadRole::Paved | RoadRole::Jeep))
        .filter(|c| custom_intersects_jeep_disk(c, dest_lat, dest_lon, max_jeep_m))
        .cloned()
        .collect();
    if locals.is_empty() {
        return "local-empty".to_string();
    }
    locals.sort_by(|a, b| a.id.cmp(&b.id));
    fingerprint_yaml(&locals)
}

pub fn load_roads_book(path: &Path) -> Result<RoadsBook> {
    if !path.is_file() {
        return Ok(seed_roads_book());
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

/// Load project ``roads.yaml``, writing the seed file if missing.
pub fn ensure_roads_book(config_path: &Path) -> Result<RoadsBook> {
    let layout = ProjectLayout::from_config_path(config_path)?;
    let path = layout.roads_path();
    if !path.is_file() {
        let seed = seed_roads_book();
        save_roads_book(&path, &seed)?;
        return Ok(seed);
    }
    load_roads_book(&path)
}

pub fn roads_path_for_config(config_path: &Path) -> Result<std::path::PathBuf> {
    Ok(ProjectLayout::from_config_path(config_path)?.roads_path())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn seed_grade2_is_other() {
        let book = seed_roads_book();
        assert_eq!(
            book.classify_osm_way(1, "track", Some("grade2"), None, false),
            RoadRole::Other
        );
    }

    #[test]
    fn seed_tertiary_dirt_is_jeep() {
        let book = seed_roads_book();
        assert_eq!(
            book.classify_osm_way(1, "tertiary", None, Some("gravel"), false),
            RoadRole::Jeep
        );
    }

    #[test]
    fn seed_tertiary_asphalt_is_paved() {
        let book = seed_roads_book();
        assert_eq!(
            book.classify_osm_way(1, "tertiary", None, Some("asphalt"), false),
            RoadRole::Paved
        );
    }

    #[test]
    fn seed_path_unpaved_is_jeep() {
        let book = seed_roads_book();
        assert_eq!(
            book.classify_osm_way(532156393, "path", None, Some("unpaved"), false),
            RoadRole::Jeep
        );
    }

    #[test]
    fn seed_path_paved_is_other() {
        let book = seed_roads_book();
        assert_eq!(
            book.classify_osm_way(1, "path", None, Some("asphalt"), false),
            RoadRole::Other
        );
    }

    #[test]
    fn osm_override_wins() {
        let mut book = seed_roads_book();
        book.overrides.push(SegmentOverride {
            key: SegmentOverrideKey::Osm { osm_id: 99 },
            role: RoadRole::Jeep,
        });
        assert_eq!(
            book.classify_osm_way(99, "path", None, None, false),
            RoadRole::Jeep
        );
    }

    #[test]
    fn roads_global_fp_changes_on_class_edit() {
        let book = seed_roads_book();
        let base = roads_global_fingerprint(&book);
        let mut edited = book.clone();
        edited.classes[0].role = RoadRole::Jeep;
        assert_ne!(base, roads_global_fingerprint(&edited));
    }

    #[test]
    fn roads_local_fp_ignores_distant_custom() {
        let mut book = seed_roads_book();
        book.customs.push(RoadCustom {
            id: "far-away".into(),
            role: RoadRole::Jeep,
            coords: vec![[35.0, -115.0], [35.01, -115.0]],
        });
        let dest_lat = 39.0;
        let dest_lon = -117.0;
        let fp_near = roads_local_fingerprint(&book, dest_lat, dest_lon, 32_000.0);
        assert_eq!(fp_near, "local-empty");
    }

    #[test]
    fn roads_local_fp_includes_custom_in_disk() {
        let mut book = seed_roads_book();
        book.customs.push(RoadCustom {
            id: "near".into(),
            role: RoadRole::Jeep,
            coords: vec![[39.0, -117.0], [39.01, -117.0]],
        });
        let fp = roads_local_fingerprint(&book, 39.005, -117.0, 32_000.0);
        assert_ne!(fp, "local-empty");
    }
}
