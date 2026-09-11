//! ``peaky peaks`` orchestration.

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::time::Instant;

use anyhow::{Context, Result};
use peaky_geo::{
    collect_role_geometry, eligible_land_dem_mask_dir, load_or_build_eligible_land_parts,
    LonLatBBox,
};
use peaky_preset::LandLayerRole;
use peaky_preset::{
    load_peaks_catalog, load_preset, preserve_denied_entries, write_peaks_catalog,
    PeakAccessRules, PeakCatalogEntry, PeaksCatalog, Preset, resolved_dem_fetch_max_workers,
    resolved_skadi_mirror_dir_for_project,
};
use std::sync::Arc;
use splatter::peaks::LandFilterIndex;
use splatter::Session;
use tracing::info;

use crate::gnis::load_gnis_candidates;
use crate::hike::{
    clamp_calibrated_slope_deg, haversine_m, profile_hike, profile_passes,
    snap_to_local_summit_filtered, HikeSampleElev, DEFAULT_MAX_HIKE_M, DEFAULT_SUMMIT_SNAP_M,
};
use crate::osm::{build_jeep_road_index, ensure_osm_pbf, JeepRoadIndex};
use crate::universe::{dedup_nearby, seed_candidates_from_sites, slug_for_candidate, RawCandidate};

pub const RAZORBACK_CALIB_SITE: &str = "old-razorback";
const SITE_DEDUP_M: f64 = 300.0;
const CANDIDATE_DEDUP_M: f64 = 300.0;
const DEM_BIN_M: f64 = 1500.0;

#[derive(Debug, Clone)]
struct KeptSummit {
    lat: f64,
    lon: f64,
    elev: f64,
    slug: String,
}

struct SessionElev<'a>(&'a Session);

impl HikeSampleElev for SessionElev<'_> {
    fn sample_elev_m(&self, lat: f64, lon: f64) -> f64 {
        self.0.sample_elev_m(lat, lon)
    }
}

#[derive(Debug, Clone)]
pub struct PeaksBuildOptions {
    pub bbox: Option<(f64, f64, f64, f64)>,
    pub force_osm: bool,
    pub verbose: bool,
}

#[derive(Debug, Clone, Default)]
pub struct PeaksBuildSummary {
    pub kept: usize,
    pub denied: usize,
    pub dropped_land: usize,
    pub dropped_road: usize,
    pub dropped_hike: usize,
    pub dropped_slope: usize,
    pub dropped_dedup: usize,
    pub gnis: usize,
    pub site_seeds: usize,
    pub dem: usize,
    pub max_slope_deg: f64,
    pub output: PathBuf,
}

#[derive(Default)]
struct DropCounts {
    land: usize,
    road: usize,
    hike: usize,
    slope: usize,
    dedup: usize,
}

