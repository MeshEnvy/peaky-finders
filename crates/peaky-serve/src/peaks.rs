//! Eligible-peaks catalog + place access API.

use std::path::Path;
use std::sync::Arc;

use crate::peaks_cache::cached_peaks_list;
use anyhow::{Context, Result};
use peaky_peaks::{
    ensure_site_access_options_on_probe, load_probe_cache, probe_cache_path,
    project_gtlf_fingerprint, project_pbf_fingerprint, warm_place_access_with_routing,
    ProjectRouting, SessionElev, OSM_ROUTE_PAD_DEG,
};
use peaky_preset::{
    ensure_roads_book, load_latest_peaks_index, load_preset, prefs_key_for_project,
    resolve_site_display_route, roads_global_fingerprint, roads_local_fingerprint, PeakHikeProfile,
    PeakJeepProfile, PlaceAccess, SiteAccessOption,
};
use serde_json::{json, Value};
use splatter::Session;

fn slim_hike(hike: &PeakHikeProfile) -> PeakHikeProfile {
    let mut out = hike.clone();
    out.histogram.clear();
    out.difficulty = peaky_preset::hike_difficulty(
        out.max_grade_pct,
        out.avg_grade_pct,
        out.horiz_m,
        out.gain_m,
    )
    .to_string();
    out
}

fn slim_jeep(jeep: &PeakJeepProfile) -> PeakJeepProfile {
    let mut out = jeep.clone();
    out.histogram.clear();
    out
}

fn slim_jeep_rated(jeep: &PeakJeepProfile) -> PeakJeepProfile {
    let mut out = slim_jeep(jeep);
    out.difficulty = peaky_preset::jeep_difficulty(&jeep.segments).to_string();
    out
}

fn jeep_access_json(jeep: &PeakJeepProfile) -> Value {
    let slim = slim_jeep_rated(jeep);
    let mut value = serde_json::to_value(&slim).unwrap_or_else(|_| json!({}));
    if let Some(obj) = value.as_object_mut() {
        obj.remove("histogram");
        obj.remove("segments");
        if let Some(reason) = peaky_preset::jeep_class_reason(&jeep.segments) {
            obj.insert("osm_highway".into(), json!(reason.highway));
            obj.insert("osm_tracktype".into(), json!(reason.tracktype));
            obj.insert(
                "osm_class_m".into(),
                json!((reason.dist_m * 10.0).round() / 10.0),
            );
        }
    }
    value
}

fn route_access_json(route: &peaky_preset::AccessRoute) -> Value {
    json!({
        "paved_lat": route.paved_loc.map(|loc| loc[0]),
        "paved_lon": route.paved_loc.map(|loc| loc[1]),
        "road_lat": route.road_loc.map(|loc| loc[0]),
        "road_lon": route.road_loc.map(|loc| loc[1]),
        "hike_m": route.hike_m,
        "max_slope_deg": route.max_slope_deg,
        "hike": route.hike.as_ref().map(slim_hike),
        "trail_lat": route.trail_loc.map(|loc| loc[0]),
        "trail_lon": route.trail_loc.map(|loc| loc[1]),
        "trail_m": route.trail_m,
        "trail": route.trail.as_ref().map(slim_hike),
        "jeep_m": route.jeep_m,
        "jeep": route.jeep.as_ref().map(jeep_access_json),
        "hike_difficulty": route.hike_difficulty,
        "jeep_difficulty": route.jeep_difficulty,
        "access_difficulty": route.access_difficulty,
        "budget": route.budget,
    })
}

fn site_option_json(opt: &SiteAccessOption, selected: bool) -> Value {
    let mut value = route_access_json(&opt.route);
    if let Some(obj) = value.as_object_mut() {
        obj.insert("id".into(), json!(opt.id));
        obj.insert("recommended".into(), json!(opt.recommended));
        obj.insert("selected".into(), json!(selected));
        obj.insert("passes_budget".into(), json!(opt.passes_budget));
    }
    value
}

