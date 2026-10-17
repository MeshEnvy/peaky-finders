//! Canonical road segment groups (one OSM way, GTLF line, or custom polyline).

use geojson::{Feature, FeatureCollection, Geometry, Value as GjValue};
use peaky_preset::RoadRole;

use crate::network_geojson::NetworkViewBBox;
use crate::road_inventory::line_intersects_view;
use crate::road_inventory::{custom_to_feature, inventory_role_from_road, InventoryRole};

#[derive(Debug, Clone)]
pub enum SegmentGroupKey {
    Osm {
        osm_id: i64,
    },
    Gtlf {
        layer_id: String,
        coord_hash: String,
    },
    Custom {
        id: String,
    },
}

#[derive(Debug, Clone)]
pub struct SegmentGroupTags {
    pub highway: String,
    pub tracktype: Option<String>,
    pub surface: Option<String>,
    pub fourwd_only: bool,
}

#[derive(Debug, Clone)]
pub struct RoadSegmentGroup {
    pub key: SegmentGroupKey,
    pub coords: Vec<(f64, f64)>,
    pub tags: SegmentGroupTags,
    pub role: RoadRole,
    pub access: String,
    pub reason: Option<String>,
}

impl RoadSegmentGroup {
    pub fn inventory_role(&self) -> InventoryRole {
        inventory_role_from_road(self.role)
    }

    pub fn is_custom_drive(&self) -> bool {
        matches!(self.key, SegmentGroupKey::Custom { .. })
            && matches!(self.role, RoadRole::Paved | RoadRole::Jeep)
    }

    /// Source key from the segment id (`osm`, `blm`, `usfs`, `custom`).
    pub fn source_name(&self) -> String {
        match &self.key {
            SegmentGroupKey::Osm { .. } => "osm".to_string(),
            SegmentGroupKey::Gtlf { layer_id, .. } => layer_id.clone(),
            SegmentGroupKey::Custom { id } => id
                .split(':')
                .next()
                .filter(|s| !s.is_empty())
                .unwrap_or("custom")
                .to_string(),
        }
    }

    /// Segment id as stored by the source plugin.
    pub fn segment_id(&self) -> String {
        match &self.key {
            SegmentGroupKey::Osm { osm_id } => format!("osm:{osm_id}"),
            SegmentGroupKey::Gtlf {
                layer_id,
                coord_hash,
            } => format!("{layer_id}:{coord_hash}"),
            SegmentGroupKey::Custom { id } => id.clone(),
        }
    }
}

