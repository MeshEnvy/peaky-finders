//! On-demand mutual site link checks via splatter P2P propagation.

use std::collections::{HashMap, HashSet};
use std::path::Path;
use std::sync::{Arc, LazyLock, Mutex};

use anyhow::{Context, Result};
use peaky_preset::{
    classify_link_kind, manual_link_pairs_for_project, project::ProjectLayout, read_pair_cache,
    write_pair_cache, prune_link_cache, BoardViewshedResolver, LinkCacheKind, PairLinkCacheRecord,
    Preset, SiteEntry, LINKS_MODEL,
};
use serde_json::{json, Value};
use splatter::{propagate::LinkLegs, Session};

use crate::rf::{
    draft_hop_radius_m, load_board_viewshed, max_hop_range_m, pair_hop_range_km,
    pair_within_hop_range_coords_to_site, pair_within_hop_range_sites, resolved_site_tx_height_m,
    rf_json_for_preset, site_hop_radius_km,
};

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

fn manual_link_pairs(preset_path: &Path) -> Result<HashSet<LinkPair>> {
    let layout = project_layout(preset_path)?;
    manual_link_pairs_for_project(&layout)
}

pub fn links_input_fingerprint(preset_path: &Path, preset: &Preset) -> Result<String> {
    let boards = load_board_viewshed(preset_path);
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
                site_hop_radius_km(preset, site, Some(&boards)),
            ])
        })
        .collect();
    site_rows.sort_by(|a, b| {
        a.get(0)
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .cmp(b.get(0).and_then(|v| v.as_str()).unwrap_or(""))
    });

    let manual_pairs = manual_link_pairs(preset_path)?;
    let mut manual: Vec<Value> = manual_pairs
        .iter()
        .map(|(a, b)| json!([a, b]))
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

pub fn invalidate_project_site_links_cache(preset_path: &Path) {
    let key = project_cache_key(preset_path);
    if let Ok(mut cache) = LINKS_PAYLOAD_CACHE.lock() {
        cache.remove(&key);
    }
}

/// P2P links from each slug to in-range sites (same stack as the site mesh).
pub fn compute_links_for_slugs(
    session: Arc<Session>,
    preset_path: &Path,
    preset: &Preset,
    slugs: &[String],
) -> Result<Value> {
    let mut records = Vec::new();
    let mut features: Vec<Value> = Vec::new();
    let mut seen_records: HashSet<LinkPair> = HashSet::new();
    let mut seen_features: HashSet<LinkPair> = HashSet::new();
    for slug in slugs {
        if !preset.sites.contains_key(slug) {
            continue;
        }
        let payload = compute_single_site_links_p2p(Arc::clone(&session), preset_path, preset, slug)?;
        if let Some(rows) = payload.get("links").and_then(|v| v.as_array()) {
            for row in rows {
                let a = row.get("a").and_then(|v| v.as_str()).unwrap_or("");
                let b = row.get("b").and_then(|v| v.as_str()).unwrap_or("");
                if a.is_empty() || b.is_empty() {
                    continue;
                }
                if !seen_records.insert(canonical_site_pair(a, b)) {
                    continue;
                }
                records.push(row.clone());
            }
        }
        if let Some(rows) = payload
            .pointer("/geojson/features")
            .and_then(|v| v.as_array())
        {
            for feature in rows {
                let props = feature.get("properties");
                let a = props
                    .and_then(|v| v.get("a"))
                    .and_then(|v| v.as_str())
                    .unwrap_or("");
                let b = props
                    .and_then(|v| v.get("b"))
                    .and_then(|v| v.as_str())
                    .unwrap_or("");
                if a.is_empty() || b.is_empty() {
                    continue;
                }
                if !seen_features.insert(canonical_site_pair(a, b)) {
                    continue;
                }
                features.push(feature.clone());
            }
        }
    }
    records.sort_by(|a, b| {
        let aa = a.get("a").and_then(|v| v.as_str()).unwrap_or("");
        let bb = b.get("a").and_then(|v| v.as_str()).unwrap_or("");
        aa.cmp(bb)
    });
    Ok(json!({
        "status": "ready",
        "links_model": LINKS_MODEL,
        "links": records,
        "geojson": { "type": "FeatureCollection", "features": features },
    }))
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
    let fingerprint = links_input_fingerprint(preset_path, preset)?;
    let key = project_cache_key(preset_path);
    if let Ok(mut cache) = LINKS_PAYLOAD_CACHE.lock() {
        cache.insert(key, (fingerprint.clone(), payload.clone()));
    }
    Ok(())
}

