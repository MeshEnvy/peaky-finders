//! Map preset simulation RF blocks to splatter CovRequest.

use std::collections::HashMap;

use anyhow::{bail, Context, Result};
use peaky_preset::{
    effective_target_raster_dimension, environment_catalog_from_preset, modem_catalog_from_preset,
    Preset, SiteEntry,
};
use serde_yaml::Mapping;
use splatter::CovRequest;
use splatter::hash::LoRaModemParams;

use crate::viewshed_sim::ViewshedSimOverrides;

const LORA_SENSITIVITY_125KHZ_DBM: &[(i64, f64)] = &[
    (7, -123.0),
    (8, -126.0),
    (9, -129.0),
    (10, -132.0),
    (11, -134.5),
    (12, -137.0),
];

fn f(v: &serde_yaml::Value) -> f64 {
    match v {
        serde_yaml::Value::Number(n) => n.as_f64().unwrap_or(0.0),
        serde_yaml::Value::String(s) => s.parse().unwrap_or(0.0),
        _ => 0.0,
    }
}

fn mapping_get<'a>(m: &'a Mapping, key: &str) -> Option<&'a serde_yaml::Value> {
    m.get(&serde_yaml::Value::from(key))
}

fn hash_get<'a>(m: &'a HashMap<String, serde_yaml::Value>, key: &str) -> Option<&'a serde_yaml::Value> {
    m.get(key)
}

fn resolve_catalog_entry(
    selected: &Option<serde_yaml::Value>,
    catalog: &HashMap<String, Mapping>,
    label: &str,
) -> Result<Mapping> {
    let Some(sel) = selected else {
        if catalog.is_empty() {
            return Ok(Mapping::new());
        }
        bail!("preset {label} is unset; set simulation.{label} and define {label}_presets in config.yaml");
    };
    match sel {
        serde_yaml::Value::String(name) => {
            let name = name.trim();
            catalog
                .get(name)
                .cloned()
                .with_context(|| format!("unknown {label} preset {name:?}"))
        }
        serde_yaml::Value::Mapping(m) => {
            if let Some(p) = mapping_get(m, "preset") {
                let name = p.as_str().unwrap_or("").trim();
                let mut base = catalog
                    .get(name)
                    .cloned()
                    .with_context(|| format!("unknown {label} preset {name:?}"))?;
                for (k, v) in m {
                    if k.as_str() == Some("preset") {
                        continue;
                    }
                    base.insert(k.clone(), v.clone());
                }
                Ok(base)
            } else {
                Ok(m.clone())
            }
        }
        _ => bail!("{label} must be a preset name string or mapping"),
    }
}

pub fn resolved_modem(preset: &Preset) -> Result<Mapping> {
    let catalog = modem_catalog_from_preset(preset);
    resolve_catalog_entry(&preset.simulation.modem, &catalog, "modem")
}

pub fn resolved_environment(preset: &Preset) -> Result<Mapping> {
    let mut env = default_environment();
    if let Some(sel) = &preset.simulation.environment {
        let catalog = environment_catalog_from_preset(preset);
        let resolved = resolve_catalog_entry(&Some(sel.clone()), &catalog, "environment")?;
        for (k, v) in resolved {
            env.insert(k, v);
        }
    }
    Ok(env)
}

fn default_environment() -> Mapping {
    let mut m = Mapping::new();
    m.insert(
        serde_yaml::Value::from("climate"),
        serde_yaml::Value::from("continental_temperate"),
    );
    m.insert(
        serde_yaml::Value::from("polarization"),
        serde_yaml::Value::from("vertical"),
    );
    m.insert(
        serde_yaml::Value::from("clutter_height_m"),
        serde_yaml::Value::from(0.0),
    );
    m.insert(
        serde_yaml::Value::from("ground_dielectric_v_m"),
        serde_yaml::Value::from(15.0),
    );
    m.insert(
        serde_yaml::Value::from("ground_conductivity_s_m"),
        serde_yaml::Value::from(0.005),
    );
    m.insert(
        serde_yaml::Value::from("atmosphere_bending_n"),
        serde_yaml::Value::from(301.0),
    );
    m.insert(
        serde_yaml::Value::from("fresnel_clearance_fraction"),
        serde_yaml::Value::from(0.6),
    );
    m.insert(
        serde_yaml::Value::from("coverage_pessimism_db"),
        serde_yaml::Value::from(0.0),
    );
    m.insert(
        serde_yaml::Value::from("situation_pct"),
        serde_yaml::Value::from(95.0),
    );
    m.insert(
        serde_yaml::Value::from("time_pct"),
        serde_yaml::Value::from(95.0),
    );
    m
}

