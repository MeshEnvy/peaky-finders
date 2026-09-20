//! Single-point peak eligibility probe for debugging catalog drops.

use std::path::Path;
use std::sync::Arc;

use anyhow::{Context, Result};
use peaky_geo::{load_or_build_eligible_land_parts, LonLatBBox};
use peaky_preset::{classify_hike, classify_jeep, ensure_access_meta, load_peaks_catalog_thin, load_preset};
use splatter::peaks::LandFilterIndex;
use splatter::Session;

use crate::access::{build_access_catalog, CatalogBuildDrop};
use crate::jeep::{jeep_class_usage, JeepLengthBudget, route_jeep_closest_approach};
use crate::hike::{
    haversine_m, neighbor_prominence_m, profile_passes, snap_to_dem_local_max_filtered, HikeProfile,
    HikeSampleElev,
};
use crate::hike_route::resolve_hike;
use crate::osm::{build_osm_routing, ensure_osm_pbf, OsmRoutingOpts};
use crate::park::{park_candidates, ON_ROAD_SNAP_M};
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
        FilterDrop::Land => "land",
        FilterDrop::Region => "region",
        FilterDrop::Road => "road",
        FilterDrop::Hike => "hike",
        FilterDrop::Slope => "slope",
        FilterDrop::Jeep => "jeep",
    }
}

