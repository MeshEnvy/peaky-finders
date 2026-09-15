//! Map preset simulation RF blocks to splatter CovRequest.

use std::collections::HashMap;

use anyhow::{bail, Context, Result};
use peaky_preset::{
    effective_target_raster_dimension, environment_catalog_from_preset, modem_catalog_from_preset,
    preset_radius_km, site_viewshed_radius_km, BoardViewshedResolver, Preset, SiteEntry,
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

pub fn default_candidate_tx_height(preset: &Preset, lat: f64, lon: f64) -> f64 {
    preset_to_request(preset, lat, lon, None)
        .map(|req| req.tx_height.max(1.0))
        .unwrap_or_else(|_| default_repeater_tx_height_m(preset).max(1.0))
}

pub fn preset_to_request(
    preset: &Preset,
    lat: f64,
    lon: f64,
    site: Option<&SiteEntry>,
) -> Result<CovRequest> {
    preset_to_request_with_sim(preset, lat, lon, site, None, None)
}

pub fn preset_to_request_with_sim(
    preset: &Preset,
    lat: f64,
    lon: f64,
    site: Option<&SiteEntry>,
    sim: Option<&ViewshedSimOverrides>,
    board_viewshed: Option<&BoardViewshedResolver>,
) -> Result<CovRequest> {
    let mut req = preset_to_request_inner(preset, lat, lon, site, board_viewshed)?;
    let site_radius_km = site.map(|s| site_viewshed_radius_km(preset, s, board_viewshed));
    if let Some(sim) = sim {
        if let Some(radius_km) = sim.radius_km {
            req.radius = radius_km * 1000.0;
        }
        if let Some(raster_dimension) = sim.raster_dimension {
            req.raster_dimension = raster_dimension;
        } else {
            let radius_for_raster = sim.radius_km.or(site_radius_km);
            req.raster_dimension =
                effective_target_raster_dimension(preset, radius_for_raster, sim.quality);
        }
    } else {
        req.raster_dimension =
            effective_target_raster_dimension(preset, site_radius_km, None);
    }
    Ok(req)
}

fn preset_to_request_inner(
    preset: &Preset,
    lat: f64,
    lon: f64,
    site: Option<&SiteEntry>,
    board_viewshed: Option<&BoardViewshedResolver>,
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

    let radius_km = site
        .map(|s| site_viewshed_radius_km(preset, s, board_viewshed))
        .unwrap_or_else(|| preset_radius_km(preset));

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
        raster_dimension: effective_target_raster_dimension(preset, Some(radius_km), None),
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
    preset_radius_km(preset) * 1000.0
}

/// Per-site hop radius (same as viewshed): board override when staked, else project default.
pub fn site_hop_radius_km(
    preset: &Preset,
    site: &SiteEntry,
    boards: Option<&BoardViewshedResolver>,
) -> f64 {
    site_viewshed_radius_km(preset, site, boards)
}

pub fn site_hop_radius_m(
    preset: &Preset,
    site: &SiteEntry,
    boards: Option<&BoardViewshedResolver>,
) -> f64 {
    site_hop_radius_km(preset, site, boards) * 1000.0
}

/// Draft / unbound placement: min(project hop, staked peer board cap).
pub fn draft_hop_radius_m(
    preset: &Preset,
    site: &SiteEntry,
    boards: Option<&BoardViewshedResolver>,
) -> f64 {
    preset_radius_km(preset)
        .min(site_hop_radius_km(preset, site, boards))
        * 1000.0
}

pub fn pair_hop_range_km(
    preset: &Preset,
    site_a: &SiteEntry,
    site_b: &SiteEntry,
    boards: Option<&BoardViewshedResolver>,
) -> f64 {
    site_hop_radius_km(preset, site_a, boards)
        .min(site_hop_radius_km(preset, site_b, boards))
}

pub fn pair_hop_range_m(
    preset: &Preset,
    site_a: &SiteEntry,
    site_b: &SiteEntry,
    boards: Option<&BoardViewshedResolver>,
) -> f64 {
    pair_hop_range_km(preset, site_a, site_b, boards) * 1000.0
}

pub fn pair_within_hop_range_sites(
    preset: &Preset,
    site_a: &SiteEntry,
    site_b: &SiteEntry,
    boards: Option<&BoardViewshedResolver>,
) -> bool {
    splatter::propagate::haversine_m(
        site_a.loc[0],
        site_a.loc[1],
        site_b.loc[0],
        site_b.loc[1],
    ) <= pair_hop_range_m(preset, site_a, site_b, boards)
}

pub fn pair_within_hop_range_coords_to_site(
    preset: &Preset,
    lat: f64,
    lon: f64,
    site: &SiteEntry,
    boards: Option<&BoardViewshedResolver>,
) -> bool {
    splatter::propagate::haversine_m(lat, lon, site.loc[0], site.loc[1])
        <= draft_hop_radius_m(preset, site, boards)
}

/// Smallest hop among sites matching ``allow_tags``; project ceiling when none match.
pub fn min_booked_hop_range_m(
    preset: &Preset,
    allow_tags: &[String],
    boards: Option<&BoardViewshedResolver>,
) -> f64 {
    use std::collections::HashSet;
    let allow: HashSet<&str> = allow_tags.iter().map(String::as_str).collect();
    let mut min_km = preset_radius_km(preset);
    let mut found = false;
    for site in preset.sites.values() {
        if !site.tags.iter().any(|t| allow.contains(t.as_str())) {
            continue;
        }
        found = true;
        min_km = min_km.min(site_hop_radius_km(preset, site, boards));
    }
    if found {
        min_km * 1000.0
    } else {
        max_hop_range_m(preset)
    }
}

pub fn load_board_viewshed(preset_path: &std::path::Path) -> BoardViewshedResolver {
    let project_dir = preset_path
        .parent()
        .filter(|p| p.is_dir())
        .unwrap_or(preset_path);
    BoardViewshedResolver::load(project_dir).unwrap_or_default()
}

#[cfg(test)]
mod hop_range_tests {
    use super::*;
    use peaky_preset::{BoardViewshedResolver, SiteEntry};

    fn t096_preset() -> Preset {
        let mut preset = Preset::default();
        preset.simulation.radius_km = serde_yaml::Value::from(72.0);
        preset
    }

    fn t096_boards(dir: &tempfile::TempDir) -> BoardViewshedResolver {
        std::fs::write(
            dir.path().join("boards.yaml"),
            "boards:\n  heltec-t096:\n    radius_km: 50\n",
        )
        .unwrap();
        std::fs::write(
            dir.path().join("nodes.yaml"),
            "nodes:\n  me0048:\n    board: heltec-t096\n",
        )
        .unwrap();
        BoardViewshedResolver::load(dir.path()).unwrap()
    }

    fn t096_site() -> SiteEntry {
        SiteEntry {
            name: "T096".into(),
            loc: [36.87, -116.68],
            height_m: None,
            description: None,
            tags: vec![],
            node: Some("me0048".into()),
        }
    }

    fn rak_site() -> SiteEntry {
        SiteEntry {
            name: "RAK".into(),
            loc: [39.0, -119.0],
            height_m: None,
            description: None,
            tags: vec![],
            node: Some("me0001".into()),
        }
    }

    #[test]
    fn t096_pair_715km_out_of_range() {
        let dir = tempfile::tempdir().unwrap();
        let preset = t096_preset();
        let boards = t096_boards(&dir);
        let a = SiteEntry {
            loc: [36.87133, -116.683758],
            ..t096_site()
        };
        let b = SiteEntry {
            loc: [36.46147, -116.06689],
            ..t096_site()
        };
        assert!(!pair_within_hop_range_sites(
            &preset,
            &a,
            &b,
            Some(&boards)
        ));
    }

    #[test]
    fn t096_pair_46km_in_range() {
        let dir = tempfile::tempdir().unwrap();
        let preset = t096_preset();
        let boards = t096_boards(&dir);
        let a = SiteEntry {
            loc: [36.87133, -116.683758],
            ..t096_site()
        };
        let b = SiteEntry {
            loc: [36.74388, -116.45068],
            ..t096_site()
        };
        assert!(pair_within_hop_range_sites(
            &preset,
            &a,
            &b,
            Some(&boards)
        ));
    }

    #[test]
    fn t096_rak_60km_out_of_range() {
        let dir = tempfile::tempdir().unwrap();
        let preset = t096_preset();
        let boards = t096_boards(&dir);
        let a = t096_site();
        let b = rak_site();
        let dist = splatter::propagate::haversine_m(a.loc[0], a.loc[1], b.loc[0], b.loc[1]);
        if dist <= 60_000.0 {
            return;
        }
        let b_near = SiteEntry {
            loc: [a.loc[0] + 0.45, a.loc[1] + 0.45],
            ..rak_site()
        };
        assert!(!pair_within_hop_range_sites(
            &preset,
            &a,
            &b_near,
            Some(&boards)
        ));
    }

    #[test]
    fn rak_pair_60km_in_range() {
        let preset = t096_preset();
        let a = rak_site();
        let b = SiteEntry {
            loc: [a.loc[0] + 0.54, a.loc[1]],
            ..rak_site()
        };
        let dist = splatter::propagate::haversine_m(a.loc[0], a.loc[1], b.loc[0], b.loc[1]);
        assert!(
            (55_000.0..=65_000.0).contains(&dist),
            "fixture dist {dist} m should be ~60 km"
        );
        assert!(pair_within_hop_range_sites(&preset, &a, &b, None));
    }

    #[test]
    fn draft_to_t096_60km_out_of_range() {
        let dir = tempfile::tempdir().unwrap();
        let preset = t096_preset();
        let boards = t096_boards(&dir);
        let site = t096_site();
        let lat = site.loc[0] + 0.54;
        let lon = site.loc[1];
        assert!(!pair_within_hop_range_coords_to_site(
            &preset,
            lat,
            lon,
            &site,
            Some(&boards)
        ));
    }
}
