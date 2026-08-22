//! On-demand mutual site link checks via splatter P2P propagation.

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::{Arc, LazyLock, Mutex};

use anyhow::{Context, Result};
use peaky_preset::{resolved_preset_cache_dir, Preset, SiteEntry};
use serde_json::{json, Value};
use splatter::{propagate::LinkStrength, Session};

use crate::rf::{max_hop_range_m, pair_within_hop_range, resolved_site_tx_height_m, rf_json_for_preset};

/// Bumps when link computation semantics change; invalidates prior mesh caches.
const LINKS_MODEL: &str = "p2p-v5";

#[derive(Debug, thiserror::Error)]
#[error("{0}")]
pub struct LinksError(pub String);

type LinkPair = (String, String);

static LINKS_PAYLOAD_CACHE: LazyLock<Mutex<HashMap<String, (String, Value)>>> =
    LazyLock::new(|| Mutex::new(HashMap::new()));

pub fn canonical_site_pair(slug_a: &str, slug_b: &str) -> LinkPair {
    let a = slug_a.trim();
    let b = slug_b.trim();
    if a <= b {
        (a.to_string(), b.to_string())
    } else {
        (b.to_string(), a.to_string())
    }
}

pub fn manual_link_pairs(preset: &Preset) -> HashSet<LinkPair> {
    preset
        .links
        .iter()
        .map(|pair| canonical_site_pair(&pair[0], &pair[1]))
        .collect()
}

pub fn links_input_fingerprint(preset: &Preset) -> Result<String> {
    let radius_km = match &preset.simulation.radius_km {
        serde_yaml::Value::Number(n) => n.as_f64().unwrap_or(50.0),
        serde_yaml::Value::String(s) => s.parse::<f64>().unwrap_or(50.0),
        _ => 50.0,
    };
    let modem = fingerprint_sim_label(&preset.simulation.modem);
    let environment = fingerprint_sim_label(&preset.simulation.environment);
    let tx_h = fingerprint_nested_height_m(&preset.simulation.transmitter, "transmitter");
    let rx_h = fingerprint_nested_height_m(&preset.simulation.receiver, "receiver");

    let mut site_rows: Vec<Value> = preset
        .sites
        .iter()
        .map(|(slug, site)| {
            json!([
                slug,
                (site.loc[0] * 1e7).round() / 1e7,
                (site.loc[1] * 1e7).round() / 1e7,
                (resolved_site_tx_height_m(preset, site) * 1000.0).round() / 1000.0,
            ])
        })
        .collect();
    site_rows.sort_by(|a, b| {
        a.get(0)
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .cmp(b.get(0).and_then(|v| v.as_str()).unwrap_or(""))
    });

    let mut manual: Vec<Value> = preset
        .links
        .iter()
        .map(|pair| {
            let (a, b) = canonical_site_pair(&pair[0], &pair[1]);
            json!([a, b])
        })
        .collect();
    manual.sort_by(|a, b| {
        let aa = a.get(0).and_then(|v| v.as_str()).unwrap_or("");
        let bb = b.get(0).and_then(|v| v.as_str()).unwrap_or("");
        aa.cmp(bb)
    });

    Ok(serde_json::to_string(&json!([
        LINKS_MODEL,
        radius_km,
        modem,
        environment,
        tx_h,
        rx_h,
        site_rows,
        manual,
    ]))?)
}

fn fingerprint_sim_label(value: &Option<serde_yaml::Value>) -> String {
    match value {
        None => "None".to_string(),
        Some(serde_yaml::Value::String(s)) => s.clone(),
        Some(v) => format!("{v:?}"),
    }
}

fn fingerprint_nested_height_m(
    project: &HashMap<String, serde_yaml::Value>,
    _home_key: &str,
) -> f64 {
    project
        .get("height_m")
        .and_then(|v| v.as_f64())
        .unwrap_or(0.0)
}

fn links_disk_cache_path(preset_path: &Path) -> PathBuf {
    resolved_preset_cache_dir(preset_path).join("links/mesh.json")
}

fn write_disk_links_cache(preset_path: &Path, fingerprint: &str, payload: &Value) -> Result<()> {
    let path = links_disk_cache_path(preset_path);
    std::fs::create_dir_all(path.parent().unwrap())?;
    let tmp = path.with_extension("json.tmp");
    let wire = json!({ "fingerprint": fingerprint, "payload": payload });
    std::fs::write(&tmp, serde_json::to_string(&wire)?)?;
    std::fs::rename(&tmp, &path)?;
    Ok(())
}