fn cached_project_site_links(preset_path: &Path, preset: &Preset) -> Result<Option<Value>> {
    let fingerprint = links_input_fingerprint(preset_path, preset)?;
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
    Ok(None)
}

fn link_record(slug_a: &str, slug_b: &str, linked: bool, manual: bool, kind: &str) -> Value {
    let (a, b) = canonical_site_pair(slug_a, slug_b);
    json!({
        "a": a,
        "b": b,
        "linked": linked,
        "manual": manual,
        "kind": kind,
    })
}

fn utc_date_iso() -> String {
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs() as i64;
    unix_days_to_iso(secs / 86_400)
}

fn unix_days_to_iso(mut z: i64) -> String {
    z += 719_468;
    let era = (if z >= 0 { z } else { z - 146_096 }) / 146_097;
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = mp + if mp < 10 { 3 } else { -9 };
    let year = y + if m <= 2 { 1 } else { 0 };
    format!("{year:04}-{m:02}-{d:02}")
}

fn project_layout(preset_path: &Path) -> Result<ProjectLayout> {
    ProjectLayout::from_config_path(preset_path)
}

fn pair_link_fingerprint(
    preset: &Preset,
    boards: &BoardViewshedResolver,
    slug_a: &str,
    slug_b: &str,
) -> Result<String> {
    let (a, b) = canonical_site_pair(slug_a, slug_b);
    let site_a = preset
        .sites
        .get(&a)
        .with_context(|| format!("unknown site {a}"))?;
    let site_b = preset
        .sites
        .get(&b)
        .with_context(|| format!("unknown site {b}"))?;
    let radius_km = match &preset.simulation.radius_km {
        serde_yaml::Value::Number(n) => n.as_f64().unwrap_or(50.0),
        serde_yaml::Value::String(s) => s.parse::<f64>().unwrap_or(50.0),
        _ => 50.0,
    };
    Ok(serde_json::to_string(&json!([
        LINKS_MODEL,
        radius_km,
        fingerprint_sim_label(&preset.simulation.modem),
        fingerprint_sim_label(&preset.simulation.environment),
        fingerprint_nested_height_m(&preset.simulation.transmitter, "transmitter"),
        fingerprint_nested_height_m(&preset.simulation.receiver, "receiver"),
        [
            a,
            (site_a.loc[0] * 1e7).round() / 1e7,
            (site_a.loc[1] * 1e7).round() / 1e7,
            (resolved_site_tx_height_m(preset, site_a) * 1000.0).round() / 1000.0,
            site_hop_radius_km(preset, site_a, Some(boards)),
        ],
        [
            b,
            (site_b.loc[0] * 1e7).round() / 1e7,
            (site_b.loc[1] * 1e7).round() / 1e7,
            (resolved_site_tx_height_m(preset, site_b) * 1000.0).round() / 1000.0,
            site_hop_radius_km(preset, site_b, Some(boards)),
        ],
    ]))?)
}