pub fn build_peaks_catalog(
    preset_path: &Path,
    opts: PeaksBuildOptions,
) -> Result<PeaksBuildSummary> {
    let t0 = Instant::now();
    let preset = load_preset(preset_path).context("load preset")?;
    let project_dir = preset_path
        .parent()
        .context("preset path has no parent")?;

    let bbox = resolve_bbox(preset_path, &preset, opts.bbox)?;
    info!(
        west = bbox.west,
        south = bbox.south,
        east = bbox.east,
        north = bbox.north,
        "peaks: scan bbox"
    );

    let land_parts =
        load_or_build_eligible_land_parts(preset_path, Some(bbox)).context("eligible land")?;
    let land_index = Arc::new(land_parts.index());
    info!(
        digest = %land_parts.digest,
        empty = land_parts.is_empty(),
        "peaks: eligible land ready"
    );

    let pbf = ensure_osm_pbf(project_dir, opts.force_osm)?;
    let roads = build_jeep_road_index(
        &pbf,
        bbox.west,
        bbox.south,
        bbox.east,
        bbox.north,
    )?;

    let mirror = resolved_skadi_mirror_dir_for_project(project_dir);
    let dem_workers = resolved_dem_fetch_max_workers(&preset);
    let session = Session::new(mirror, opts.verbose, dem_workers);
    session
        .ensure_tiles_for_bounds(bbox.west, bbox.south, bbox.east, bbox.north)
        .context("preload DEM for bbox")?;

    let mut rules = PeakAccessRules::default();
    rules.max_hike_m = DEFAULT_MAX_HIKE_M;
    rules.max_slope_deg = calibrate_max_slope(
        &preset,
        &session,
        &roads,
        rules.max_hike_m,
    )?;
    info!(
        max_hike_m = rules.max_hike_m,
        max_slope_deg = rules.max_slope_deg,
        "peaks: access rules"
    );

    let mut universe: Vec<RawCandidate> = Vec::new();
    let site_seeds = seed_candidates_from_sites(&preset);
    info!(count = site_seeds.len(), "peaks: site seeds (eip/aw/installed)");
    universe.extend(site_seeds.clone());

    let gnis = load_gnis_candidates(
        project_dir,
        bbox.west,
        bbox.south,
        bbox.east,
        bbox.north,
    )?;
    info!(count = gnis.len(), "peaks: GNIS candidates");
    universe.extend(gnis.clone());

    let mask_dir = eligible_land_dem_mask_dir(preset_path, &land_parts.digest);
    let dem_peaks = load_dem_candidates(
        &session,
        land_index.clone(),
        bbox,
        &mask_dir,
    )?;
    info!(count = dem_peaks.len(), "peaks: DEM candidates on eligible land");
    universe.extend(dem_peaks.clone());

    universe = dedup_nearby(universe, CANDIDATE_DEDUP_M);
    universe.sort_by(|a, b| {
        b.elev_m
            .unwrap_or(0.0)
            .partial_cmp(&a.elev_m.unwrap_or(0.0))
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    info!(count = universe.len(), "peaks: universe after dedup");

    let site_locs: Vec<(f64, f64)> = preset
        .sites
        .values()
        .map(|s| (s.lat(), s.lon()))
        .collect();

    let previous = load_peaks_catalog(preset_path)?;
    let mut catalog = PeaksCatalog {
        generated_at: chrono_date(),
        rules: rules.clone(),
        entries: HashMap::new(),
    };

    let mut drops = DropCounts::default();
    let mut kept = 0usize;
    let mut existing_slugs: HashSet<String> = HashSet::new();
    let mut kept_summits: Vec<KeptSummit> = Vec::new();
    let elev = SessionElev(&session);
    let land = land_index.clone();

    for (i, cand) in universe.iter().enumerate() {
        if (i + 1) % 50 == 0 || i + 1 == universe.len() {
            info!(
                "[{}/{}] filtering… kept={} land={} road={} hike={} slope={}",
                i + 1,
                universe.len(),
                kept,
                drops.land,
                drops.road,
                drops.hike,
                drops.slope
            );
        }

        if is_near_any_site(cand.lat, cand.lon, &site_locs, SITE_DEDUP_M) {
            drops.dedup += 1;
            continue;
        }
        let Some((peak_lat, peak_lon, peak_elev)) = snap_to_local_summit_filtered(
            &elev,
            cand.lat,
            cand.lon,
            DEFAULT_SUMMIT_SNAP_M,
            30.0,
            |lat, lon| land.as_ref().contains(lon, lat),
        ) else {
            drops.land += 1;
            continue;
        };
        let replace_idx = kept_summits.iter().position(|k| {
            haversine_m(peak_lat, peak_lon, k.lat, k.lon) <= CANDIDATE_DEDUP_M
        });
        if let Some(idx) = replace_idx {
            if peak_elev <= kept_summits[idx].elev {
                drops.dedup += 1;
                continue;
            }
            catalog.entries.remove(&kept_summits[idx].slug);
            existing_slugs.remove(&kept_summits[idx].slug);
            kept_summits.remove(idx);
            kept = kept.saturating_sub(1);
        }
        let Some((road_lat, road_lon, road_m)) =
            roads.nearest_within(peak_lat, peak_lon, rules.max_hike_m)
        else {
            drops.road += 1;
            continue;
        };
        let Some(profile) = profile_hike(
            &elev,
            road_lat,
            road_lon,
            peak_lat,
            peak_lon,
            30.0,
        ) else {
            drops.hike += 1;
            continue;
        };
        if profile.hike_m_3d > rules.max_hike_m + 1.0 {
            drops.hike += 1;
            if opts.verbose {
                info!(
                    name = ?cand.name,
                    hike_m = profile.hike_m_3d,
                    "peaks: drop hike length"
                );
            }
            continue;
        }
        if profile.max_slope_deg > rules.max_slope_deg + 0.5 {
            drops.slope += 1;
            if opts.verbose {
                info!(
                    name = ?cand.name,
                    slope = profile.max_slope_deg,
                    "peaks: drop slope"
                );
            }
            continue;
        }
        if !profile_passes(&profile, rules.max_hike_m, rules.max_slope_deg) {
            drops.hike += 1;
            continue;
        }

        let elev_m = Some(peak_elev);

        let mut snapped = cand.clone();
        snapped.lat = peak_lat;
        snapped.lon = peak_lon;
        snapped.elev_m = elev_m;
        let slug = slug_for_candidate(&snapped, &existing_slugs);
        existing_slugs.insert(slug.clone());
        kept_summits.push(KeptSummit {
            lat: peak_lat,
            lon: peak_lon,
            elev: peak_elev,
            slug: slug.clone(),
        });
        catalog.entries.insert(
            slug,
            PeakCatalogEntry {
                name: cand.name.clone(),
                loc: [peak_lat, peak_lon],
                elev_m,
                source: cand.source.clone(),
                road_m: Some((road_m * 10.0).round() / 10.0),
                road_loc: Some([road_lat, road_lon]),
                hike_m: Some((profile.hike_m_3d * 10.0).round() / 10.0),
                max_slope_deg: Some((profile.max_slope_deg * 10.0).round() / 10.0),
                deny: None,
            },
        );
        kept += 1;
    }

    let denied = preserve_denied_entries(&mut catalog, &previous);
    info!(
        kept,
        denied,
        dropped_land = drops.land,
        dropped_road = drops.road,
        dropped_hike = drops.hike,
        dropped_slope = drops.slope,
        dropped_dedup = drops.dedup,
        elapsed_secs = t0.elapsed().as_secs_f64(),
        "peaks: filter complete"
    );

    write_peaks_catalog(preset_path, &catalog).context("write peaks.yaml")?;
    let output = peaky_preset::peaks_catalog_path(preset_path)?;

    info!(path = %output.display(), entries = catalog.entries.len(), "peaks: wrote peaks.yaml");

    Ok(PeaksBuildSummary {
        kept,
        denied,
        dropped_land: drops.land,
        dropped_road: drops.road,
        dropped_hike: drops.hike,
        dropped_slope: drops.slope,
        dropped_dedup: drops.dedup,
        gnis: gnis.len(),
        site_seeds: site_seeds.len(),
        dem: dem_peaks.len(),
        max_slope_deg: rules.max_slope_deg,
        output,
    })
}

fn calibrate_max_slope(
    preset: &Preset,
    session: &Session,
    roads: &JeepRoadIndex,
    max_road_m: f64,
) -> Result<f64> {
    let Some(site) = preset.sites.get(RAZORBACK_CALIB_SITE) else {
        info!(
            site = RAZORBACK_CALIB_SITE,
            "peaks: Razorback site missing — using default max_slope_deg=35"
        );
        return Ok(35.0);
    };
    let lat = site.lat();
    let lon = site.lon();
    session
        .ensure_tiles_for_points(&[(lat, lon)], max_road_m * 2.0)
        .context("DEM for Razorback calibration")?;
    let Some((road_lat, road_lon, road_m)) = roads.nearest_within(lat, lon, max_road_m * 4.0) else {
        info!("peaks: no OSM road near Razorback — using default max_slope_deg=35");
        return Ok(35.0);
    };
    let elev = SessionElev(session);
    let profile = profile_hike(&elev, road_lat, road_lon, lat, lon, 30.0)
        .context("Razorback hike profile")?;
    info!(
        road_m,
        hike_m = profile.hike_m_3d,
        max_slope_deg = profile.max_slope_deg,
        "peaks: Razorback calibration"
    );
    Ok(clamp_calibrated_slope_deg(profile.max_slope_deg))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::hike::HikeSampleElev;
    use crate::osm::test_index_from_points;

    struct FlatElev(f64);

    impl HikeSampleElev for FlatElev {
        fn sample_elev_m(&self, _lat: f64, _lon: f64) -> f64 {
            self.0
        }
    }

    #[test]
    fn peak_beyond_half_mile_road_fails_hike_gate() {
        let roads = test_index_from_points(&[(38.0, -117.0)]);
        let peak_lat = 38.0087;
        let peak_lon = -117.0;
        let road = roads.nearest_within(peak_lat, peak_lon, DEFAULT_MAX_HIKE_M);
        assert!(road.is_none(), "0.6 mi from road should exceed 805 m cap");
    }

    #[test]
    fn steeper_than_calibrated_ceiling_fails_slope_gate() {
        let roads = test_index_from_points(&[(38.0, -117.0)]);
        let peak_lat = 38.003;
        let peak_lon = -117.0;
        let (rlat, rlon, _) = roads
            .nearest_within(peak_lat, peak_lon, DEFAULT_MAX_HIKE_M)
            .unwrap();
        struct SteepRamp {
            base_lat: f64,
        }
        impl HikeSampleElev for SteepRamp {
            fn sample_elev_m(&self, lat: f64, _lon: f64) -> f64 {
                (lat - self.base_lat) * 45_000.0
            }
        }
        let ramp = SteepRamp { base_lat: 38.0 };
        let profile = profile_hike(&ramp, rlat, rlon, peak_lat, peak_lon, 30.0).unwrap();
        let ceiling = clamp_calibrated_slope_deg(profile.max_slope_deg - 5.0);
        assert!(profile.max_slope_deg > ceiling + 0.5);
        assert!(!profile_passes(&profile, DEFAULT_MAX_HIKE_M, ceiling));
    }

    #[test]
    fn flat_near_road_passes_both_gates() {
        let roads = test_index_from_points(&[(38.0, -117.0)]);
        let peak_lat = 38.0003;
        let peak_lon = -117.0;
        let (rlat, rlon, road_m) = roads
            .nearest_within(peak_lat, peak_lon, DEFAULT_MAX_HIKE_M)
            .unwrap();
        assert!(road_m < 100.0);
        let flat = FlatElev(2000.0);
        let profile = profile_hike(&flat, rlat, rlon, peak_lat, peak_lon, 30.0).unwrap();
        assert!(profile_passes(&profile, DEFAULT_MAX_HIKE_M, 45.0));
    }
}

fn load_dem_candidates(
    session: &Session,
    land: Arc<LandFilterIndex>,
    bbox: LonLatBBox,
    mask_dir: &Path,
) -> Result<Vec<RawCandidate>> {
    let _ = std::fs::create_dir_all(mask_dir);
    let peaks = session
        .binned_peaks_in_bounds(
            bbox.west,
            bbox.south,
            bbox.east,
            bbox.north,
            land,
            Some(mask_dir),
            DEM_BIN_M,
            None,
        )
        .context("DEM peak scan")?;
    Ok(peaks
        .into_iter()
        .map(|p| RawCandidate {
            name: None,
            lat: p.lat,
            lon: p.lon,
            elev_m: Some(p.elev_m),
            source: "dem".into(),
            seed_slug: None,
        })
        .collect())
}

fn resolve_bbox(
    preset_path: &Path,
    _preset: &Preset,
    override_bbox: Option<(f64, f64, f64, f64)>,
) -> Result<LonLatBBox> {
    if let Some(b) = override_bbox {
        return Ok(LonLatBBox::from_tuple(b));
    }
    let aoi = collect_role_geometry(preset_path, LandLayerRole::Aoi, None)
        .context("load AOI geometry")?;
    if let Some(b) = LonLatBBox::from_geometry(&aoi) {
        return Ok(b.padded(0.02));
    }
    Ok(LonLatBBox::new(-120.0, 35.0, -114.0, 42.0))
}

fn is_near_any_site(lat: f64, lon: f64, sites: &[(f64, f64)], min_m: f64) -> bool {
    sites
        .iter()
        .any(|(slat, slon)| crate::hike::haversine_m(lat, lon, *slat, *slon) <= min_m)
}

fn chrono_date() -> String {
    use std::time::SystemTime;
    let dur = SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .unwrap_or_default();
    let days = dur.as_secs() / 86_400;
    let (y, m, d) = days_to_ymd(days as i64);
    format!("{y:04}-{m:02}-{d:02}")
}

fn days_to_ymd(days: i64) -> (i64, u32, u32) {
    let z = days + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = mp + if mp < 10 { 3 } else { -9 };
    let y = y + if m <= 2 { 1 } else { 0 };
    (y, m as u32, d as u32)
}
