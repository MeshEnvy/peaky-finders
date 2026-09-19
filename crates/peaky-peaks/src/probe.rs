//! Single-point peak eligibility probe for debugging catalog drops.

use std::path::Path;

use anyhow::{Context, Result};
use peaky_geo::{load_or_build_eligible_land_parts, LonLatBBox};
use peaky_preset::{classify_hike, classify_jeep, ensure_access_meta, load_preset};
use splatter::Session;

use crate::access::build_access_from_park;
use crate::hike::{
    neighbor_prominence_m, profile_passes, snap_to_dem_local_max_filtered, HikeProfile,
    HikeSampleElev,
};
use crate::hike_route::resolve_hike;
use crate::osm::{build_osm_routing, ensure_osm_pbf, OsmRoutingOpts};
use crate::park::{park_candidates, select_park_and_hike, ON_ROAD_SNAP_M};
use crate::run::{filter_candidate, FilterDrop, PeaksFilterParams};
use crate::universe::RawCandidate;

struct SessionElev<'a>(&'a Session);

impl HikeSampleElev for SessionElev<'_> {
    fn sample_elev_m(&self, lat: f64, lon: f64) -> f64 {
        self.0.sample_elev_m(lat, lon)
    }
}

fn drop_label(d: FilterDrop) -> &'static str {
    match d {
        FilterDrop::SiteDedup => "site_dedup",
        FilterDrop::Land => "land",
        FilterDrop::Region => "region",
        FilterDrop::Road => "road",
        FilterDrop::Hike => "hike",
        FilterDrop::Slope => "slope",
        FilterDrop::Jeep => "jeep",
    }
}

fn print_sector_parks(
    elev: &SessionElev<'_>,
    routing: &crate::osm::OsmRouting,
    peak_lat: f64,
    peak_lon: f64,
    search_m: f64,
    max_slope_deg: f64,
    path_max_m: f64,
    sample_m: f64,
    difficulty: &peaky_preset::PeaksDifficultyConfig,
) {
    let cands = park_candidates(
        &routing.jeep_roads,
        Some(&routing.paved),
        peak_lat,
        peak_lon,
        search_m,
    );
    println!("sector_parks (search_m={search_m:.0}, count={}):", cands.len());
    for (i, (lat, lon, dist)) in cands.iter().enumerate() {
        let hike = resolve_hike(
            elev,
            *lat,
            *lon,
            peak_lat,
            peak_lon,
            max_slope_deg,
            path_max_m,
            sample_m,
            true,
        );
        match hike {
            Some(h) => println!(
                "  [{i}] dist={dist:.0}m paved_near={} max_slope={:.1}° gain={:.0}m 3d={:.0}m band={}",
                *dist <= ON_ROAD_SNAP_M + 1.0,
                h.max_slope_deg,
                h.gain_m,
                h.hike_m_3d,
                classify_hike(
                    h.max_grade_pct,
                    h.avg_grade_pct,
                    h.gain_m,
                    &difficulty.hike,
                ),
            ),
            None => println!("  [{i}] dist={dist:.0}m hike=FAIL"),
        }
    }
}