fn cache_record_from_legs(
    preset: &Preset,
    boards: &BoardViewshedResolver,
    slug_a: &str,
    slug_b: &str,
    legs: LinkLegs,
) -> Result<PairLinkCacheRecord> {
    let (a, b) = canonical_site_pair(slug_a, slug_b);
    let site_a = &preset.sites[&a];
    let site_b = &preset.sites[&b];
    let kind = classify_link_kind(legs.forward_margin_db, legs.reverse_margin_db);
    let distance_km = (splatter::propagate::haversine_m(
        site_a.loc[0],
        site_a.loc[1],
        site_b.loc[0],
        site_b.loc[1],
    ) / 1000.0
        * 10.0)
        .round()
        / 10.0;
    Ok(PairLinkCacheRecord {
        a,
        b,
        fingerprint: pair_link_fingerprint(preset, boards, slug_a, slug_b)?,
        links_model: LINKS_MODEL.to_string(),
        forward_margin_db: legs.forward_margin_db,
        reverse_margin_db: legs.reverse_margin_db,
        kind,
        distance_km,
        at: utc_date_iso(),
        manual: false,
        field_at: None,
    })
}

fn site_mesh_pair_legs(
    session: &Session,
    preset: &Preset,
    rf_pair_slugs: &[(String, String)],
) -> Result<Vec<LinkLegs>> {
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
    session.site_mesh_pair_legs(&pairs, &rf_json)
}

fn ensure_pair_caches(
    session: &Session,
    preset_path: &Path,
    preset: &Preset,
    boards: &BoardViewshedResolver,
    rf_pair_slugs: &[(String, String)],
) -> Result<Vec<PairLinkCacheRecord>> {
    let layout = project_layout(preset_path)?;
    let mut out: Vec<Option<PairLinkCacheRecord>> = Vec::with_capacity(rf_pair_slugs.len());
    let mut to_compute: Vec<(usize, (String, String))> = Vec::new();

    for (idx, (slug_a, slug_b)) in rf_pair_slugs.iter().enumerate() {
        let fp = pair_link_fingerprint(preset, boards, slug_a, slug_b)?;
        if let Some(cached) = read_pair_cache(&layout, slug_a, slug_b)? {
            if cached.links_model == LINKS_MODEL && cached.fingerprint == fp {
                out.push(Some(cached));
                continue;
            }
        }
        to_compute.push((idx, (slug_a.clone(), slug_b.clone())));
        out.push(None);
    }

    if !to_compute.is_empty() {
        let pairs: Vec<(String, String)> = to_compute.iter().map(|(_, p)| p.clone()).collect();
        let legs = site_mesh_pair_legs(session, preset, &pairs)?;
        for ((idx, (slug_a, slug_b)), leg) in to_compute.into_iter().zip(legs.into_iter()) {
            let record = cache_record_from_legs(preset, boards, &slug_a, &slug_b, leg)?;
            write_pair_cache(&layout, &record)?;
            out[idx] = Some(record);
        }
    }

    Ok(out
        .into_iter()
        .map(|slot| slot.expect("unfilled pair cache slot"))
        .collect())
}

fn line_feature(
    preset: &Preset,
    boards: Option<&BoardViewshedResolver>,
    slug_a: &str,
    slug_b: &str,
    site_a: &SiteEntry,
    site_b: &SiteEntry,
    manual: bool,
    kind: &str,
) -> Value {
    let (a, b) = canonical_site_pair(slug_a, slug_b);
    let lat_a = site_a.loc[0];
    let lon_a = site_a.loc[1];
    let lat_b = site_b.loc[0];
    let lon_b = site_b.loc[1];
    let distance_km =
        (splatter::propagate::haversine_m(lat_a, lon_a, lat_b, lon_b) / 1000.0 * 10.0).round() / 10.0;
    let hop_range_km = pair_hop_range_km(preset, site_a, site_b, boards);
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
            "kind": kind,
            "distance_km": distance_km,
            "hop_range_km": hop_range_km,
        },
    })
}