fn read_disk_links_cache(preset_path: &Path, fingerprint: &str) -> Option<Value> {
    let path = links_disk_cache_path(preset_path);
    let raw = std::fs::read_to_string(&path).ok()?;
    let wire: Value = serde_json::from_str(&raw).ok()?;
    if wire.get("fingerprint")?.as_str()? != fingerprint {
        return None;
    }
    let payload = wire.get("payload")?.clone();
    if payload.get("status")?.as_str()? != "ready" {
        return None;
    }
    if payload.get("links_model")?.as_str()? != LINKS_MODEL {
        return None;
    }
    Some(payload)
}

fn cache_payload_is_p2p(payload: &Value) -> bool {
    payload.get("links_model").and_then(|v| v.as_str()) == Some(LINKS_MODEL)
}

fn project_cache_key(preset_path: &Path) -> String {
    preset_path
        .parent()
        .map(|p| p.canonicalize().unwrap_or_else(|_| p.to_path_buf()))
        .unwrap_or_else(|| preset_path.to_path_buf())
        .display()
        .to_string()
}

pub fn store_project_site_links_cache(
    preset_path: &Path,
    preset: &Preset,
    payload: &Value,
) -> Result<()> {
    if payload.get("status").and_then(|v| v.as_str()) != Some("ready") {
        return Ok(());
    }
    let feature_count = payload
        .pointer("/geojson/features")
        .and_then(|v| v.as_array())
        .map(|a| a.len())
        .unwrap_or(0);
    let linked_count = payload
        .get("links")
        .and_then(|v| v.as_array())
        .map(|rows| {
            rows.iter()
                .filter(|row| row.get("linked").and_then(|v| v.as_bool()) != Some(false))
                .count()
        })
        .unwrap_or(0);
    if feature_count == 0 && linked_count == 0 {
        return Ok(());
    }
    let fingerprint = links_input_fingerprint(preset)?;
    let key = project_cache_key(preset_path);
    if let Ok(mut cache) = LINKS_PAYLOAD_CACHE.lock() {
        cache.insert(key, (fingerprint.clone(), payload.clone()));
    }
    let _ = write_disk_links_cache(preset_path, &fingerprint, payload);
    Ok(())
}

fn cached_project_site_links(preset_path: &Path, preset: &Preset) -> Result<Option<Value>> {
    let fingerprint = links_input_fingerprint(preset)?;
    let key = project_cache_key(preset_path);
    if let Ok(cache) = LINKS_PAYLOAD_CACHE.lock() {
        if let Some((fp, payload)) = cache.get(&key) {
            if fp == &fingerprint
                && payload.get("status").and_then(|v| v.as_str()) == Some("ready")
                && cache_payload_is_p2p(payload)
            {
                return Ok(Some(payload.clone()));
            }
        }
    }
    if let Some(disk) = read_disk_links_cache(preset_path, &fingerprint) {
        if let Ok(mut cache) = LINKS_PAYLOAD_CACHE.lock() {
            cache.insert(key, (fingerprint, disk.clone()));
        }
        return Ok(Some(disk));
    }
    Ok(None)
}

fn link_record(slug_a: &str, slug_b: &str, linked: bool, manual: bool, strength: &str) -> Value {
    let (a, b) = canonical_site_pair(slug_a, slug_b);
    json!({
        "a": a,
        "b": b,
        "linked": linked,
        "manual": manual,
        "strength": strength,
    })
}

fn strength_label(strength: LinkStrength) -> &'static str {
    match strength {
        LinkStrength::Weak => "weak",
        LinkStrength::Strong => "strong",
    }
}

fn site_mesh_pair_strengths(
    session: &Session,
    preset: &Preset,
    rf_pair_slugs: &[(String, String)],
) -> Result<Vec<Option<LinkStrength>>> {
    if rf_pair_slugs.is_empty() {
        return Ok(Vec::new());
    }
    let rf_json = rf_json_for_preset(preset)?;
    let pairs: Vec<(f64, f64, f64, f64, f64, f64)> = rf_pair_slugs
        .iter()
        .map(|(slug_a, slug_b)| {
            let site_a = &preset.sites[slug_a];
            let site_b = &preset.sites[slug_b];
            (
                site_a.loc[0],
                site_a.loc[1],
                resolved_site_tx_height_m(preset, site_a),
                site_b.loc[0],
                site_b.loc[1],
                resolved_site_tx_height_m(preset, site_b),
            )
        })
        .collect();
    session.site_mesh_pair_strengths(&pairs, &rf_json)
}

