//! Single-point and region access probes for debugging catalog drops.

use std::path::Path;
use std::sync::Arc;

use anyhow::{Context, Result};
use peaky_geo::{eligible_land_dem_mask_dir, load_or_build_eligible_land_parts};
use peaky_preset::{load_preset, AccessMeta, AccessProbe, PeaksScanConfig};
use splatter::peaks::LandFilterIndex;
use splatter::Session;

use crate::access_probe::{probe_drop_reason, probe_explain, AccessContext};
use crate::access_warm::{load_routing_for_bbox, resolve_probe, ProjectRouting};
use crate::catalog_filter::FilterDrop;
use crate::corridor::point_in_region;
use crate::gnis::load_gnis_candidates;
use crate::hike::HikeSampleElev;
use crate::jeep::JeepLengthBudget;
use crate::osm::OsmRoutingOpts;
use crate::run::{
    filter_candidate, load_dem_candidates, resolve_scan, PeaksBuildOptions, PeaksFilterParams,
};
use crate::universe::{
    dedup_nearby_with_drops, seed_candidates_from_sites, RawCandidate,
};

struct SessionElev<'a>(&'a Session);

impl HikeSampleElev for SessionElev<'_> {
    fn sample_elev_m(&self, lat: f64, lon: f64) -> f64 {
        self.0.sample_elev_m(lat, lon)
    }
}

fn drop_label(d: FilterDrop) -> &'static str {
    match d {
        FilterDrop::Land => "land",
        FilterDrop::Region => "region",
        FilterDrop::Road => "road",
        FilterDrop::Hike => "hike",
        FilterDrop::Slope => "slope",
        FilterDrop::Jeep => "jeep",
    }
}

/// Unified access probe for one coordinate (catalog prefs + disk cache).
pub fn probe_access_point(
    preset_path: &Path,
    lat: f64,
    lon: f64,
    force_osm: bool,
) -> Result<AccessProbe> {
    let preset = load_preset(preset_path).context("load preset")?;
    let project_dir = preset_path.parent().context("preset parent")?;
    let scan = preset.scan.peaks.clone();
    let rules = preset.peak_access_rules();
    let access_meta = preset.resolved_access_meta();
    let osm_opts = OsmRoutingOpts::from(&access_meta);

    let pad = 0.05;
    let loaded = load_routing_for_bbox(
        project_dir,
        lon - pad,
        lat - pad,
        lon + pad,
        lat + pad,
        force_osm,
        &osm_opts,
    )?;

    let mirror = peaky_preset::resolved_skadi_mirror_dir_for_project(project_dir);
    let session = Session::new(
        mirror,
        false,
        peaky_preset::resolved_dem_fetch_max_workers(&preset),
    );
    session.ensure_tiles_for_points(&[(lat, lon)], access_meta.place_road_search_m)?;

    let jeep_budget = JeepLengthBudget::from_extreme(&scan.difficulty.jeep.extreme);
    let elev = SessionElev(&session);
    resolve_probe(
        project_dir,
        &elev,
        &loaded,
        &access_meta,
        &scan,
        lat,
        lon,
        rules.max_jeep_m,
        rules.max_slope_deg,
        jeep_budget,
    )
    .context("no paved anchor in loaded routing graph")
}

