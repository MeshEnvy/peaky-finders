//! Strip land feature properties and trim coordinate precision.

use anyhow::{Context, Result};
use serde_json::{Map, Value};

use crate::FREEZE_LAYER_KEY_FIELD;

const COORD_DECIMALS: i32 = 6;

fn round_coord(n: f64) -> f64 {
    let factor = 10_f64.powi(COORD_DECIMALS);
    (n * factor).round() / factor
}

fn trim_position_pair(pair: &mut [Value]) {
    if pair.len() >= 2 {
        if let Some(lon) = pair[0].as_f64() {
            pair[0] = Value::from(round_coord(lon));
        }
        if let Some(lat) = pair[1].as_f64() {
            pair[1] = Value::from(round_coord(lat));
        }
    }
}

fn trim_coordinates(value: &mut Value) {
    match value {
        Value::Array(items) => {
            if items.len() >= 2 && items[0].is_number() && items[1].is_number() {
                trim_position_pair(items);
                return;
            }
            for item in items.iter_mut() {
                trim_coordinates(item);
            }
        }
        Value::Object(obj) => {
            if let Some(coords) = obj.get_mut("coordinates") {
                trim_coordinates(coords);
            }
            if let Some(geometries) = obj.get_mut("geometries") {
                trim_coordinates(geometries);
            }
        }
        _ => {}
    }
}

pub fn extract_and_compact_features(
    geojson: &Value,
    layer_key: &str,
    keep_label: bool,
) -> Result<Vec<Value>> {
    let features = geojson
        .get("features")
        .and_then(|v| v.as_array())
        .context("GeoJSON missing features array")?;

    let mut out = Vec::with_capacity(features.len());
    for feat in features {
        let Some(obj) = feat.as_object() else {
            continue;
        };
        let mut feature = obj.clone();
        let mut props = Map::new();
        props.insert(
            FREEZE_LAYER_KEY_FIELD.to_string(),
            Value::String(layer_key.to_string()),
        );
        if keep_label {
            if let Some(label) = obj
                .get("properties")
                .and_then(|p| p.get("label"))
                .and_then(|v| v.as_str())
            {
                props.insert("label".to_string(), Value::String(label.to_string()));
            }
        }
        feature.insert("properties".to_string(), Value::Object(props));
        if let Some(geom) = feature.get_mut("geometry") {
            trim_coordinates(geom);
        }
        out.push(Value::Object(feature));
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn strips_properties_and_trims_coords() {
        let geo = json!({
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": { "SMA_ID": "2", "label": "BLM", "junk": true },
                "geometry": {
                    "type": "Point",
                    "coordinates": [-119.12345678901, 39.98765432109]
                }
            }]
        });
        let feats = extract_and_compact_features(&geo, "blm", true).unwrap();
        assert_eq!(feats.len(), 1);
        let props = feats[0]["properties"].as_object().unwrap();
        assert_eq!(props.get("SMA_ID"), None);
        assert_eq!(props.get("_peaky_layer_key").unwrap(), "blm");
        assert_eq!(props.get("label").unwrap(), "BLM");
        let coords = feats[0]["geometry"]["coordinates"].as_array().unwrap();
        assert_eq!(coords[0].as_f64().unwrap(), -119.123457);
        assert_eq!(coords[1].as_f64().unwrap(), 39.987654);
    }
}