pub fn group_to_feature(group: &RoadSegmentGroup) -> Feature {
    match &group.key {
        SegmentGroupKey::Osm { osm_id } => {
            let coords: Vec<Vec<f64>> = group
                .coords
                .iter()
                .map(|(lat, lon)| vec![*lon, *lat])
                .collect();
            let mut props = serde_json::Map::new();
            props.insert("role".into(), group.inventory_role().as_str().into());
            props.insert("source".into(), "osm".into());
            props.insert("osm_id".into(), (*osm_id).into());
            props.insert("highway".into(), group.tags.highway.clone().into());
            if let Some(tt) = &group.tags.tracktype {
                props.insert("tracktype".into(), tt.clone().into());
            }
            if let Some(s) = &group.tags.surface {
                props.insert("surface".into(), s.clone().into());
            }
            if group.tags.fourwd_only {
                props.insert("fourwd_only".into(), true.into());
            }
            if let Some(r) = &group.reason {
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
        SegmentGroupKey::Gtlf {
            layer_id,
            coord_hash,
        } => {
            let coords: Vec<Vec<f64>> = group
                .coords
                .iter()
                .map(|(lat, lon)| vec![*lon, *lat])
                .collect();
            let mut props = serde_json::Map::new();
            props.insert("role".into(), group.inventory_role().as_str().into());
            props.insert("source".into(), "gtlf".into());
            props.insert("gtlf_layer".into(), layer_id.clone().into());
            props.insert("coord_hash".into(), coord_hash.clone().into());
            props.insert("highway".into(), group.tags.highway.clone().into());
            Feature {
                bbox: None,
                geometry: Some(Geometry::new(GjValue::LineString(coords))),
                id: None,
                properties: Some(props),
                foreign_members: None,
            }
        }
        SegmentGroupKey::Custom { id } => {
            let mut f = custom_to_feature(&peaky_preset::RoadCustom {
                id: id.clone(),
                role: group.role,
                coords: group.coords.iter().map(|(lat, lon)| [*lat, *lon]).collect(),
            });
            if let Some(props) = f.properties.as_mut() {
                props.insert("highway".into(), group.tags.highway.clone().into());
            }
            f
        }
    }
}

pub fn groups_all_geojson(groups: &[RoadSegmentGroup], view: NetworkViewBBox) -> FeatureCollection {
    let mut features = Vec::new();
    for group in groups {
        if line_intersects_view(&group.coords, view) {
            features.push(group_to_feature(group));
        }
    }
    FeatureCollection {
        bbox: None,
        features,
        foreign_members: None,
    }
}

pub fn groups_all_geojson_bytes(
    groups: &[RoadSegmentGroup],
    view: NetworkViewBBox,
) -> serde_json::Result<Vec<u8>> {
    serde_json::to_vec(&groups_all_geojson(groups, view))
}

fn line_length_m(coords: &[(f64, f64)]) -> f64 {
    coords.windows(2).map(|w| haversine_m(w[0], w[1])).sum()
}

fn haversine_m(a: (f64, f64), b: (f64, f64)) -> f64 {
    let (lat1, lon1) = a;
    let (lat2, lon2) = b;
    let r = 6_371_000.0;
    let dlat = (lat2 - lat1).to_radians();
    let dlon = (lon2 - lon1).to_radians();
    let h = (dlat / 2.0).sin().powi(2)
        + lat1.to_radians().cos() * lat2.to_radians().cos() * (dlon / 2.0).sin().powi(2);
    r * 2.0 * h.sqrt().asin()
}

fn grade_label(tracktype: &Option<String>) -> String {
    match tracktype {
        Some(value) if !value.is_empty() => value
            .strip_prefix("grade")
            .filter(|rest| !rest.is_empty())
            .unwrap_or(value)
            .to_string(),
        _ => "unknown".to_string(),
    }
}

fn role_feature(group: &RoadSegmentGroup) -> Feature {
    let coords: Vec<Vec<f64>> = group
        .coords
        .iter()
        .map(|(lat, lon)| vec![*lon, *lat])
        .collect();
    let mut props = serde_json::Map::new();
    props.insert("id".into(), group.segment_id().into());
    props.insert("role".into(), group.role.as_str().into());
    props.insert("source".into(), group.source_name().into());
    props.insert("kind".into(), group.tags.highway.clone().into());
    props.insert(
        "surface".into(),
        group
            .tags
            .surface
            .clone()
            .filter(|s| !s.is_empty())
            .unwrap_or_else(|| "unknown".into())
            .into(),
    );
    props.insert("grade".into(), grade_label(&group.tags.tracktype).into());
    props.insert(
        "access".into(),
        if group.access.is_empty() {
            "unknown".into()
        } else {
            group.access.clone().into()
        },
    );
    if let Some(reason) = &group.reason {
        props.insert("reason".into(), reason.clone().into());
    }
    Feature {
        bbox: None,
        geometry: Some(Geometry::new(GjValue::LineString(coords))),
        id: None,
        properties: Some(props),
        foreign_members: None,
    }
}

/// One role from the master graph. Full zoom is one feature per segment.
/// Farther out, the lines share one feature and carry no segment id.
pub fn role_layer_geojson(
    groups: &[RoadSegmentGroup],
    role: RoadRole,
    view: NetworkViewBBox,
    detailed: bool,
    min_m: f64,
) -> FeatureCollection {
    let mut lines: Vec<Vec<Vec<f64>>> = Vec::new();
    let mut features = Vec::new();
    for group in groups {
        if group.role != role || group.coords.len() < 2 {
            continue;
        }
        if !line_intersects_view(&group.coords, view) {
            continue;
        }
        if min_m > 0.0 && line_length_m(&group.coords) < min_m {
            continue;
        }
        if detailed {
            features.push(role_feature(group));
        } else {
            lines.push(
                group
                    .coords
                    .iter()
                    .map(|(lat, lon)| vec![*lon, *lat])
                    .collect(),
            );
        }
    }
    if !detailed {
        if lines.is_empty() {
            return FeatureCollection {
                bbox: None,
                features: Vec::new(),
                foreign_members: None,
            };
        }
        features.push(Feature {
            bbox: None,
            geometry: Some(Geometry::new(GjValue::MultiLineString(lines))),
            id: None,
            properties: None,
            foreign_members: None,
        });
    }
    FeatureCollection {
        bbox: None,
        features,
        foreign_members: None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn full_zoom_feature_carries_segment_id() {
        let group = RoadSegmentGroup {
            key: SegmentGroupKey::Osm { osm_id: 14379428 },
            coords: vec![(40.0, -119.7), (40.01, -119.71)],
            tags: SegmentGroupTags {
                highway: "track".into(),
                tracktype: None,
                surface: None,
                fourwd_only: false,
            },
            role: RoadRole::Jeep,
            access: "unknown".into(),
            reason: None,
        };
        let view = NetworkViewBBox {
            west: -119.8,
            south: 39.9,
            east: -119.6,
            north: 40.1,
        };
        let fc = role_layer_geojson(&[group], RoadRole::Jeep, view, true, 0.0);
        let props = fc.features[0].properties.as_ref().expect("props");
        assert_eq!(props.get("id").and_then(|v| v.as_str()), Some("osm:14379428"));
        assert_eq!(props.get("role").and_then(|v| v.as_str()), Some("jeep"));
        assert_eq!(props.get("kind").and_then(|v| v.as_str()), Some("track"));
        assert_eq!(props.get("access").and_then(|v| v.as_str()), Some("unknown"));
    }
}