/// Like [`probe_access_point`] but also returns scored park candidates.
pub fn probe_access_point_explain(
    preset_path: &Path,
    lat: f64,
    lon: f64,
    force_osm: bool,
) -> Result<(AccessProbe, Vec<crate::access_probe::ProbeCandidateReport>)> {
    let preset = load_preset(preset_path).context("load preset")?;
    let project_dir = preset_path.parent().context("preset parent")?;
    let scan = preset.scan.peaks.clone();
    let rules = preset.peak_access_rules();
    let access_meta = preset.resolved_access_meta();
    let osm_opts = OsmRoutingOpts::from(&access_meta);

    let pad = 0.05;
    let loaded = load_routing_for_bbox(
        project_dir,
        lon - pad,
        lat - pad,
        lon + pad,
        lat + pad,
        force_osm,
        &osm_opts,
    )?;

    let mirror = peaky_preset::resolved_skadi_mirror_dir_for_project(project_dir);
    let session = Session::new(
        mirror,
        false,
        peaky_preset::resolved_dem_fetch_max_workers(&preset),
    );
    session.ensure_tiles_for_points(&[(lat, lon)], access_meta.place_road_search_m)?;

    let jeep_budget = JeepLengthBudget::from_extreme(&scan.difficulty.jeep.extreme);
    let trees = loaded.trees(rules.max_jeep_m, &jeep_budget);
    let elev = SessionElev(&session);
    let ctx = AccessContext {
        routing: loaded.osm(),
        trails: loaded.trails_opt(),
        tree: &trees.unconstrained,
        tree_budgeted: &trees.budgeted,
        max_jeep_m: rules.max_jeep_m,
        hike_path_max_m: access_meta.hike_path_max_m,
        profile_sample_m: access_meta.profile_sample_m,
        max_slope_deg: rules.max_slope_deg,
        jeep_budget,
        preference: scan.preference.clone(),
        difficulty: scan.difficulty.clone(),
    };
    probe_explain(&elev, lat, lon, &ctx).context("no paved anchor in loaded routing graph")
}

fn explain_candidate(
    cand: &RawCandidate,
    project_dir: &Path,
    session: &Session,
    land: &LandFilterIndex,
    project_routing: &ProjectRouting,
    scan_region: &crate::corridor::ScanRegion,
    rules: &peaky_preset::PeakAccessRules,
    access_meta: &AccessMeta,
    scan: &PeaksScanConfig,
    filter: PeaksFilterParams,
) -> (bool, String) {
    match filter_candidate(
        cand,
        project_dir,
        session,
        land,
        project_routing,
        scan_region,
        rules,
        access_meta,
        scan,
        filter,
    ) {
        Ok(pass) => (
            true,
            format!(
                "snap=({:.6},{:.6}) elev={:.0}m jeep={:.0}m trail={:.0}m hike={:.0}m",
                pass.peak_lat,
                pass.peak_lon,
                pass.peak_elev,
                pass.jeep_m,
                pass.trail_m,
                pass.hike_m_3d
            ),
        ),
        Err(drop) => (false, drop_label(drop).to_string()),
    }
}