fn access_json(
    slug: &str,
    access: &PlaceAccess,
    site_picker: bool,
    access_choice: Option<&str>,
) -> Value {
    let display = if site_picker {
        resolve_site_display_route(access, access_choice)
    } else {
        access.recommended.clone()
    };
    let mut value = route_access_json(&display);
    if let Some(obj) = value.as_object_mut() {
        obj.insert("slug".into(), json!(slug));
        obj.insert("status".into(), json!("ready"));
        obj.insert("probe_key".into(), json!(access.probe_key));
        obj.insert("eligible".into(), json!(access.eligible));
        if site_picker {
            if let Some(choice) = access_choice {
                obj.insert("access_choice".into(), json!(choice));
            }
            if access.site_options_rev >= SITE_OPTIONS_REV {
                if let Some(ref options) = access.site_options {
                    let list: Vec<Value> = options
                        .iter()
                        .map(|o| site_option_json(o, access_choice == Some(o.id.as_str())))
                        .collect();
                    obj.insert("options".into(), json!(list));
                }
            }
        } else if let Some(ref budget_route) = access.budget_route {
            obj.insert("budget_route".into(), route_access_json(budget_route));
        }
    }
    value
}

pub fn access_payload_json(slug: &str, access: &PlaceAccess, preset_path: &Path) -> Value {
    let preset = load_preset(preset_path).unwrap_or_default();
    let site_picker = place_uses_site_access_picker(&preset, slug);
    let access_choice = preset
        .sites
        .get(slug)
        .and_then(|s| s.access_choice.as_deref());
    access_json(slug, access, site_picker, access_choice)
}

pub fn list_peaks_payload(preset_path: &Path) -> Result<Value> {
    cached_peaks_list(preset_path)
}

pub const SITE_OPTIONS_REV: u8 = peaky_peaks::SITE_ACCESS_OPTIONS_REV;

fn site_options_ready(probe: &PlaceAccess) -> bool {
    probe.site_options_rev >= SITE_OPTIONS_REV
        && probe
            .site_options
            .as_ref()
            .is_some_and(|options| !options.is_empty())
}

fn place_uses_site_access_picker(preset: &peaky_preset::Preset, place_slug: &str) -> bool {
    preset.sites.contains_key(place_slug) || place_slug == "_draft"
}

fn resolve_point(
    preset_path: &Path,
    place_slug: &str,
    warm_lat: Option<f64>,
    warm_lon: Option<f64>,
) -> Result<(f64, f64)> {
    if let (Some(lat), Some(lon)) = (warm_lat, warm_lon) {
        return Ok((lat, lon));
    }
    let project_dir = preset_path.parent().context("preset parent")?;
    if let Some((_, index)) = load_latest_peaks_index(project_dir)? {
        if let Some(row) = index.peaks.get(place_slug) {
            return Ok((row.lat(), row.lon()));
        }
    }
    let preset = load_preset(preset_path)?;
    if let Some(site) = preset.sites.get(place_slug) {
        return Ok((site.loc[0], site.loc[1]));
    }
    anyhow::bail!("unknown place slug {place_slug}")
}

fn probe_cache_matches_roads(preset_path: &Path, lat: f64, lon: f64, probe: &PlaceAccess) -> bool {
    let Ok(roads) = ensure_roads_book(preset_path) else {
        return false;
    };
    let global_fp = roads_global_fingerprint(&roads);
    let max_jeep_m = OSM_ROUTE_PAD_DEG * 111_320.0;
    let local_fp = roads_local_fingerprint(&roads, lat, lon, max_jeep_m);
    probe.roads_global_fp.as_deref() == Some(global_fp.as_str())
        && probe.roads_local_fp.as_deref() == Some(local_fp.as_str())
}

