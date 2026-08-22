//! Project and home simulation API payloads.

use std::collections::HashMap;
use std::path::Path;

use peaky_preset::{
    effective_target_raster_dimension, environment_catalog_from_preset, load_preset,
    modem_catalog_from_preset, SimulationConfig, VIEWSHED_QUALITY_MAX, VIEWSHED_QUALITY_MIN,
    SKADI_DEM_SPACING_M,
};
use serde_json::{json, Value};

const MIN_SERVE_RADIUS_KM: f64 = 1.0;
const MAX_SERVE_RADIUS_KM: f64 = 100.0;

fn yaml_to_json(value: &serde_yaml::Value) -> Value {
    serde_json::to_value(value).unwrap_or(Value::Null)
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
        "max_workers": {
            "coverage": sim.max_workers.coverage,
            "dem": sim.max_workers.dem,
        },
    })
}

pub fn project_simulation_payload(preset_path: &Path) -> Result<Value, String> {
    let preset = load_preset(preset_path).map_err(|e| e.to_string())?;
    let effective = serialize_simulation(&preset.simulation);
    let modems = modem_catalog_from_preset(&preset);
    let environments = environment_catalog_from_preset(&preset);
    Ok(json!({
        "simulation": effective,
        "radius_km_min": MIN_SERVE_RADIUS_KM,
        "radius_km_max": MAX_SERVE_RADIUS_KM,
        "viewshed_quality_min": VIEWSHED_QUALITY_MIN,
        "viewshed_quality_max": VIEWSHED_QUALITY_MAX,
        "dem_spacing_m": SKADI_DEM_SPACING_M,
        "modem_names": modems.keys().collect::<Vec<_>>(),
        "environment_names": environments.keys().collect::<Vec<_>>(),
    }))
}