fn lora_sensitivity_dbm(modem: &Mapping) -> Result<f64> {
    let sf = mapping_get(modem, "spreading_factor")
        .and_then(|v| v.as_i64())
        .context("modem.spreading_factor")?;
    let mut sens = LORA_SENSITIVITY_125KHZ_DBM
        .iter()
        .find(|(s, _)| *s == sf)
        .map(|(_, v)| *v)
        .with_context(|| format!("unsupported spreading_factor {sf}"))?;
    let bw = mapping_get(modem, "bandwidth_khz")
        .map(f)
        .unwrap_or(125.0);
    sens += 10.0 * (bw / 125.0).log10();
    let cr = mapping_get(modem, "coding_rate")
        .and_then(|v| v.as_i64())
        .unwrap_or(5);
    if cr < 5 {
        sens -= 1.0;
    } else if cr > 5 {
        sens += 1.0;
    }
    Ok(sens)
}

fn modem_decode_threshold_dbm(modem: &Mapping) -> Result<f64> {
    let impl_m = mapping_get(modem, "implementation_margin_db")
        .map(f)
        .unwrap_or(0.0);
    if let Some(s) = mapping_get(modem, "sensitivity_dbm") {
        return Ok(f(s) + impl_m);
    }
    Ok(lora_sensitivity_dbm(modem)? + impl_m)
}

fn reliability_margin_db(situation_pct: f64, time_pct: f64) -> f64 {
    let z = |p: f64| -> f64 {
        let p = p.clamp(0.01, 99.99) / 100.0;
        if p >= 0.95 {
            1.645
        } else if p >= 0.9 {
            1.282
        } else {
            1.0
        }
    };
    let sigma = 2.375_f64;
    (z(situation_pct).powi(2) + z(time_pct).powi(2)).sqrt() * sigma
}

pub fn default_repeater_tx_height_m(preset: &Preset) -> f64 {
    preset
        .simulation
        .transmitter
        .get("height_m")
        .map(f)
        .unwrap_or(2.0)
        .max(1.0)
}

pub fn resolved_site_tx_height_m(preset: &Preset, site: &SiteEntry) -> f64 {
    if let Some(h) = site.height_m {
        return h.max(1.0);
    }
    default_repeater_tx_height_m(preset)
}

pub fn preset_to_request(
    preset: &Preset,
    lat: f64,
    lon: f64,
    site: Option<&SiteEntry>,
) -> Result<CovRequest> {
    preset_to_request_with_sim(preset, lat, lon, site, None)
}

pub fn preset_to_request_with_sim(
    preset: &Preset,
    lat: f64,
    lon: f64,
    site: Option<&SiteEntry>,
    sim: Option<&ViewshedSimOverrides>,
) -> Result<CovRequest> {
    let mut req = preset_to_request_inner(preset, lat, lon, site)?;
    if let Some(sim) = sim {
        if let Some(radius_km) = sim.radius_km {
            req.radius = radius_km * 1000.0;
        }
        if let Some(raster_dimension) = sim.raster_dimension {
            req.raster_dimension = raster_dimension;
        } else {
            req.raster_dimension =
                effective_target_raster_dimension(preset, sim.radius_km, sim.quality);
        }
    } else {
        req.raster_dimension = effective_target_raster_dimension(preset, None, None);
    }
    Ok(req)
}