/// Read precomputed probe JSON from disk — no OSM/GTLF/DEM work.
pub fn place_access_from_probe_cache(
    preset_path: &Path,
    place_slug: &str,
    warm_lat: Option<f64>,
    warm_lon: Option<f64>,
) -> Result<Option<Value>> {
    let (lat, lon) = resolve_point(preset_path, place_slug, warm_lat, warm_lon)?;
    let project_dir = preset_path.parent().context("preset parent")?;
    let gtlf = project_gtlf_fingerprint(project_dir);
    let pbf = project_pbf_fingerprint(project_dir);
    let prefs_key = prefs_key_for_project(preset_path, &gtlf, &pbf)?;
    let cache_path = probe_cache_path(project_dir, &prefs_key, lat, lon);
    if let Some(probe) = load_probe_cache(&cache_path) {
        if !probe_cache_matches_roads(preset_path, lat, lon, &probe) {
            return Ok(None);
        }
        let preset = load_preset(preset_path)?;
        let site_picker = place_uses_site_access_picker(&preset, place_slug);
        let access_choice = preset
            .sites
            .get(place_slug)
            .and_then(|s| s.access_choice.as_deref());
        return Ok(Some(access_json(
            place_slug,
            &probe,
            site_picker,
            access_choice,
        )));
    }
    Ok(None)
}

/// Booked sites need ``site_options`` on the probe cache before a cache-first GET is enough.
pub fn site_probe_cache_needs_options(
    preset_path: &Path,
    place_slug: &str,
    warm_lat: Option<f64>,
    warm_lon: Option<f64>,
) -> Result<bool> {
    let preset = load_preset(preset_path)?;
    if !preset.sites.contains_key(place_slug) {
        return Ok(false);
    }
    let (lat, lon) = resolve_point(preset_path, place_slug, warm_lat, warm_lon)?;
    let project_dir = preset_path.parent().context("preset parent")?;
    let gtlf = project_gtlf_fingerprint(project_dir);
    let pbf = project_pbf_fingerprint(project_dir);
    let prefs_key = prefs_key_for_project(preset_path, &gtlf, &pbf)?;
    let cache_path = probe_cache_path(project_dir, &prefs_key, lat, lon);
    let Some(probe) = load_probe_cache(&cache_path) else {
        return Ok(true);
    };
    if !probe_cache_matches_roads(preset_path, lat, lon, &probe) {
        return Ok(true);
    }
    Ok(!site_options_ready(&probe))
}

/// How `GET /access` and `POST /access/warm` share one probe.
///
/// A cache read must not load the routing graph. A miss is computed once, on
/// the shared in-memory graph, so the paved-reach tree is not built again for
/// the same point.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PlaceAccessAction {
    ReturnCached,
    NotCached,
    Compute,
}

pub fn place_access_action(warm: bool, cache_hit: bool, needs_options: bool) -> PlaceAccessAction {
    if cache_hit && !(warm && needs_options) {
        PlaceAccessAction::ReturnCached
    } else if !warm {
        PlaceAccessAction::NotCached
    } else {
        PlaceAccessAction::Compute
    }
}

/// Single-flight key for one destination. Same rounding as the probe file name.
pub fn place_access_flight_key(
    preset_path: &Path,
    place_slug: &str,
    warm_lat: Option<f64>,
    warm_lon: Option<f64>,
) -> Result<String> {
    let (lat, lon) = resolve_point(preset_path, place_slug, warm_lat, warm_lon)?;
    Ok(format!("{lat:.5},{lon:.5}"))
}

pub fn place_access_payload(
    preset_path: &Path,
    session: &Arc<Session>,
    place_slug: &str,
    warm_if_missing: bool,
    warm_lat: Option<f64>,
    warm_lon: Option<f64>,
    routing: Option<&ProjectRouting>,
) -> Result<Value> {
    let (lat, lon) = resolve_point(preset_path, place_slug, warm_lat, warm_lon)?;
    let preset = load_preset(preset_path)?;
    let site_picker = place_uses_site_access_picker(&preset, place_slug);
    let access_choice = preset
        .sites
        .get(place_slug)
        .and_then(|s| s.access_choice.as_deref());
    let mut access = if let Some(routing) = routing {
        let meta = preset.resolved_access_meta();
        warm_place_access_with_routing(preset_path, session, routing, &meta, lat, lon)?
    } else {
        None
    };
    if let Some(ref mut probe) = access {
        if site_picker {
            if let Some(routing) = routing {
                let meta = preset.resolved_access_meta();
                let elev = SessionElev(session.as_ref());
                ensure_site_access_options_on_probe(
                    preset_path.parent().context("preset parent")?,
                    &elev,
                    routing,
                    &meta,
                    lat,
                    lon,
                    probe,
                )?;
            }
        }
        return Ok(access_json(place_slug, probe, site_picker, access_choice));
    }
    if warm_if_missing {
        anyhow::bail!("access not found for {place_slug}");
    }
    anyhow::bail!("access not found for {place_slug}")
}

