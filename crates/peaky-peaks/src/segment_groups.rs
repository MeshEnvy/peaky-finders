//! Canonical road segment groups (one OSM way, GTLF line, or custom polyline).

use geojson::{Feature, FeatureCollection, Geometry, Value as GjValue};
use peaky_preset::RoadRole;

use crate::network_geojson::NetworkViewBBox;
use crate::road_inventory::line_intersects_view;
use crate::road_inventory::{custom_to_feature, inventory_role_from_road, InventoryRole};

#[derive(Debug, Clone)]
pub enum SegmentGroupKey {
    Osm { osm_id: i64 },
    Gtlf {
        layer_id: String,
        coord_hash: String,
    },
    Custom { id: String },
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
                coords: group
                    .coords
                    .iter()
                    .map(|(lat, lon)| [*lat, *lon])
                    .collect(),
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