fn preset_to_request_inner(
    preset: &Preset,
    lat: f64,
    lon: f64,
    site: Option<&SiteEntry>,
) -> Result<CovRequest> {
    let env = resolved_environment(preset)?;
    let mut modem_map = resolved_modem(preset)?;
    if let Some(s) = preset.simulation.receiver.get("sensitivity_dbm") {
        modem_map.insert(
            serde_yaml::Value::from("sensitivity_dbm"),
            s.clone(),
        );
    }
    let pessimism = mapping_get(&env, "coverage_pessimism_db").map(f).unwrap_or(0.0);
    let impl_key = serde_yaml::Value::from("implementation_margin_db");
    let cur = mapping_get(&modem_map, "implementation_margin_db")
        .map(f)
        .unwrap_or(0.0);
    modem_map.insert(impl_key, serde_yaml::Value::from(cur + pessimism));

    let tx = &preset.simulation.transmitter;
    let rx = &preset.simulation.receiver;
    let tx_power = hash_get(tx, "power_dbm")
        .map(f)
        .or_else(|| hash_get(tx, "power_w").map(|w| {
            let pw = f(w);
            10.0 * pw.log10() + 30.0
        }))
        .or_else(|| mapping_get(&modem_map, "power_dbm").map(f))
        .context("transmitter power_dbm/power_w or modem power_dbm")?;

    let situation = mapping_get(&env, "situation_pct").map(f).unwrap_or(95.0);
    let time_pct = mapping_get(&env, "time_pct").map(f).unwrap_or(95.0);
    let decode = modem_decode_threshold_dbm(&modem_map)?;
    let rel = reliability_margin_db(situation, time_pct);

    let radius_km = match &preset.simulation.radius_km {
        serde_yaml::Value::Number(n) => n.as_f64().unwrap_or(50.0),
        serde_yaml::Value::String(s) => s.parse().unwrap_or(50.0),
        _ => 50.0,
    };

    let tx_height = site
        .map(|s| resolved_site_tx_height_m(preset, s))
        .unwrap_or_else(|| hash_get(tx, "height_m").map(f).unwrap_or(2.0).max(1.0));

    Ok(CovRequest {
        lat,
        lon,
        tx_height,
        tx_power,
        tx_gain: hash_get(tx, "gain_dbi").map(f).unwrap_or(0.0),
        frequency_mhz: mapping_get(&modem_map, "frequency_mhz")
            .or_else(|| hash_get(tx, "frequency_mhz"))
            .map(f)
            .context("frequency_mhz")?,
        rx_height: hash_get(rx, "height_m").map(f).unwrap_or(2.0).max(1.0),
        rx_gain: hash_get(rx, "gain_dbi").map(f).unwrap_or(0.0),
        signal_threshold: decode + rel,
        clutter_height: mapping_get(&env, "clutter_height_m").map(f).unwrap_or(0.0).max(0.0),
        ground_dielectric: mapping_get(&env, "ground_dielectric_v_m").map(f).unwrap_or(15.0),
        ground_conductivity: mapping_get(&env, "ground_conductivity_s_m").map(f).unwrap_or(0.005),
        atmosphere_bending: mapping_get(&env, "atmosphere_bending_n").map(f).unwrap_or(301.0),
        radius: radius_km * 1000.0,
        system_loss: hash_get(tx, "loss_db").map(f).unwrap_or(0.0)
            + hash_get(rx, "loss_db").map(f).unwrap_or(0.0),
        radio_climate: mapping_get(&env, "climate")
            .and_then(|v| v.as_str())
            .unwrap_or("continental_temperate")
            .to_string(),
        polarization: mapping_get(&env, "polarization")
            .and_then(|v| v.as_str())
            .unwrap_or("vertical")
            .to_string(),
        situation_fraction: situation,
        time_fraction: time_pct,
        fresnel_clearance_fraction: mapping_get(&env, "fresnel_clearance_fraction")
            .map(f)
            .unwrap_or(0.6),
        colormap: preset.display.colormap.clone(),
        min_dbm: preset.display.min_dbm,
        max_dbm: preset.display.max_dbm,
        raster_dimension: effective_target_raster_dimension(preset, None, None),
        modem: LoRaModemParams {
            spreading_factor: mapping_get(&modem_map, "spreading_factor")
                .and_then(|v| v.as_i64())
                .unwrap_or(10),
            bandwidth_khz: mapping_get(&modem_map, "bandwidth_khz").map(f).unwrap_or(125.0),
            coding_rate: mapping_get(&modem_map, "coding_rate")
                .and_then(|v| v.as_i64())
                .unwrap_or(5),
            implementation_margin_db: mapping_get(&modem_map, "implementation_margin_db")
                .map(f)
                .unwrap_or(0.0),
            sensitivity_dbm: mapping_get(&modem_map, "sensitivity_dbm").map(f),
        },
    })
}

pub fn viewshed_workspace_digest(req: &CovRequest) -> Result<String> {
    splatter::splat_input_sha256(req).map_err(|e| anyhow::anyhow!(e))
}

/// JSON propagation contract for splatter link evaluation (position is arbitrary).
pub fn rf_json_for_preset(preset: &Preset) -> Result<String> {
    let req = preset_to_request(preset, 0.0, 0.0, None)?;
    Ok(serde_json::to_string(&req)?)
}

pub fn max_hop_range_m(preset: &Preset) -> f64 {
    let radius_km = match &preset.simulation.radius_km {
        serde_yaml::Value::Number(n) => n.as_f64().unwrap_or(50.0),
        serde_yaml::Value::String(s) => s.parse::<f64>().unwrap_or(50.0),
        _ => 50.0,
    };
    radius_km * 1000.0
}

pub fn pair_within_hop_range(
    preset: &Preset,
    lat_a: f64,
    lon_a: f64,
    lat_b: f64,
    lon_b: f64,
) -> bool {
    splatter::propagate::haversine_m(lat_a, lon_a, lat_b, lon_b) <= max_hop_range_m(preset)
}