pub fn catalog_peaks_filtered(
    preset_path: &Path,
    keep: impl Fn(f64, f64) -> bool,
) -> Result<(Vec<(f64, f64, f64)>, usize), String> {
    let catalog = crate::peaks_cache::cached_peaks_catalog_thin(preset_path)
        .map_err(|e| format!("load peaks catalog: {e}"))?;
    let n_catalog = catalog.peaks.len();
    if n_catalog == 0 {
        return Err("peaks catalog is empty; run peaky peaks build".into());
    }
    let peaks = catalog
        .peaks
        .values()
        .filter(|entry| entry.eligible)
        .filter(|entry| keep(entry.lat(), entry.lon()))
        .map(|entry| (entry.lon(), entry.lat(), entry.elev_m.unwrap_or(0.0)))
        .collect();
    Ok((peaks, n_catalog))
}

#[cfg(test)]
mod tests {
    use super::*;
    use peaky_peaks::save_probe_cache;
    use peaky_preset::model::{
        AccessProbe, AccessRoute, PeakHikeProfile, PeakHikeProfilePoint, PeakJeepProfile,
        PeakJeepProfilePoint,
    };
    use std::fs;

    fn stamp_probe_roads(config: &Path, lat: f64, lon: f64, mut probe: AccessProbe) -> AccessProbe {
        let roads = ensure_roads_book(config).unwrap();
        probe.roads_global_fp = Some(roads_global_fingerprint(&roads));
        let max_jeep_m = OSM_ROUTE_PAD_DEG * 111_320.0;
        probe.roads_local_fp = Some(roads_local_fingerprint(&roads, lat, lon, max_jeep_m));
        probe
    }

    #[test]
    fn place_access_from_probe_cache_returns_profiles() {
        let dir = tempfile::tempdir().unwrap();
        let config = dir.path().join("config.yaml");
        fs::write(&config, "simulation: {}\nscan: {}\n").unwrap();

        let lat = 39.12345;
        let lon = -117.54321;
        let gtlf = project_gtlf_fingerprint(dir.path());
        let pbf = project_pbf_fingerprint(dir.path());
        let prefs_key = prefs_key_for_project(&config, &gtlf, &pbf).unwrap();

        let probe = AccessProbe {
            probe_key: Some("test-key".into()),
            eligible: true,
            recommended: AccessRoute {
                hike_m: Some(1200.0),
                jeep_m: Some(8000.0),
                hike: Some(PeakHikeProfile {
                    hike_m_3d: 1200.0,
                    horiz_m: 1100.0,
                    gain_m: 400.0,
                    loss_m: 0.0,
                    max_slope_deg: 25.0,
                    max_grade_pct: 40.0,
                    avg_grade_pct: 15.0,
                    difficulty: "moderate".into(),
                    profile: vec![
                        PeakHikeProfilePoint {
                            dist_m: 0.0,
                            elev_m: 1800.0,
                            lat: 39.12,
                            lon: -117.54,
                        },
                        PeakHikeProfilePoint {
                            dist_m: 1100.0,
                            elev_m: 2200.0,
                            lat,
                            lon,
                        },
                    ],
                    histogram: vec![],
                }),
                jeep: Some(PeakJeepProfile {
                    jeep_m_3d: 8000.0,
                    horiz_m: 7900.0,
                    gain_m: 200.0,
                    loss_m: 50.0,
                    max_slope_deg: 8.0,
                    max_grade_pct: 12.0,
                    avg_grade_pct: 3.0,
                    difficulty: "easy".into(),
                    profile: vec![
                        PeakJeepProfilePoint {
                            dist_m: 0.0,
                            elev_m: 1600.0,
                            lat: 39.11,
                            lon: -117.55,
                        },
                        PeakJeepProfilePoint {
                            dist_m: 7900.0,
                            elev_m: 1800.0,
                            lat: 39.12,
                            lon: -117.54,
                        },
                    ],
                    histogram: vec![],
                    segments: vec![],
                    jeep_line: vec![],
                }),
                ..AccessRoute::default()
            },
            budget_route: None,
            site_options: None,
            site_options_rev: 0,
            ..Default::default()
        };
        let probe = stamp_probe_roads(&config, lat, lon, probe);

        let cache_path = probe_cache_path(dir.path(), &prefs_key, lat, lon);
        save_probe_cache(&cache_path, &probe).unwrap();

        let payload = place_access_from_probe_cache(&config, "test-peak", Some(lat), Some(lon))
            .unwrap()
            .expect("probe cache hit");
        assert_eq!(
            payload.get("slug").and_then(|v| v.as_str()),
            Some("test-peak")
        );
        assert_eq!(
            payload.get("status").and_then(|v| v.as_str()),
            Some("ready")
        );
        let hike_profile = payload
            .pointer("/hike/profile")
            .and_then(|v| v.as_array())
            .map(|a| a.len())
            .unwrap_or(0);
        let jeep_profile = payload
            .pointer("/jeep/profile")
            .and_then(|v| v.as_array())
            .map(|a| a.len())
            .unwrap_or(0);
        assert!(hike_profile >= 2, "expected hike profile points");
        assert!(jeep_profile >= 2, "expected jeep profile points");
    }