/// Run eligibility probe at ``lat,lon`` and print stage-by-stage results.
pub fn probe_peak(preset_path: &Path, lat: f64, lon: f64) -> Result<()> {
    let preset = load_preset(preset_path).context("load preset")?;
    let project_dir = preset_path.parent().context("preset parent")?;
    let peaks_cfg = preset.scan.peaks.clone();
    let rules = preset.peak_access_rules();
    let filter = PeaksFilterParams::from(&peaks_cfg);
    let difficulty = rules.difficulty.clone();

    let bbox = LonLatBBox {
        west: lon - 0.05,
        south: lat - 0.05,
        east: lon + 0.05,
        north: lat + 0.05,
    };

    let land_parts =
        load_or_build_eligible_land_parts(preset_path, Some(bbox)).context("eligible land")?;
    let land = land_parts.index();
    let land_ok = land.contains(lon, lat);
    println!("1. land_eligible: {land_ok} (digest={})", land_parts.digest);

    let mirror = peaky_preset::resolved_skadi_mirror_dir_for_project(project_dir);
    let session = Session::new(mirror, false, peaky_preset::resolved_dem_fetch_max_workers(&preset));
    session.ensure_tiles_for_bounds(bbox.west, bbox.south, bbox.east, bbox.north)?;

    let elev = SessionElev(&session);
    let snapped = snap_to_dem_local_max_filtered(
        &elev,
        lat,
        lon,
        filter.summit_snap_m,
        15.0,
        |la, lo| land.contains(lo, la),
    );
    match snapped {
        Some((slat, slon, selev)) => {
            let prom = neighbor_prominence_m(&elev, slat, slon, 30.0);
            println!(
                "2. dem_candidate: snap=({slat:.6},{slon:.6}) elev={selev:.0}m prominence={prom:.1}m (min {})",
                filter.min_prominence_m
            );
        }
        None => println!("2. dem_candidate: FAIL (no land-eligible local max within snap)"),
    }

    let pbf = ensure_osm_pbf(project_dir, false)?;
    let access_meta = ensure_access_meta(preset_path)?;
    let osm_opts = OsmRoutingOpts::from(&access_meta);
    let routing = build_osm_routing(
        &pbf,
        bbox.west,
        bbox.south,
        bbox.east,
        bbox.north,
        &osm_opts,
    )?;

    let jeep_near = routing
        .jeep_roads
        .nearest_within(lat, lon, rules.max_hike_m);
    let paved_near = routing
        .paved
        .nearest_within(lat, lon, rules.max_hike_m);
    println!(
        "3. nearest_road: jeep={:?} paved={:?} (pre-check radius={:.0}m)",
        jeep_near.map(|(_, _, d)| d),
        paved_near.map(|(_, _, d)| d),
        rules.max_hike_m,
    );

    let cand = RawCandidate {
        lat,
        lon,
        elev_m: Some(session.sample_elev_m(lat, lon)),
        name: None,
        source: "probe".into(),
        seed_slug: None,
    };
    let site_locs: Vec<(f64, f64)> = Vec::new();
    let scan_region = crate::corridor::ScanRegion::BboxOnly;

    match filter_candidate(
        &cand,
        &session,
        &land,
        &routing,
        &scan_region,
        &site_locs,
        &rules,
        access_meta.profile_sample_m,
        access_meta.hike_path_max_m,
        filter,
    ) {
        Ok(pass) => {
            let hike_band = classify_hike(
                pass.hike.max_grade_pct,
                pass.hike.avg_grade_pct,
                pass.hike.gain_m,
                &difficulty.hike,
            );
            let jeep_band = classify_jeep(&pass.jeep.segments, &difficulty.jeep);
            println!("4. filter_candidate: PASS");
            println!(
                "5. winner: road_m={:.0} hike_3d={:.0} max_slope={:.1}° gain={:.0}m avg_grade={:.1}% max_grade={:.1}%",
                pass.road_m,
                pass.hike_m_3d,
                pass.max_slope_deg,
                pass.hike.gain_m,
                pass.hike.avg_grade_pct,
                pass.hike.max_grade_pct,
            );
            println!(
                "6. stamped: hike={hike_band} (gate {:.0}%) jeep={jeep_band} (gate {:.0}m)",
                difficulty.hike.extreme.max_segment_grade_pct,
                difficulty.jeep.extreme.max_route_m,
            );
        }
        Err(drop) => {
            println!("4. filter_candidate: DROP ({})", drop_label(drop));
            if let Some((peak_lat, peak_lon, _)) = snapped {
                print_sector_parks(
                    &elev,
                    &routing,
                    peak_lat,
                    peak_lon,
                    access_meta.hike_path_max_m,
                    rules.max_slope_deg,
                    access_meta.hike_path_max_m,
                    access_meta.profile_sample_m,
                    &difficulty,
                );
                if let Some(picked) = select_park_and_hike(
                    &elev,
                    &routing.jeep_roads,
                    Some(&routing.paved),
                    peak_lat,
                    peak_lon,
                    access_meta.hike_path_max_m,
                    rules.max_slope_deg,
                    access_meta.hike_path_max_m,
                    access_meta.profile_sample_m,
                    true,
                ) {
                    let built = build_access_from_park(
                        &elev,
                        &routing,
                        &picked,
                        rules.max_jeep_m,
                        access_meta.profile_sample_m,
                    );
                    if let Some(built) = built {
                        let profile = HikeProfile {
                            hike_m_3d: picked.hike.hike_m_3d,
                            max_slope_deg: picked.hike.max_slope_deg,
                            n_samples: picked.hike.profile.len(),
                        };
                        let slope_ok = profile_passes(&profile, rules.max_slope_deg);
                        println!(
                            "7. best_park: road_m={:.0} jeep_m={:.0} hike_band={} jeep_band={} slope_gate_ok={slope_ok}",
                            picked.road_m,
                            built.jeep_m,
                            classify_hike(
                                picked.hike.max_grade_pct,
                                picked.hike.avg_grade_pct,
                                picked.hike.gain_m,
                                &difficulty.hike,
                            ),
                            classify_jeep(&built.jeep.segments, &difficulty.jeep),
                        );
                    }
                }
            }
        }
    }

    Ok(())
}
