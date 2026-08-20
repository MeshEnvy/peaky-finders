//! Attribute filters for land layer GeoJSON features.

use peaky_preset::LandAttributeFilter;
use serde_json::{Map, Value};

pub fn json_value_as_compare_string(value: &Value) -> Option<String> {
    match value {
        Value::Null => None,
        Value::Bool(b) => Some(b.to_string()),
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                Some(i.to_string())
            } else if let Some(f) = n.as_f64() {
                Some(f.to_string())
            } else {
                Some(n.to_string())
            }
        }
        Value::String(s) => {
            let trimmed = s.trim();
            if trimmed.is_empty() {
                None
            } else {
                Some(trimmed.to_string())
            }
        }
        other => {
            let s = other.to_string();
            if s.is_empty() {
                None
            } else {
                Some(s)
            }
        }
    }
}

pub fn property_matches_filter(props: &Map<String, Value>, field: &str, values: &[String]) -> bool {
    let Some(raw) = props.get(field) else {
        return false;
    };
    let Some(actual) = json_value_as_compare_string(raw) else {
        return false;
    };
    values.iter().any(|expected| expected == &actual)
}

pub fn matches_land_attribute_filters(
    props: &Map<String, Value>,
    include: &[LandAttributeFilter],
    exclude: &[LandAttributeFilter],
) -> bool {
    for filt in include {
        if !property_matches_filter(props, &filt.field, &filt.values) {
            return false;
        }
    }
    for filt in exclude {
        for value in &filt.values {
            if property_matches_filter(props, &filt.field, std::slice::from_ref(value)) {
                return false;
            }
        }
    }
    true
}

pub fn json_safe_properties(props: &Map<String, Value>) -> Map<String, Value> {
    let mut out = Map::new();
    for (key, value) in props {
        if key == "geometry" {
            continue;
        }
        if let Some(safe) = json_safe_value(value) {
            out.insert(key.clone(), safe);
        }
    }
    out
}

fn json_safe_value(value: &Value) -> Option<Value> {
    match value {
        Value::Null => None,
        Value::Bool(b) => Some(Value::Bool(*b)),
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                Some(Value::Number(i.into()))
            } else if let Some(f) = n.as_f64() {
                serde_json::Number::from_f64(f).map(Value::Number)
            } else {
                Some(Value::Number(n.clone()))
            }
        }
        Value::String(s) => {
            let trimmed = s.trim();
            if trimmed.is_empty() {
                None
            } else {
                Some(Value::String(trimmed.to_string()))
            }
        }
        other => Some(Value::String(other.to_string())),
    }
}