fn line_feature(
    slug_a: &str,
    slug_b: &str,
    site_a: &SiteEntry,
    site_b: &SiteEntry,
    manual: bool,
    strength: &str,
) -> Value {
    let (a, b) = canonical_site_pair(slug_a, slug_b);
    let lat_a = site_a.loc[0];
    let lon_a = site_a.loc[1];
    let lat_b = site_b.loc[0];
    let lon_b = site_b.loc[1];
    let distance_km =
        (splatter::propagate::haversine_m(lat_a, lon_a, lat_b, lon_b) / 1000.0 * 10.0).round() / 10.0;
    json!({
        "type": "Feature",
        "geometry": {
            "type": "LineString",
            "coordinates": [[lon_a, lat_a], [lon_b, lat_b]],
        },
        "properties": {
            "a": a,
            "b": b,
            "manual": manual,
            "strength": strength,
            "distance_km": distance_km,
        },
    })
}

fn manual_only_payload(preset: &Preset) -> Value {
    let manual_pairs = manual_link_pairs(preset);
    let mut records = Vec::new();
    let mut features = Vec::new();
    for (a, b) in manual_pairs {
        let site_a = preset.sites.get(&a);
        let site_b = preset.sites.get(&b);
        if site_a.is_none() || site_b.is_none() {
            continue;
        }
        records.push(link_record(&a, &b, true, true, "strong"));
        features.push(line_feature(
            &a,
            &b,
            site_a.unwrap(),
            site_b.unwrap(),
            true,
            "strong",
        ));
    }
    records.sort_by(|a, b| {
        let aa = a.get("a").and_then(|v| v.as_str()).unwrap_or("");
        let bb = b.get("a").and_then(|v| v.as_str()).unwrap_or("");
        aa.cmp(bb)
    });
    json!({
        "status": "pending",
        "links": records,
        "geojson": { "type": "FeatureCollection", "features": features },
    })
}

fn ensure_dem_for_site_links(session: &Session, preset: &Preset) -> Result<()> {
    let points: Vec<(f64, f64)> = preset
        .sites
        .values()
        .map(|site| (site.loc[0], site.loc[1]))
        .collect();
    if points.is_empty() {
        return Ok(());
    }
    session
        .ensure_tiles_for_points(&points, max_hop_range_m(preset))
        .context("ensure dem tiles for site links")
}

pub fn compute_project_site_links_p2p(
    session: Arc<Session>,
    preset: &Preset,
    analysis_complete: bool,
) -> Result<Value> {
    ensure_dem_for_site_links(session.as_ref(), preset)?;
    let manual_pairs = manual_link_pairs(preset);
    let slug_list: Vec<&String> = preset.sites.keys().collect();

    let mut rf_pair_slugs: Vec<LinkPair> = Vec::new();

    for i in 0..slug_list.len() {
        for j in (i + 1)..slug_list.len() {
            let slug_a = slug_list[i];
            let slug_b = slug_list[j];
            let key = canonical_site_pair(slug_a, slug_b);
            if manual_pairs.contains(&key) {
                continue;
            }
            let site_a = &preset.sites[slug_a];
            let site_b = &preset.sites[slug_b];
            if !pair_within_hop_range(
                preset,
                site_a.loc[0],
                site_a.loc[1],
                site_b.loc[0],
                site_b.loc[1],
            ) {
                continue;
            }
            rf_pair_slugs.push(key);
        }
    }

    let strengths = if rf_pair_slugs.is_empty() {
        Vec::new()
    } else {
        site_mesh_pair_strengths(&session, preset, &rf_pair_slugs).context("site_mesh_pair_strengths")?
    };

    let mut records = Vec::new();
    let mut features = Vec::new();

    for (slug_a, slug_b) in manual_pairs {
        let Some(site_a) = preset.sites.get(&slug_a) else { continue };
        let Some(site_b) = preset.sites.get(&slug_b) else { continue };
        records.push(link_record(&slug_a, &slug_b, true, true, "strong"));
        features.push(line_feature(
            &slug_a, &slug_b, site_a, site_b, true, "strong",
        ));
    }

    for (idx, (slug_a, slug_b)) in rf_pair_slugs.into_iter().enumerate() {
        let Some(strength) = strengths.get(idx).copied().flatten() else {
            continue;
        };
        let label = strength_label(strength);
        let site_a = &preset.sites[&slug_a];
        let site_b = &preset.sites[&slug_b];
        records.push(link_record(&slug_a, &slug_b, true, false, label));
        features.push(line_feature(
            &slug_a, &slug_b, site_a, site_b, false, label,
        ));
    }

    records.sort_by(|a, b| {
        let aa = a.get("a").and_then(|v| v.as_str()).unwrap_or("");
        let bb = b.get("a").and_then(|v| v.as_str()).unwrap_or("");
        aa.cmp(bb)
    });

    let status = if analysis_complete { "ready" } else { "ready" };
    Ok(json!({
        "status": status,
        "links_model": LINKS_MODEL,
        "links": records,
        "geojson": { "type": "FeatureCollection", "features": features },
    }))
}

