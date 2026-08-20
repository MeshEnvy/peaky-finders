//! Project and home simulation API payloads.

use std::collections::HashMap;
use std::path::Path;

use peaky_preset::{
    effective_target_raster_dimension, load_environment_catalog, load_home_simulation,
    load_modem_catalog, load_preset, load_preset_raw, SimulationConfig, VIEWSHED_QUALITY_MAX,
    VIEWSHED_QUALITY_MIN, SKADI_DEM_SPACING_M,
};
use serde_json::{json, Value};
use serde_yaml::Mapping;

const MIN_SERVE_RADIUS_KM: f64 = 1.0;
const MAX_SERVE_RADIUS_KM: f64 = 100.0;

fn yaml_to_json(value: &serde_yaml::Value) -> Value {
    serde_json::to_value(value).unwrap_or(Value::Null)
}

fn mapping_to_json(m: &Mapping) -> Value {
    yaml_to_json(&serde_yaml::Value::Mapping(m.clone()))
}

pub fn serialize_simulation(sim: &SimulationConfig) -> Value {
    let radius_km = match &sim.radius_km {
        serde_yaml::Value::Number(n) => n.as_f64().unwrap_or(50.0),
        serde_yaml::Value::String(s) => s.parse().unwrap_or(50.0),
        _ => 50.0,
    };
    let preset = peaky_preset::Preset {
        simulation: sim.clone(),
        ..Default::default()
    };
    let raster_dimension = effective_target_raster_dimension(&preset, None, None);
    json!({
        "modem": sim.modem.as_ref().map(yaml_to_json),
        "environment": sim.environment.as_ref().map(yaml_to_json),
        "radius_km": radius_km,
        "viewshed_quality": sim.viewshed_quality,
        "raster_dimension": raster_dimension,
        "transmitter": sim.transmitter.iter().map(|(k, v)| (k.clone(), yaml_to_json(v))).collect::<HashMap<_, _>>(),
        "receiver": sim.receiver.iter().map(|(k, v)| (k.clone(), yaml_to_json(v))).collect::<HashMap<_, _>>(),
        "max_workers": { "splatter": sim.max_workers.splatter },
    })
}

fn simulation_leaf_paths(raw: &Value, prefix: &str) -> Vec<String> {
    let Some(obj) = raw.as_object() else {
        return Vec::new();
    };
    let mut paths = Vec::new();
    for (key, val) in obj {
        let path = if prefix.is_empty() {
            key.clone()
        } else {
            format!("{prefix}.{key}")
        };
        if val.is_object() && !val.as_object().unwrap().is_empty() {
            paths.extend(simulation_leaf_paths(val, &path));
        } else if key != "raster_dimension" {
            paths.push(path);
        }
    }
    paths
}

pub fn home_simulation_payload() -> Value {
    let sim = load_home_simulation().unwrap_or_default();
    let modems = load_modem_catalog().unwrap_or_default();
    let environments = load_environment_catalog().unwrap_or_default();
    json!({
        "simulation": mapping_to_json(&sim),
        "modem_names": modems.keys().collect::<Vec<_>>(),
        "environment_names": environments.keys().collect::<Vec<_>>(),
    })
}

pub fn project_simulation_payload(preset_path: &Path) -> Result<Value, String> {
    let preset = load_preset(preset_path).map_err(|e| e.to_string())?;
    let home_raw = load_home_simulation().unwrap_or_default();
    let defaults = if home_raw.is_empty() {
        serialize_simulation(&SimulationConfig::default())
    } else {
        mapping_to_json(&home_raw)
    };
    let effective = serialize_simulation(&preset.simulation);
    let project_doc = load_preset_raw(preset_path).map_err(|e| e.to_string())?;
    let project_sim = project_doc
        .get("simulation")
        .map(yaml_to_json)
        .unwrap_or(json!({}));
    let overrides = simulation_leaf_paths(&project_sim, "");
    let modems = load_modem_catalog().unwrap_or_default();
    let environments = load_environment_catalog().unwrap_or_default();
    Ok(json!({
        "simulation": effective,
        "defaults": defaults,
        "overrides": overrides,
        "radius_km_min": MIN_SERVE_RADIUS_KM,
        "radius_km_max": MAX_SERVE_RADIUS_KM,
        "viewshed_quality_min": VIEWSHED_QUALITY_MIN,
        "viewshed_quality_max": VIEWSHED_QUALITY_MAX,
        "dem_spacing_m": SKADI_DEM_SPACING_M,
        "modem_names": modems.keys().collect::<Vec<_>>(),
        "environment_names": environments.keys().collect::<Vec<_>>(),
    }))
}
