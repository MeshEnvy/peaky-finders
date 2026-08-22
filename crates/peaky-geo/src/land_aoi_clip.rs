//! Clip land layer GeoJSON to the preset AOI union before attribute transforms.

use geo::Geometry;
use geojson::Geometry as GeoJsonGeometry;
use peaky_preset::LandLayerEntry;
use peaky_preset::LandLayerRole;
use serde_json::Value;

use crate::eligible_land::{intersect_land_geometry, land_geometry_is_empty};

fn geometry_to_json_value(geom: &Geometry<f64>) -> Option<Value> {
    let gj: GeoJsonGeometry = geom.try_into().ok()?;
    serde_json::to_value(gj).ok()
}

fn geometry_from_json_value(value: &Value) -> Option<Geometry<f64>> {
    let gj: GeoJsonGeometry = serde_json::from_value(value.clone()).ok()?;
    gj.try_into().ok()
}

pub fn clip_geojson_to_aoi(mut geojson: Value, aoi: &Geometry<f64>) -> Value {
    if land_geometry_is_empty(aoi) {
        return geojson;
    }
    let Some(features) = geojson.get_mut("features").and_then(|v| v.as_array_mut()) else {
        return geojson;
    };

    let mut clipped = Vec::with_capacity(features.len());
    for feat in features.drain(..) {
        let Some(obj) = feat.as_object() else {
            continue;
        };
        let Some(geom_value) = obj.get("geometry") else {
            continue;
        };
        let Some(geom) = geometry_from_json_value(geom_value) else {
            continue;
        };
        let clipped_geom = intersect_land_geometry(geom, aoi);
        if land_geometry_is_empty(&clipped_geom) {
            continue;
        }
        let Some(clipped_geom_value) = geometry_to_json_value(&clipped_geom) else {
            continue;
        };
        let mut out = obj.clone();
        out.insert("geometry".to_string(), clipped_geom_value);
        clipped.push(Value::Object(out));
    }
    *features = clipped;
    geojson
}

pub fn layer_skips_aoi_clip(layer: &LandLayerEntry) -> bool {
    layer.role == Some(LandLayerRole::Aoi)
}

pub fn apply_aoi_clip_to_geojson(
    geojson: Value,
    layer: &LandLayerEntry,
    aoi: Option<&Geometry<f64>>,
) -> Value {
    if layer_skips_aoi_clip(layer) {
        return geojson;
    }
    let Some(aoi_geom) = aoi.filter(|geom| !land_geometry_is_empty(geom)) else {
        return geojson;
    };
    clip_geojson_to_aoi(geojson, aoi_geom)
}

#[cfg(test)]
mod tests {
    use super::*;
    use geo::{LineString, Polygon};

    fn square(min_x: f64, min_y: f64, max_x: f64, max_y: f64) -> Geometry<f64> {
        Geometry::Polygon(Polygon::new(
            LineString::from(vec![
                geo::Coord {
                    x: min_x,
                    y: min_y,
                },
                geo::Coord {
                    x: max_x,
                    y: min_y,
                },
                geo::Coord {
                    x: max_x,
                    y: max_y,
                },
                geo::Coord {
                    x: min_x,
                    y: max_y,
                },
                geo::Coord {
                    x: min_x,
                    y: min_y,
                },
            ]),
            vec![],
        ))
    }

    #[test]
    fn clip_geojson_drops_features_outside_aoi() {
        let aoi = square(-119.5, 39.0, -119.0, 39.5);
        let geojson = serde_json::json!({
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": { "name": "inside" },
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[
                            [-119.4, 39.1],
                            [-119.2, 39.1],
                            [-119.2, 39.2],
                            [-119.4, 39.2],
                            [-119.4, 39.1]
                        ]]
                    }
                },
                {
                    "type": "Feature",
                    "properties": { "name": "outside" },
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[
                            [-120.0, 39.1],
                            [-119.8, 39.1],
                            [-119.8, 39.2],
                            [-120.0, 39.2],
                            [-120.0, 39.1]
                        ]]
                    }
                }
            ]
        });

        let clipped = clip_geojson_to_aoi(geojson, &aoi);
        let features = clipped["features"].as_array().expect("features");
        assert_eq!(features.len(), 1);
        assert_eq!(features[0]["properties"]["name"], "inside");
    }
}