fn manual_only_payload(
    preset_path: &Path,
    preset: &Preset,
    boards: &BoardViewshedResolver,
) -> Result<Value> {
    let manual_pairs = manual_link_pairs(preset_path)?;
    let mut records = Vec::new();
    let mut features = Vec::new();
    for (a, b) in manual_pairs {
        let site_a = preset.sites.get(&a);
        let site_b = preset.sites.get(&b);
        if site_a.is_none() || site_b.is_none() {
            continue;
        }
        records.push(link_record(&a, &b, true, true, "manual"));
        features.push(line_feature(
            preset,
            Some(boards),
            &a,
            &b,
            site_a.unwrap(),
            site_b.unwrap(),
            true,
            "manual",
        ));
    }
    records.sort_by(|a, b| {
        let aa = a.get("a").and_then(|v| v.as_str()).unwrap_or("");
        let bb = b.get("a").and_then(|v| v.as_str()).unwrap_or("");
        aa.cmp(bb)
    });
    Ok(json!({
        "status": "pending",
        "links": records,
        "geojson": { "type": "FeatureCollection", "features": features },
    }))
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
    preset_path: &Path,
    preset: &Preset,
    analysis_complete: bool,
) -> Result<Value> {
    let boards = load_board_viewshed(preset_path);
    ensure_dem_for_site_links(session.as_ref(), preset)?;
    let manual_pairs = manual_link_pairs(preset_path)?;
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
            if !pair_within_hop_range_sites(preset, site_a, site_b, Some(&boards)) {
                continue;
            }
            rf_pair_slugs.push(key);
        }
    }

    let pair_records = if rf_pair_slugs.is_empty() {
        Vec::new()
    } else {
        ensure_pair_caches(session.as_ref(), preset_path, preset, &boards, &rf_pair_slugs)
            .context("ensure pair link caches")?
    };

    let mut records = Vec::new();
    let mut features = Vec::new();

    for (slug_a, slug_b) in manual_pairs {
        let Some(site_a) = preset.sites.get(&slug_a) else { continue };
        let Some(site_b) = preset.sites.get(&slug_b) else { continue };
        records.push(link_record(&slug_a, &slug_b, true, true, "manual"));
        features.push(line_feature(
            preset,
            Some(&boards),
            &slug_a,
            &slug_b,
            site_a,
            site_b,
            true,
            "manual",
        ));
    }

    for (record, (slug_a, slug_b)) in pair_records.into_iter().zip(rf_pair_slugs.into_iter()) {
        if !record.kind.renders_on_map() {
            continue;
        }
        let kind = record.kind.as_str();
        let linked = record.kind.mesh_linked(false);
        let site_a = &preset.sites[&slug_a];
        let site_b = &preset.sites[&slug_b];
        records.push(link_record(&slug_a, &slug_b, linked, false, kind));
        features.push(line_feature(
            preset,
            Some(&boards),
            &slug_a,
            &slug_b,
            site_a,
            site_b,
            false,
            kind,
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
    compute_single_site_links_p2p(session, preset_path, preset, site_slug)
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
    preset_path: &Path,
    preset: &Preset,
    site_slug: &str,
) -> Result<Value> {
    let boards = load_board_viewshed(preset_path);
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
    let manual_pairs = manual_link_pairs(preset_path)?;

    let mut rf_slugs = Vec::new();
    let mut records = Vec::new();
    let mut features = Vec::new();

    for (other_slug, other) in preset.sites.iter() {
        if other_slug == slug {
            continue;
        }
        let pair = canonical_site_pair(slug, other_slug);
        if manual_pairs.contains(&pair) {
            records.push(link_record(slug, other_slug, true, true, "manual"));
            features.push(line_feature(
                preset,
                Some(&boards),
                slug,
                other_slug,
                center,
                other,
                true,
                "manual",
            ));
            continue;
        }
        if !pair_within_hop_range_sites(preset, center, other, Some(&boards)) {
            continue;
        }
        rf_slugs.push(other_slug.clone());
    }

    if !rf_slugs.is_empty() {
        let pair_slugs: Vec<(String, String)> = rf_slugs
            .iter()
            .map(|other| canonical_site_pair(slug, other))
            .collect();
        let cached = ensure_pair_caches(session.as_ref(), preset_path, preset, &boards, &pair_slugs)
            .context("ensure pair link caches")?;
        for (record, other_slug) in cached.into_iter().zip(rf_slugs.into_iter()) {
            if !record.kind.renders_on_map() {
                continue;
            }
            let kind = record.kind.as_str();
            let linked = record.kind.mesh_linked(false);
            let other = &preset.sites[&other_slug];
            records.push(link_record(slug, &other_slug, linked, false, kind));
            features.push(line_feature(
                preset,
                Some(&boards),
                slug,
                &other_slug,
                center,
                other,
                false,
                kind,
            ));
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
    manual_only_payload(preset_path, preset, &load_board_viewshed(preset_path))
}

pub fn warm_project_site_links(
    session: Arc<Session>,
    preset_path: &Path,
    preset: &Preset,
) -> Result<Value> {
    if let Some(cached) = cached_project_site_links(preset_path, preset)? {
        return Ok(cached);
    }
    let layout = project_layout(preset_path)?;
    let slugs: std::collections::HashSet<String> = preset.sites.keys().cloned().collect();
    prune_link_cache(&layout, &slugs)?;
    let payload = compute_project_site_links_p2p(session, preset_path, preset, true)?;
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
    let manual_pairs = manual_link_pairs(preset_path).unwrap_or_default();
    let boards = load_board_viewshed(preset_path);
    let draft_h = crate::rf::default_candidate_tx_height(preset, lat, lon);
    let mut candidates: Vec<(String, f64, f64, f64, f64)> = Vec::new();

    for (slug, site) in preset.sites.iter() {
        if !exclude.is_empty() && slug.as_str() == exclude {
            continue;
        }
        let site_lat = site.loc[0];
        let site_lon = site.loc[1];
        if !pair_within_hop_range_coords_to_site(preset, lat, lon, site, Some(&boards)) {
            continue;
        }
        let hop_range_km = draft_hop_radius_m(preset, site, Some(&boards)) / 1000.0;
        candidates.push((
            slug.clone(),
            site_lat,
            site_lon,
            resolved_site_tx_height_m(preset, site),
            hop_range_km,
        ));
    }

    if candidates.is_empty() {
        return Ok(Vec::new());
    }

    session
        .ensure_tiles_for_points(
            &std::iter::once((lat, lon))
                .chain(candidates.iter().map(|(_, slat, slon, _, _)| (*slat, *slon)))
                .collect::<Vec<_>>(),
            max_hop_range_m(preset),
        )
        .map_err(|e| LinksError(format!("ensure dem tiles: {e:#}")))?;

    let rf_json = rf_json_for_preset(preset).map_err(|e| LinksError(e.to_string()))?;
    let endpoints: Vec<(f64, f64, f64)> = candidates
        .iter()
        .map(|(_, slat, slon, tx_h, _)| (*slat, *slon, *tx_h))
        .collect();
    let viable = session
        .seek_repeater_link_batch(lat, lon, draft_h, &endpoints, &rf_json)
        .map_err(|e| LinksError(format!("coords link batch: {e:#}")))?;

    let mut records = Vec::new();
    for ((slug, site_lat, site_lon, _, hop_range_km), linked) in
        candidates.into_iter().zip(viable.into_iter())
    {
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
            "hop_range_km": hop_range_km,
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

pub fn site_pair_link_detail(
    session: &Session,
    preset_path: &Path,
    preset: &Preset,
    boards: Option<&BoardViewshedResolver>,
    slug_a: &str,
    slug_b: &str,
) -> Result<Value, LinksError> {
    let (a, b) = canonical_site_pair(slug_a, slug_b);
    let site_a = preset
        .sites
        .get(&a)
        .ok_or_else(|| LinksError(format!("unknown site {a}")))?;
    let site_b = preset
        .sites
        .get(&b)
        .ok_or_else(|| LinksError(format!("unknown site {b}")))?;
    let boards = boards
        .cloned()
        .or_else(|| Some(load_board_viewshed(preset_path)));
    let hop_range_km = pair_hop_range_km(preset, site_a, site_b, boards.as_ref());
    let rf_json = rf_json_for_preset(preset).map_err(|e| LinksError(e.to_string()))?;
    let lat_a = site_a.loc[0];
    let lon_a = site_a.loc[1];
    let tx_a = resolved_site_tx_height_m(preset, site_a).max(1.0);
    let lat_b = site_b.loc[0];
    let lon_b = site_b.loc[1];
    let tx_b = resolved_site_tx_height_m(preset, site_b).max(1.0);
    let distance_km =
        (splatter::propagate::haversine_m(lat_a, lon_a, lat_b, lon_b) / 1000.0 * 10.0).round() / 10.0;
    let in_hop_range = pair_within_hop_range_sites(preset, site_a, site_b, boards.as_ref());
    let boards_ref = boards.as_ref().expect("boards loaded");
    let layout = project_layout(preset_path).map_err(|e| LinksError(e.to_string()))?;
    let cached_meta = read_pair_cache(&layout, &a, &b).map_err(|e| LinksError(e.to_string()))?;
    let is_manual = cached_meta.as_ref().is_some_and(|c| c.manual);

    if !in_hop_range && is_manual {
        let kind = cached_meta
            .as_ref()
            .map(|c| c.kind.as_str())
            .unwrap_or("none");
        return Ok(json!({
            "a": a,
            "b": b,
            "linked": true,
            "manual": true,
            "kind": kind,
            "forward_margin_db": cached_meta.as_ref().and_then(|c| c.forward_margin_db),
            "reverse_margin_db": cached_meta.as_ref().and_then(|c| c.reverse_margin_db),
            "margin_db": Value::Null,
            "distance_km": cached_meta.as_ref().map(|c| c.distance_km).unwrap_or(distance_km),
            "hop_range_km": hop_range_km,
        }));
    }

    let (forward_margin_db, reverse_margin_db, kind_str, linked, manual) = if in_hop_range {
        let fp = pair_link_fingerprint(preset, boards_ref, &a, &b)
            .map_err(|e| LinksError(e.to_string()))?;
        if let Some(cached) = read_pair_cache(&layout, &a, &b)
            .map_err(|e| LinksError(e.to_string()))?
        {
            if cached.links_model == LINKS_MODEL && cached.fingerprint == fp {
                let kind = cached.kind.as_str();
                return Ok(json!({
                    "a": a,
                    "b": b,
                    "linked": cached.kind.mesh_linked(cached.manual),
                    "manual": cached.manual,
                    "kind": kind,
                    "forward_margin_db": cached.forward_margin_db.map(|m| (m * 10.0).round() / 10.0),
                    "reverse_margin_db": cached.reverse_margin_db.map(|m| (m * 10.0).round() / 10.0),
                    "margin_db": cached.forward_margin_db.and_then(|f| {
                        cached.reverse_margin_db.map(|r| (f.min(r) * 10.0).round() / 10.0)
                    }).or_else(|| cached.forward_margin_db.or(cached.reverse_margin_db).map(|m| (m * 10.0).round() / 10.0)),
                    "distance_km": cached.distance_km,
                    "hop_range_km": hop_range_km,
                }));
            }
        }
        let legs = session
            .site_mesh_pair_legs(&[(lat_a, lon_a, tx_a, lat_b, lon_b, tx_b)], &rf_json)
            .map_err(|e| LinksError(e.to_string()))?
            .into_iter()
            .next()
            .unwrap_or(LinkLegs {
                forward_margin_db: None,
                reverse_margin_db: None,
            });
        let record = cache_record_from_legs(preset, boards_ref, &a, &b, legs)
            .map_err(|e| LinksError(e.to_string()))?;
        write_pair_cache(&layout, &record).map_err(|e| LinksError(e.to_string()))?;
        let kind = record.kind.as_str();
        let manual = read_pair_cache(&layout, &a, &b)
            .map_err(|e| LinksError(e.to_string()))?
            .map(|c| c.manual)
            .unwrap_or(is_manual);
        (
            record.forward_margin_db,
            record.reverse_margin_db,
            kind.to_string(),
            record.kind.mesh_linked(manual),
            manual,
        )
    } else {
        (None, None, "none".to_string(), false, is_manual)
    };

    let margin_db = match (forward_margin_db, reverse_margin_db) {
        (Some(f), Some(r)) if f.is_finite() && r.is_finite() => Some(f.min(r)),
        (Some(f), _) if f.is_finite() => Some(f),
        (_, Some(r)) if r.is_finite() => Some(r),
        _ => None,
    };
    Ok(json!({
        "a": a,
        "b": b,
        "linked": if manual { true } else { linked },
        "manual": manual,
        "kind": kind_str,
        "forward_margin_db": forward_margin_db.map(|m| (m * 10.0).round() / 10.0),
        "reverse_margin_db": reverse_margin_db.map(|m| (m * 10.0).round() / 10.0),
        "margin_db": margin_db.map(|m| (m * 10.0).round() / 10.0),
        "distance_km": distance_km,
        "hop_range_km": hop_range_km,
    }))
}

#[cfg(test)]
mod tests {
    use std::collections::HashSet;
    use std::fs;

    use peaky_preset::{
        load_preset, prune_link_cache, read_pair_cache, remove_preset_site, write_pair_cache,
        project::ProjectLayout, LinkCacheKind, PairLinkCacheRecord, LINKS_MODEL,
    };

    use super::*;

    #[test]
    fn canonical_pair_orders() {
        assert_eq!(canonical_site_pair("b", "a"), ("a".into(), "b".into()));
    }

    #[test]
    fn remove_site_prunes_pair_files_and_clears_mesh_cache() {
        let dir = tempfile::tempdir().unwrap();
        let config = dir.path().join("config.yaml");
        fs::write(&config, "simulation:\n  radius_km: 50\n").unwrap();
        fs::write(
            dir.path().join("sites.yaml"),
            "sites:\n  alpha:\n    name: Alpha\n    loc: [36.0, -116.0]\n  beta:\n    name: Beta\n    loc: [36.1, -116.1]\n",
        )
        .unwrap();
        let layout = ProjectLayout::from_config_path(&config).unwrap();
        write_pair_cache(
            &layout,
            &PairLinkCacheRecord {
                a: "alpha".into(),
                b: "beta".into(),
                fingerprint: "fp".into(),
                links_model: LINKS_MODEL.into(),
                forward_margin_db: Some(1.0),
                reverse_margin_db: Some(1.0),
                kind: LinkCacheKind::TwoWay,
                distance_km: 12.0,
                at: "2026-10-14".into(),
                manual: false,
                field_at: None,
            },
        )
        .unwrap();

        remove_preset_site(&config, "beta").unwrap();
        let preset = load_preset(&config).unwrap();
        let slugs: HashSet<String> = preset.sites.keys().cloned().collect();
        prune_link_cache(&layout, &slugs).unwrap();
        assert!(read_pair_cache(&layout, "alpha", "beta").unwrap().is_none());

        let payload = json!({
            "status": "ready",
            "links_model": LINKS_MODEL,
            "links": [{"a": "alpha", "b": "beta", "linked": true, "manual": false, "kind": "two_way"}],
            "geojson": {
                "type": "FeatureCollection",
                "features": [{
                    "type": "Feature",
                    "geometry": { "type": "LineString", "coordinates": [[-116.0, 36.0], [-116.1, 36.1]] },
                    "properties": { "a": "alpha", "b": "beta", "kind": "two_way", "distance_km": 12.0 }
                }]
            }
        });
        store_project_site_links_cache(&config, &preset, &payload).unwrap();
        assert!(cached_project_site_links(&config, &preset).unwrap().is_some());
        invalidate_project_site_links_cache(&config);
        assert!(cached_project_site_links(&config, &preset).unwrap().is_none());
    }
}