    #[test]
    fn place_access_from_probe_cache_hit_even_when_coords_from_query() {
        let dir = tempfile::tempdir().unwrap();
        let config = dir.path().join("config.yaml");
        fs::write(&config, "simulation: {}\nscan: {}\n").unwrap();
        let lat = 40.04345;
        let lon = -118.77774;
        let gtlf = project_gtlf_fingerprint(dir.path());
        let pbf = project_pbf_fingerprint(dir.path());
        let prefs_key = prefs_key_for_project(&config, &gtlf, &pbf).unwrap();
        let probe = AccessProbe {
            probe_key: Some("site-key".into()),
            eligible: true,
            recommended: AccessRoute {
                hike_m: Some(500.0),
                jeep_m: Some(3000.0),
                ..AccessRoute::default()
            },
            budget_route: None,
            site_options: None,
            site_options_rev: 0,
            ..Default::default()
        };
        let probe = stamp_probe_roads(&config, lat, lon, probe);
        save_probe_cache(&probe_cache_path(dir.path(), &prefs_key, lat, lon), &probe).unwrap();
        let payload = place_access_from_probe_cache(&config, "toulon-south", Some(lat), Some(lon))
            .unwrap()
            .expect("site coord probe hit");
        assert_eq!(
            payload.get("slug").and_then(|v| v.as_str()),
            Some("toulon-south")
        );
        assert_eq!(payload.get("jeep_m").and_then(|v| v.as_f64()), Some(3000.0));
    }

    #[test]
    fn place_access_from_probe_cache_miss_returns_none() {
        let dir = tempfile::tempdir().unwrap();
        let config = dir.path().join("config.yaml");
        fs::write(&config, "simulation: {}\nscan: {}\n").unwrap();
        let hit =
            place_access_from_probe_cache(&config, "missing", Some(39.0), Some(-117.0)).unwrap();
        assert!(hit.is_none());
    }

    #[test]
    fn cache_read_does_not_compute() {
        assert_eq!(
            place_access_action(false, true, true),
            PlaceAccessAction::ReturnCached
        );
        assert_eq!(
            place_access_action(false, false, false),
            PlaceAccessAction::NotCached
        );
    }

    #[test]
    fn warm_computes_only_on_miss_or_missing_options() {
        assert_eq!(
            place_access_action(true, true, false),
            PlaceAccessAction::ReturnCached
        );
        assert_eq!(
            place_access_action(true, true, true),
            PlaceAccessAction::Compute
        );
        assert_eq!(
            place_access_action(true, false, false),
            PlaceAccessAction::Compute
        );
    }
}
