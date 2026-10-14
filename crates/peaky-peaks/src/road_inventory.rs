//! OSM + GTLF road inventory for the map "All" debug overlay (not the routing graph).

use std::collections::{HashMap, HashSet};

use geojson::{Feature, FeatureCollection, Geometry, Value as GjValue};
use serde::{Deserialize, Serialize};

use crate::network_geojson::NetworkViewBBox;
use crate::osm::{is_jeep_highway, is_paved_anchor, OsmRoutingOpts, WayTags};

pub const ROAD_INVENTORY_CACHE_VERSION: u32 = 3;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum InventoryRole {
    Paved,
    Jeep,
    Hiking,
    Other,
}

impl InventoryRole {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Paved => "paved",
            Self::Jeep => "jeep",
            Self::Hiking => "hiking",
            Self::Other => "other",
        }
    }

    fn from_u8(v: u8) -> Self {
        match v {
            0 => Self::Paved,
            1 => Self::Jeep,
            2 => Self::Hiking,
            _ => Self::Other,
        }
    }

    fn to_u8(self) -> u8 {
        match self {
            Self::Paved => 0,
            Self::Jeep => 1,
            Self::Hiking => 2,
            Self::Other => 3,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OsmInventoryWay {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub osm_id: Option<i64>,
    pub coords: Vec<(f32, f32)>,
    pub highway: String,
    pub tracktype: Option<String>,
    pub surface: Option<String>,
    pub fourwd_only: bool,
    pub role: InventoryRole,
    pub reason: Option<String>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct OsmRoadInventory {
    pub ways: Vec<OsmInventoryWay>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GtlfInventoryLine {
    pub coords: Vec<(f64, f64)>,
    pub role: InventoryRole,
    pub layer_id: String,
    pub props: HashMap<String, String>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct GtlfRoadInventory {
    pub lines: Vec<GtlfInventoryLine>,
}

/// Classify an OSM highway way for display (same paved/jeep rules as routing).
pub fn classify_osm_inventory_way(
    tags: WayTags<'_>,
    jeep_tags: &HashSet<&str>,
    paved_tags: &HashSet<&str>,
) -> (InventoryRole, Option<String>) {
    if is_paved_anchor(tags, paved_tags) {
        return (InventoryRole::Paved, None);
    }
    if is_jeep_highway(tags, jeep_tags) {
        return (InventoryRole::Jeep, None);
    }
    (InventoryRole::Other, Some(other_reason(tags, jeep_tags, paved_tags)))
}

fn other_reason(
    tags: WayTags<'_>,
    jeep_tags: &HashSet<&str>,
    paved_tags: &HashSet<&str>,
) -> String {
    if tags.highway == "track" {
        if let Some(tt) = tags.tracktype {
            if matches!(tt, "grade1" | "grade2") {
                return tt.to_string();
            }
        }
        if tags.surface == Some("paved") {
            return "surface=paved".into();
        }
    }
    if !jeep_tags.contains(tags.highway) && !paved_tags.contains(tags.highway) {
        return tags.highway.to_string();
    }
    "excluded".into()
}

pub fn classify_osm_inventory_from_opts(
    highway: &str,
    tracktype: Option<&str>,
    surface: Option<&str>,
    fourwd_only: bool,
    opts: &OsmRoutingOpts,
) -> (InventoryRole, Option<String>) {
    let jeep_tags: HashSet<&str> = opts.jeep_highways.iter().map(String::as_str).collect();
    let paved_tags: HashSet<&str> = opts.paved_highways.iter().map(String::as_str).collect();
    classify_osm_inventory_way(
        WayTags {
            highway,
            tracktype,
            surface,
            fourwd_only,
        },
        &jeep_tags,
        &paved_tags,
    )
}

pub fn line_intersects_view(coords: &[(f64, f64)], view: NetworkViewBBox) -> bool {
    for &(lat, lon) in coords {
        if view.contains(lat, lon) {
            return true;
        }
    }
    if coords.len() < 2 {
        return false;
    }
    for w in coords.windows(2) {
        let (lat1, lon1) = w[0];
        let (lat2, lon2) = w[1];
        let min_lat = lat1.min(lat2);
        let max_lat = lat1.max(lat2);
        let min_lon = lon1.min(lon2);
        let max_lon = lon1.max(lon2);
        if max_lon < view.west
            || min_lon > view.east
            || max_lat < view.south
            || min_lat > view.north
        {
            continue;
        }
        return true;
    }
    false
}

fn osm_way_to_feature(way: &OsmInventoryWay) -> Feature {
    let coords: Vec<Vec<f64>> = way
        .coords
        .iter()
        .map(|(lat, lon)| vec![*lon as f64, *lat as f64])
        .collect();
    let mut props = serde_json::Map::new();
    props.insert("role".into(), way.role.as_str().into());
    props.insert("source".into(), "osm".into());
    if let Some(id) = way.osm_id {
        props.insert("osm_id".into(), id.into());
    }
    props.insert("highway".into(), way.highway.clone().into());
    if let Some(tt) = &way.tracktype {
        props.insert("tracktype".into(), tt.clone().into());
    }
    if let Some(s) = &way.surface {
        props.insert("surface".into(), s.clone().into());
    }
    if way.fourwd_only {
        props.insert("fourwd_only".into(), true.into());
    }
    if let Some(r) = &way.reason {
        props.insert("reason".into(), r.clone().into());
    }
    Feature {
        bbox: None,
        geometry: Some(Geometry::new(GjValue::LineString(coords))),
        id: None,
        properties: Some(props),
        foreign_members: None,
    }
}

fn gtlf_line_to_feature(line: &GtlfInventoryLine) -> Feature {
    let coords: Vec<Vec<f64>> = line
        .coords
        .iter()
        .map(|(lat, lon)| vec![*lon, *lat])
        .collect();
    let mut props = serde_json::Map::new();
    props.insert("role".into(), line.role.as_str().into());
    props.insert("source".into(), "gtlf".into());
    props.insert("gtlf_layer".into(), line.layer_id.clone().into());
    props.insert(
        "coord_hash".into(),
        peaky_preset::gtlf_coord_hash(&line.layer_id, &line.coords).into(),
    );
    for (k, v) in &line.props {
        props.insert(format!("gtlf_{k}"), v.clone().into());
    }
    Feature {
        bbox: None,
        geometry: Some(Geometry::new(GjValue::LineString(coords))),
        id: None,
        properties: Some(props),
        foreign_members: None,
    }
}

/// Viewport GeoJSON: one feature per OSM way and GTLF line (no merge/simplify).
pub fn custom_to_feature(custom: &peaky_preset::RoadCustom) -> Feature {
    let coords: Vec<Vec<f64>> = custom
        .coords
        .iter()
        .map(|[lat, lon]| vec![*lon, *lat])
        .collect();
    let mut props = serde_json::Map::new();
    props.insert(
        "role".into(),
        inventory_role_from_road(custom.role).as_str().into(),
    );
    props.insert("source".into(), "custom".into());
    props.insert("custom_id".into(), custom.id.clone().into());
    Feature {
        bbox: None,
        geometry: Some(Geometry::new(GjValue::LineString(coords))),
        id: None,
        properties: Some(props),
        foreign_members: None,
    }
}

pub fn inventory_role_from_road(role: peaky_preset::RoadRole) -> InventoryRole {
    match role {
        peaky_preset::RoadRole::Paved => InventoryRole::Paved,
        peaky_preset::RoadRole::Jeep => InventoryRole::Jeep,
        peaky_preset::RoadRole::Hiking => InventoryRole::Hiking,
        peaky_preset::RoadRole::Other => InventoryRole::Other,
    }
}

pub fn inventory_all_geojson(
    osm: &OsmRoadInventory,
    gtlf: &GtlfRoadInventory,
    customs: &[peaky_preset::RoadCustom],
    view: NetworkViewBBox,
) -> FeatureCollection {
    let mut features = Vec::new();
    for way in &osm.ways {
        let coords: Vec<(f64, f64)> = way
            .coords
            .iter()
            .map(|(lat, lon)| (*lat as f64, *lon as f64))
            .collect();
        if line_intersects_view(&coords, view) {
            features.push(osm_way_to_feature(way));
        }
    }
    for line in &gtlf.lines {
        if line_intersects_view(&line.coords, view) {
            features.push(gtlf_line_to_feature(line));
        }
    }
    for custom in customs {
        let coords: Vec<(f64, f64)> = custom.coords.iter().map(|[lat, lon]| (*lat, *lon)).collect();
        if line_intersects_view(&coords, view) {
            features.push(custom_to_feature(custom));
        }
    }
    FeatureCollection {
        bbox: None,
        features,
        foreign_members: None,
    }
}

pub fn inventory_all_geojson_bytes(
    osm: &OsmRoadInventory,
    gtlf: &GtlfRoadInventory,
    customs: &[peaky_preset::RoadCustom],
    view: NetworkViewBBox,
) -> serde_json::Result<Vec<u8>> {
    serde_json::to_vec(&inventory_all_geojson(osm, gtlf, customs, view))
}

#[derive(Serialize, Deserialize)]
pub(crate) struct RoadInventoryDisk {
    pub version: u32,
    pub pbf_len: u64,
    pub pbf_mtime: u64,
    pub west: f64,
    pub south: f64,
    pub east: f64,
    pub north: f64,
    pub jeep_highways: Vec<String>,
    pub paved_highways: Vec<String>,
    pub ways: Vec<OsmInventoryWayDisk>,
}

#[derive(Serialize, Deserialize)]
struct OsmInventoryWayDisk {
    #[serde(default)]
    osm_id: Option<i64>,
    lat: Vec<f32>,
    lon: Vec<f32>,
    highway: String,
    tracktype: Option<String>,
    surface: Option<String>,
    fourwd_only: bool,
    role: u8,
    reason: Option<String>,
}

impl From<&OsmInventoryWay> for OsmInventoryWayDisk {
    fn from(w: &OsmInventoryWay) -> Self {
        let mut lat = Vec::with_capacity(w.coords.len());
        let mut lon = Vec::with_capacity(w.coords.len());
        for (la, lo) in &w.coords {
            lat.push(*la);
            lon.push(*lo);
        }
        Self {
            osm_id: w.osm_id,
            lat,
            lon,
            highway: w.highway.clone(),
            tracktype: w.tracktype.clone(),
            surface: w.surface.clone(),
            fourwd_only: w.fourwd_only,
            role: w.role.to_u8(),
            reason: w.reason.clone(),
        }
    }
}

impl From<OsmInventoryWayDisk> for OsmInventoryWay {
    fn from(d: OsmInventoryWayDisk) -> Self {
        let coords = d
            .lat
            .into_iter()
            .zip(d.lon)
            .map(|(lat, lon)| (lat, lon))
            .collect();
        Self {
            osm_id: d.osm_id,
            coords,
            highway: d.highway,
            tracktype: d.tracktype,
            surface: d.surface,
            fourwd_only: d.fourwd_only,
            role: InventoryRole::from_u8(d.role),
            reason: d.reason,
        }
    }
}

pub(crate) fn inventory_to_disk(
    pbf_len: u64,
    pbf_mtime: u64,
    west: f64,
    south: f64,
    east: f64,
    north: f64,
    opts: &OsmRoutingOpts,
    inventory: &OsmRoadInventory,
) -> RoadInventoryDisk {
    RoadInventoryDisk {
        version: ROAD_INVENTORY_CACHE_VERSION,
        pbf_len,
        pbf_mtime,
        west,
        south,
        east,
        north,
        jeep_highways: opts.jeep_highways.clone(),
        paved_highways: opts.paved_highways.clone(),
        ways: inventory.ways.iter().map(OsmInventoryWayDisk::from).collect(),
    }
}

pub(crate) fn inventory_from_disk(disk: RoadInventoryDisk) -> OsmRoadInventory {
    OsmRoadInventory {
        ways: disk.ways.into_iter().map(OsmInventoryWay::from).collect(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use peaky_preset::{RoadRole, seed_roads_book};

    #[test]
    fn grade2_track_is_other_not_in_drive_graph() {
        let book = seed_roads_book();
        assert_eq!(
            book.classify_osm_way(1, "track", Some("grade2"), None, false),
            RoadRole::Other
        );
    }

    #[test]
    fn highway_road_is_other_with_reason() {
        let book = seed_roads_book();
        assert_eq!(
            book.classify_osm_way(1, "road", None, None, false),
            RoadRole::Other
        );
    }

    #[test]
    fn unclassified_dirt_is_jeep() {
        let book = seed_roads_book();
        assert_eq!(
            book.classify_osm_way(1, "unclassified", None, Some("dirt"), false),
            RoadRole::Jeep
        );
    }

    #[test]
    fn primary_is_paved() {
        let book = seed_roads_book();
        assert_eq!(
            book.classify_osm_way(1, "primary", None, None, false),
            RoadRole::Paved
        );
    }

    #[test]
    fn gtlf_foot_line_role_hiking() {
        let line = GtlfInventoryLine {
            coords: vec![(39.0, -119.0), (39.01, -119.0)],
            role: InventoryRole::Hiking,
            layer_id: "4".into(),
            props: HashMap::new(),
        };
        assert_eq!(line.role, InventoryRole::Hiking);
    }

    #[test]
    fn drive_graph_excludes_grade2_track() {
        let book = seed_roads_book();
        assert_ne!(
            book.classify_osm_way(1, "track", Some("grade2"), None, false),
            RoadRole::Jeep
        );
    }
}