fn drop_detail(d: FilterDrop, source: &str, prom: Option<f64>, min_prom: f64) -> String {
    match d {
        FilterDrop::Land if source == "dem" && prom.is_some_and(|p| p < min_prom) => {
            format!(
                "land (dem prominence {:.1}m < {:.0}m min)",
                prom.unwrap_or(0.0),
                min_prom
            )
        }
        other => drop_label(other).to_string(),
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

fn nearest_dem_seed(
    session: &Session,
    land: std::sync::Arc<LandFilterIndex>,
    lat: f64,
    lon: f64,
    bin_size_m: f64,
    min_prominence_m: f64,
) -> Result<Option<(f64, f64, f64, f64)>> {
    let pad = 0.025;
    let seeds = session.binned_peaks_in_bounds(
        lon - pad,
        lat - pad,
        lon + pad,
        lat + pad,
        land,
        None,
        bin_size_m,
        min_prominence_m,
        None,
    )?;
    let mut best: Option<(f64, f64, f64, f64)> = None;
    for p in seeds {
        let d = haversine_m(lat, lon, p.lat, p.lon);
        if best.as_ref().is_none_or(|(_, _, _, bd)| d < *bd) {
            best = Some((p.lat, p.lon, p.elev_m, d));
        }
    }
    Ok(best)
}

fn nearest_catalog_peak(
    preset_path: &Path,
    lat: f64,
    lon: f64,
) -> Result<Option<(String, f64, f64, f64, bool)>> {
    let catalog = load_peaks_catalog_thin(preset_path).context("load peaks catalog")?;
    let mut best: Option<(String, f64, f64, f64, bool)> = None;
    for (slug, entry) in &catalog.entries {
        let d = haversine_m(lat, lon, entry.lat(), entry.lon());
        if best.as_ref().is_none_or(|(_, _, _, bd, _)| d < *bd) {
            best = Some((
                slug.clone(),
                entry.lat(),
                entry.lon(),
                d,
                entry.deny.unwrap_or(false),
            ));
        }
    }
    Ok(best)
}

fn print_summary(
    in_catalog: bool,
    catalog_slug: Option<&str>,
    catalog_dist_m: Option<f64>,
    prom_ok: bool,
    prom: Option<f64>,
    min_prom: f64,
    dem_seed_near: Option<(f64, f64, f64, f64)>,
    dedup_m: f64,
    dem_filter: Result<(), FilterDrop>,
    probe_filter: Result<(), FilterDrop>,
    jeep_pre: Option<f64>,
    paved_pre: Option<f64>,
    max_hike_m: f64,
    _hike_path_max_m: f64,
) {
    println!("\n=== WHY NOT IN CATALOG ===");
    if in_catalog {
        println!(
            "IN CATALOG as {} ({:.0}m from pin)",
            catalog_slug.unwrap_or("?"),
            catalog_dist_m.unwrap_or(0.0)
        );
        return;
    }
    if !prom_ok {
        println!(
            "- DEM tile scan never seeds here: prominence {:.1}m < {:.0}m min at snapped summit",
            prom.unwrap_or(0.0),
            min_prom
        );
    } else {
        println!("- DEM prominence OK at snapped summit ({:.1}m)", prom.unwrap_or(0.0));
    }
    match dem_seed_near {
        Some((slat, slon, selev, dist)) if dist <= dedup_m => {
            if dist < 30.0 {
                println!("- Nearest DEM bin winner is this summit ({selev:.0}m)");
            } else {
                println!(
                    "- Nearest DEM bin winner is {:.0}m away at ({slat:.6},{slon:.6}) {selev:.0}m (within dedup; higher candidate may win)",
                    dist
                );
            }
        }
        Some((slat, slon, selev, dist)) => {
            println!(
                "- Nearest DEM bin winner {:.0}m away at ({slat:.6},{slon:.6}) {selev:.0}m (> {:.0}m dedup; separate candidate)",
                dist, dedup_m
            );
        }
        None => println!("- No DEM bin winners within ~2 km (tile scan empty)"),
    }
    match dem_filter {
        Ok(()) => println!("- Catalog filter (source=dem): PASS"),
        Err(d) => println!(
            "- Catalog filter (source=dem): DROP ({})",
            drop_detail(d, "dem", prom, min_prom)
        ),
    }
    match probe_filter {
        Ok(()) => println!("- Probe filter (source=probe, skips dem prominence): PASS"),
        Err(d) => println!(
            "- Probe filter (source=probe): DROP ({})",
            drop_label(d)
        ),
    }
    match (jeep_pre, paved_pre) {
        (None, None) => println!(
            "- Nearest OSM @ summit ({max_hike_m:.0}m): no jeep or paved (catalog uses paved-first approach, not this pre-check)"
        ),
        (j, p) => {
            let j_s = j.map(|d| format!("{d:.0}m")).unwrap_or_else(|| "none".into());
            let p_s = p.map(|d| format!("{d:.0}m")).unwrap_or_else(|| "none".into());
            println!("- Nearest OSM @ summit ({max_hike_m:.0}m): jeep={j_s} paved={p_s} (informational; catalog is paved-first)");
        }
    }
}

/// Stage-by-stage report: why a lat,lon is or is not in the peaks catalog.
pub fn probe_peak(preset_path: &Path, lat: f64, lon: f64) -> Result<()> {
    let preset = load_preset(preset_path).context("load preset")?;
    let project_dir = preset_path.parent().context("preset parent")?;
    let peaks_cfg = preset.scan.peaks.clone();
    let rules = preset.peak_access_rules();
    let filter = PeaksFilterParams::from(&peaks_cfg);
    let difficulty = rules.difficulty.clone();
    let bin_size_m = preset.scan.peak_bin_size_m;

    let bbox = LonLatBBox {
        west: lon - 0.05,
        south: lat - 0.05,
        east: lon + 0.05,
        north: lat + 0.05,
    };

    let land_parts =
        load_or_build_eligible_land_parts(preset_path, Some(bbox)).context("eligible land")?;
    let land = Arc::new(land_parts.index());
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
    let (prom, prom_ok) = match snapped {
        Some((slat, slon, selev)) => {
            let prom = neighbor_prominence_m(&elev, slat, slon, 30.0);
            let prom_ok = prom >= filter.min_prominence_m;
            println!(
                "2. dem_candidate: snap=({slat:.6},{slon:.6}) elev={selev:.0}m prominence={prom:.1}m (min {} {})",
                filter.min_prominence_m,
                if prom_ok { "OK" } else { "FAIL" }
            );
            (Some(prom), prom_ok)
        }
        None => {
            println!("2. dem_candidate: FAIL (no land-eligible local max within snap)");
            (None, false)
        }
    };

    println!(
        "3. dem_universe: scanning {:.0}m bins in ~2km bbox (may take a moment)...",
        bin_size_m
    );
    let dem_seed_near = nearest_dem_seed(
        &session,
        Arc::clone(&land),
        lat,
        lon,
        bin_size_m,
        filter.min_prominence_m,
    )?;
    match dem_seed_near {
        Some((slat, slon, selev, dist)) => println!(
            "   nearest_bin_winner: ({slat:.6},{slon:.6}) elev={selev:.0}m dist={dist:.0}m",
        ),
        None => println!("   nearest_bin_winner: none"),
    }

    let catalog_near = nearest_catalog_peak(preset_path, lat, lon);
    let (in_catalog, catalog_slug, catalog_dist_m) = match catalog_near {
        Ok(Some((slug, _, _, dist, denied))) if dist <= filter.candidate_dedup_m && !denied => {
            println!(
                "4. catalog: IN ({slug}) dist={dist:.0}m",
            );
            (true, Some(slug), Some(dist))
        }
        Ok(Some((slug, clat, clon, dist, denied))) => {
            println!(
                "4. catalog: not within {:.0}m; nearest={slug} ({clat:.6},{clon:.6}) dist={dist:.0}m denied={denied}",
                filter.candidate_dedup_m
            );
            (false, None, None)
        }
        Ok(None) => {
            println!("4. catalog: empty (run peaks build)");
            (false, None, None)
        }
        Err(e) => {
            println!("4. catalog: unreadable ({e:#})");
            (false, None, None)
        }
    };

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

    let (peak_lat, peak_lon) = snapped.map(|(a, b, _)| (a, b)).unwrap_or((lat, lon));
    let jeep_pre = routing
        .jeep_roads
        .nearest_within(peak_lat, peak_lon, rules.max_hike_m)
        .map(|(_, _, d)| d);
    let paved_pre = routing
        .paved
        .nearest_within(peak_lat, peak_lon, rules.max_hike_m)
        .map(|(_, _, d)| d);
    let budget = JeepLengthBudget::from_extreme(&rules.difficulty.jeep.extreme);
    let catalog_route = route_jeep_closest_approach(
        &routing.graph,
        &routing.paved,
        peak_lat,
        peak_lon,
        rules.difficulty.jeep.extreme.max_route_m,
        Some(&budget),
    );
    println!(
        "5. catalog paved-first jeep @ summit snap: jeep_near={jeep_pre:?} paved_near={paved_pre:?} (informational)"
    );
    match &catalog_route {
        Some(r) => {
            println!(
                "   approach: jeep_horiz={:.0}m overall_cap={:.0}m park=({:.6},{:.6})",
                r.horiz_m, budget.overall_m, r.coords.last().map(|c| c.0).unwrap_or(0.0),
                r.coords.last().map(|c| c.1).unwrap_or(0.0),
            );
            for (cls, used, cap) in jeep_class_usage(&r.road_segments, &budget) {
                let over = if used > cap + 1.0 { " OVER" } else { "" };
                println!("   class {cls}: {used:.0}m / {cap:.0}m{over}");
            }
        }
        None => println!(
            "   approach: FAIL (no paved anchor or exceeds jeep caps; overall={:.0}m)",
            budget.overall_m
        ),
    }

    let scan_region = crate::corridor::ScanRegion::BboxOnly;
    let dem_cand = RawCandidate {
        lat,
        lon,
        elev_m: Some(session.sample_elev_m(lat, lon)),
        name: None,
        source: "dem".into(),
        seed_slug: None,
    };
    let probe_cand = RawCandidate {
        lat,
        lon,
        elev_m: Some(session.sample_elev_m(lat, lon)),
        name: None,
        source: "probe".into(),
        seed_slug: None,
    };

    let dem_filter = filter_candidate(
        &dem_cand,
        &session,
        land.as_ref(),
        &routing,
        &scan_region,
        &rules,
        access_meta.profile_sample_m,
        access_meta.hike_path_max_m,
        filter,
    )
    .map(|_| ())
    .map_err(|e| e);

    let probe_filter = filter_candidate(
        &probe_cand,
        &session,
        land.as_ref(),
        &routing,
        &scan_region,
        &rules,
        access_meta.profile_sample_m,
        access_meta.hike_path_max_m,
        filter,
    )
    .map(|_| ())
    .map_err(|e| e);

    match dem_filter {
        Ok(()) => println!("6. filter_candidate (source=dem): PASS"),
        Err(drop) => println!(
            "6. filter_candidate (source=dem): DROP ({})",
            drop_detail(drop, "dem", prom, filter.min_prominence_m)
        ),
    }

    match probe_filter {
        Ok(()) => {
            let pass = filter_candidate(
                &probe_cand,
                &session,
                land.as_ref(),
                &routing,
                &scan_region,
                &rules,
                access_meta.profile_sample_m,
                access_meta.hike_path_max_m,
                filter,
            )
            .expect("probe filter already passed");
            let hike_band = classify_hike(
                pass.hike.max_grade_pct,
                pass.hike.avg_grade_pct,
                pass.hike.gain_m,
                &difficulty.hike,
            );
            let jeep_band = classify_jeep(&pass.jeep.segments, &difficulty.jeep);
            println!("7. filter_candidate (source=probe): PASS");
            println!(
                "   road_m={:.0} hike_3d={:.0} max_slope={:.1}° hike={hike_band} jeep={jeep_band}",
                pass.road_m, pass.hike_m_3d, pass.max_slope_deg,
            );
        }
        Err(drop) => {
            println!(
                "7. filter_candidate (source=probe): DROP ({})",
                drop_label(drop)
            );
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
                match build_access_catalog(
                    &elev,
                    &routing,
                    peak_lat,
                    peak_lon,
                    &rules.difficulty.jeep.extreme,
                    rules.max_slope_deg,
                    access_meta.hike_path_max_m,
                    access_meta.profile_sample_m,
                ) {
                    Ok(built) => {
                        let profile = HikeProfile {
                            hike_m_3d: built.hike_detail.hike_m_3d,
                            max_slope_deg: built.hike_detail.max_slope_deg,
                            n_samples: built.hike_detail.profile.len(),
                        };
                        let slope_ok = profile_passes(&profile, rules.max_slope_deg);
                        println!(
                            "   catalog_access: road_m={:.0} jeep_m={:.0} hike_band={} jeep_band={} slope_gate_ok={slope_ok}",
                            built.road_m,
                            built.jeep_m,
                            classify_hike(
                                built.hike_detail.max_grade_pct,
                                built.hike_detail.avg_grade_pct,
                                built.hike_detail.gain_m,
                                &difficulty.hike,
                            ),
                            classify_jeep(&built.jeep.segments, &difficulty.jeep),
                        );
                    }
                    Err(CatalogBuildDrop::Jeep) => println!("   catalog_access: jeep FAIL (no paved or caps)"),
                    Err(CatalogBuildDrop::Slope) => println!("   catalog_access: hike FAIL (slope/path gate)"),
                }
            }
        }
    }

    print_summary(
        in_catalog,
        catalog_slug.as_deref(),
        catalog_dist_m,
        prom_ok,
        prom,
        filter.min_prominence_m,
        dem_seed_near,
        filter.candidate_dedup_m,
        dem_filter,
        probe_filter,
        jeep_pre,
        paved_pre,
        rules.max_hike_m,
        access_meta.hike_path_max_m,
    );

    Ok(())
}