pub fn load_single_site_links(
    session: Arc<Session>,
    preset_path: &Path,
    preset: &Preset,
    site_slug: &str,
) -> Result<Value> {
    if let Some(mesh) = cached_project_site_links(preset_path, preset)? {
        return Ok(slice_site_links_from_mesh(&mesh, site_slug));
    }
    compute_single_site_links_p2p(session, preset, site_slug)
}

pub fn slice_site_links_from_mesh(mesh: &Value, site_slug: &str) -> Value {
    let slug = site_slug.trim();
    let links: Vec<Value> = mesh
        .get("links")
        .and_then(|v| v.as_array())
        .map(|rows| {
            rows.iter()
                .filter(|row| {
                    row.get("a").and_then(|v| v.as_str()) == Some(slug)
                        || row.get("b").and_then(|v| v.as_str()) == Some(slug)
                })
                .cloned()
                .collect()
        })
        .unwrap_or_default();
    let features: Vec<Value> = mesh
        .pointer("/geojson/features")
        .and_then(|v| v.as_array())
        .map(|rows| {
            rows.iter()
                .filter(|f| {
                    f.get("properties")
                        .and_then(|v| v.as_object())
                        .map(|p| {
                            p.get("a").and_then(|v| v.as_str()) == Some(slug)
                                || p.get("b").and_then(|v| v.as_str()) == Some(slug)
                        })
                        .unwrap_or(false)
                })
                .cloned()
                .collect()
        })
        .unwrap_or_default();
    json!({
        "status": "ready",
        "site": slug,
        "outbound_ready": true,
        "links": links,
        "geojson": { "type": "FeatureCollection", "features": features },
        "missing": [],
    })
}

pub fn compute_single_site_links_p2p(
    session: Arc<Session>,
    preset: &Preset,
    site_slug: &str,
) -> Result<Value> {
    let slug = site_slug.trim();
    let center = preset
        .sites
        .get(slug)
        .ok_or_else(|| LinksError(format!("unknown site slug: {site_slug:?}")))?;
    session
        .ensure_tiles_for_points(
            &[(center.loc[0], center.loc[1])],
            max_hop_range_m(preset),
        )
        .context("ensure dem tiles for single-site links")?;
    let manual_pairs = manual_link_pairs(preset);
    let lat_c = center.loc[0];
    let lon_c = center.loc[1];

    let mut rf_slugs = Vec::new();
    let mut records = Vec::new();
    let mut features = Vec::new();

    for (other_slug, other) in preset.sites.iter() {
        if other_slug == slug {
            continue;
        }
        let pair = canonical_site_pair(slug, other_slug);
        let lat_o = other.loc[0];
        let lon_o = other.loc[1];
        if manual_pairs.contains(&pair) {
            records.push(link_record(slug, other_slug, true, true, "strong"));
            features.push(line_feature(slug, other_slug, center, other, true, "strong"));
            continue;
        }
        if !pair_within_hop_range(preset, lat_c, lon_c, lat_o, lon_o) {
            continue;
        }
        rf_slugs.push(other_slug.clone());
    }

    if !rf_slugs.is_empty() {
        let pair_slugs: Vec<(String, String)> = rf_slugs
            .iter()
            .map(|other| canonical_site_pair(slug, other))
            .collect();
        let strengths =
            site_mesh_pair_strengths(&session, preset, &pair_slugs).context("site_mesh_pair_strengths")?;
        for (idx, other_slug) in rf_slugs.into_iter().enumerate() {
            let Some(strength) = strengths.get(idx).copied().flatten() else {
                continue;
            };
            let label = strength_label(strength);
            let other = &preset.sites[&other_slug];
            records.push(link_record(slug, &other_slug, true, false, label));
            features.push(line_feature(slug, &other_slug, center, other, false, label));
        }
    }

    records.sort_by(|a, b| {
        let aa = a.get("a").and_then(|v| v.as_str()).unwrap_or("");
        let bb = b.get("a").and_then(|v| v.as_str()).unwrap_or("");
        aa.cmp(bb)
    });

    Ok(json!({
        "status": "ready",
        "site": slug,
        "outbound_ready": true,
        "links": records,
        "geojson": { "type": "FeatureCollection", "features": features },
        "missing": [],
    }))
}