/// List every catalog candidate in a bbox/corridor/polygon and why it passed or dropped.
pub fn probe_peaks_region(preset_path: &Path, opts: PeaksBuildOptions) -> Result<()> {
    let preset = load_preset(preset_path).context("load preset")?;
    let project_dir = preset_path.parent().context("preset parent")?;
    let peaks_cfg = preset.scan.peaks.clone();
    let filter = PeaksFilterParams::from(&peaks_cfg);
    let rules = preset.peak_access_rules();
    let (bbox, scan_region) = resolve_scan(preset_path, &preset, &opts)?;

    println!(
        "probe region bbox={:.4},{:.4},{:.4},{:.4} project={}",
        bbox.west, bbox.south, bbox.east, bbox.north, preset_path.display()
    );

    let land_parts =
        load_or_build_eligible_land_parts(preset_path, Some(bbox)).context("eligible land")?;
    let land_index = Arc::new(land_parts.index());
    println!(
        "land digest={} empty={}",
        land_parts.digest,
        land_parts.is_empty()
    );

    let access_meta = preset.resolved_access_meta();
    let osm_opts = OsmRoutingOpts::from(&access_meta);
    let loaded = load_routing_for_bbox(
        project_dir,
        bbox.west,
        bbox.south,
        bbox.east,
        bbox.north,
        opts.force_osm,
        &osm_opts,
    )?;
    println!("gtlf={}", loaded.gtlf_fingerprint);

    let mirror = peaky_preset::resolved_skadi_mirror_dir_for_project(project_dir);
    let session = Session::new(
        mirror,
        opts.verbose,
        peaky_preset::resolved_dem_fetch_max_workers(&preset),
    );
    session.ensure_tiles_for_bounds(bbox.west, bbox.south, bbox.east, bbox.north)?;

    let mut universe: Vec<RawCandidate> = Vec::new();
    let site_seeds = seed_candidates_from_sites(&preset, &peaks_cfg.seeds.site_tags);
    universe.extend(site_seeds.clone());
    let gnis = if peaks_cfg.seeds.gnis {
        load_gnis_candidates(
            project_dir,
            &preset.sources.gnis,
            bbox.west,
            bbox.south,
            bbox.east,
            bbox.north,
        )?
    } else {
        Vec::new()
    };
    universe.extend(gnis.clone());
    let mask_dir = eligible_land_dem_mask_dir(preset_path, &land_parts.digest);
    let dem_peaks = load_dem_candidates(
        &session,
        land_index.clone(),
        bbox,
        &mask_dir,
        preset.scan.peak_bin_size_m,
        peaks_cfg.min_prominence_m,
    )?;
    universe.extend(dem_peaks.clone());

    let pre_region = universe.len();
    let region_drops: Vec<RawCandidate> = universe
        .iter()
        .filter(|c| !point_in_region(&scan_region, c.lat, c.lon))
        .cloned()
        .collect();
    universe.retain(|c| point_in_region(&scan_region, c.lat, c.lon));
    let (mut kept, dedup_drops) =
        dedup_nearby_with_drops(universe, filter.candidate_dedup_m);
    kept.sort_by(|a, b| {
        b.elev_m
            .unwrap_or(0.0)
            .partial_cmp(&a.elev_m.unwrap_or(0.0))
            .unwrap_or(std::cmp::Ordering::Equal)
    });

    println!(
        "universe sites={} gnis={} dem={} after_bbox={} after_region={} after_dedup={}",
        site_seeds.len(),
        gnis.len(),
        dem_peaks.len(),
        pre_region.saturating_sub(region_drops.len()),
        pre_region.saturating_sub(region_drops.len()),
        kept.len()
    );

    let mut pass_n = 0usize;
    let mut snap_n = 0usize;
    let mut prom_n = 0usize;
    let mut jeep_n = 0usize;
    let mut slope_n = 0usize;
    let mut hike_n = 0usize;
    let mut region_n = 0usize;

    println!("\n=== CANDIDATES ===");
    for drop in &region_drops {
        println!(
            "DROP {}({:.6},{:.6}) region: outside corridor/polygon",
            drop.source, drop.lat, drop.lon
        );
    }
    for drop in &dedup_drops {
        println!(
            "DEDUP {}({:.6},{:.6}) collapsed into {}({:.6},{:.6}) ({:.0}m)",
            drop.dropped.source,
            drop.dropped.lat,
            drop.dropped.lon,
            drop.kept.source,
            drop.kept.lat,
            drop.kept.lon,
            drop.dist_m
        );
    }
    for cand in &kept {
        let (ok, reason) = explain_candidate(
            cand,
            project_dir,
            &session,
            land_index.as_ref(),
            &loaded,
            &scan_region,
            &rules,
            &access_meta,
            &peaks_cfg,
            filter,
        );
        let elev = cand
            .elev_m
            .map(|e| format!("{e:.0}m"))
            .unwrap_or_else(|| "-".into());
        if ok {
            pass_n += 1;
            println!(
                "PASS {}({:.6},{:.6}) elev={elev} {reason}",
                cand.source, cand.lat, cand.lon
            );
        } else {
            match reason.as_str() {
                "land" => snap_n += 1,
                "region" => region_n += 1,
                "jeep" => jeep_n += 1,
                "slope" => slope_n += 1,
                "hike" => hike_n += 1,
                _ => {}
            }
            if cand.source == "dem" && reason == "land" {
                prom_n += 1;
            }
            println!(
                "DROP {}({:.6},{:.6}) elev={elev} {reason}",
                cand.source, cand.lat, cand.lon
            );
        }
    }

    println!("\n=== COUNTS ===");
    println!(
        "pass={pass_n} snap={snap_n} prominence={prom_n} jeep={jeep_n} hike={hike_n} slope={slope_n} region_after_snap={region_n} region_clip={} dedup={}",
        region_drops.len(),
        dedup_drops.len()
    );
    Ok(())
}

/// Legacy verbose stage report (kept for deep debugging).
pub fn probe_peak(preset_path: &Path, lat: f64, lon: f64) -> Result<()> {
    let probe = probe_access_point(preset_path, lat, lon, false)?;
    println!("eligible={}", probe.eligible);
    if !probe.eligible {
        println!("drop={}", drop_label(probe_drop_reason(&probe)));
    }
    println!("{}", serde_yaml::to_string(&probe)?);
    Ok(())
}