pub fn load_project_site_links(
    preset_path: &Path,
    preset: &Preset,
) -> Result<Value> {
    if let Some(cached) = cached_project_site_links(preset_path, preset)? {
        return Ok(cached);
    }
    Ok(manual_only_payload(preset))
}

pub fn warm_project_site_links(
    session: Arc<Session>,
    preset_path: &Path,
    preset: &Preset,
) -> Result<Value> {
    if let Some(cached) = cached_project_site_links(preset_path, preset)? {
        return Ok(cached);
    }
    let payload = compute_project_site_links_p2p(session, preset, true)?;
    store_project_site_links_cache(preset_path, preset, &payload)?;
    Ok(payload)
}

/// Evaluate P2P mutual links from draft coordinates to each in-range site.
pub fn load_coords_site_links(
    session: &Session,
    preset_path: &Path,
    preset: &Preset,
    lat: f64,
    lon: f64,
    exclude_site_slug: Option<&str>,
) -> Result<Vec<Value>, LinksError> {
    if !(-90.0..=90.0).contains(&lat) {
        return Err(LinksError(format!("lat out of bounds: {lat}")));
    }
    if !(-180.0..=180.0).contains(&lon) {
        return Err(LinksError(format!("lon out of bounds: {lon}")));
    }

    let exclude = exclude_site_slug.unwrap_or("").trim();
    let manual_pairs = manual_link_pairs(preset);
    let draft_h = crate::rf::default_repeater_tx_height_m(preset);
    let mut candidates: Vec<(String, f64, f64, f64)> = Vec::new();

    for (slug, site) in preset.sites.iter() {
        if !exclude.is_empty() && slug.as_str() == exclude {
            continue;
        }
        let site_lat = site.loc[0];
        let site_lon = site.loc[1];
        if !pair_within_hop_range(preset, lat, lon, site_lat, site_lon) {
            continue;
        }
        candidates.push((
            slug.clone(),
            site_lat,
            site_lon,
            resolved_site_tx_height_m(preset, site),
        ));
    }

    if candidates.is_empty() {
        return Ok(Vec::new());
    }

    let _ = preset_path;
    session
        .ensure_tiles_for_points(
            &std::iter::once((lat, lon))
                .chain(candidates.iter().map(|(_, slat, slon, _)| (*slat, *slon)))
                .collect::<Vec<_>>(),
            max_hop_range_m(preset),
        )
        .map_err(|e| LinksError(format!("ensure dem tiles: {e:#}")))?;

    let rf_json = rf_json_for_preset(preset).map_err(|e| LinksError(e.to_string()))?;
    let endpoints: Vec<(f64, f64, f64)> = candidates
        .iter()
        .map(|(_, slat, slon, tx_h)| (*slat, *slon, *tx_h))
        .collect();
    let viable = session
        .seek_repeater_link_batch(lat, lon, draft_h, &endpoints, &rf_json)
        .map_err(|e| LinksError(format!("coords link batch: {e:#}")))?;

    let mut records = Vec::new();
    for ((slug, site_lat, site_lon, _), linked) in candidates.into_iter().zip(viable.into_iter()) {
        if manual_pairs.contains(&canonical_site_pair("__draft__", &slug)) {
            continue;
        }
        if !linked {
            continue;
        }
        let distance_km =
            (splatter::propagate::haversine_m(lat, lon, site_lat, site_lon) / 1000.0 * 10.0).round() / 10.0;
        records.push(json!({
            "slug": slug,
            "linked": true,
            "manual": false,
            "distance_km": distance_km,
        }));
    }

    records.sort_by(|a, b| {
        let da = a.get("distance_km").and_then(|v| v.as_f64()).unwrap_or(0.0);
        let db = b.get("distance_km").and_then(|v| v.as_f64()).unwrap_or(0.0);
        da.partial_cmp(&db)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| {
                a.get("slug")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .cmp(b.get("slug").and_then(|v| v.as_str()).unwrap_or(""))
            })
    });
    Ok(records)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn canonical_pair_orders() {
        assert_eq!(canonical_site_pair("b", "a"), ("a".into(), "b".into()));
    }
}
