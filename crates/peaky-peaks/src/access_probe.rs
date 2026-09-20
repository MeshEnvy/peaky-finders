//! Unified paved→jeep→trail→hike access probe.

use std::collections::HashSet;

use peaky_preset::{
    access_difficulty_from_stored, classify_hike, classify_jeep, AccessPreferenceConfig,
    AccessProbe, AccessRoute, BudgetLegReport, PeaksDifficultyConfig, RouteBudgetReport,
};
use peaky_preset::difficulty::difficulty_rank;

use crate::access::{classify_access_mode, BuiltAccess, LEG_MIN_M};
use crate::catalog_filter::{FilterDrop, FilterPass};
use crate::hike::hike_detail_from_stored;
use crate::hike::{
    haversine_m, profile_passes, stored_peak_hike, zero_hike_at, HikeProfile, HikeSampleElev,
};
use crate::hike_route::{profile_polyline_detailed, resolve_hike};
use crate::jeep::{
    jeep_class_usage, profile_along_polyline, reconstruct_jeep_route, stored_peak_jeep,
    JeepLengthBudget, JeepRoadSegment, PavedReachTree,
};
use crate::osm::OsmRouting;
use crate::trail::{route_trail_closest_approach, trail_park_candidates, TrailRouting, TRAIL_MAX_M};

pub struct AccessContext<'a> {
    pub routing: &'a OsmRouting,
    pub trails: Option<&'a TrailRouting>,
    pub tree: &'a PavedReachTree,
    pub tree_budgeted: &'a PavedReachTree,
    pub max_jeep_m: f64,
    pub hike_path_max_m: f64,
    pub profile_sample_m: f64,
    pub max_slope_deg: f64,
    pub jeep_budget: JeepLengthBudget,
    pub preference: AccessPreferenceConfig,
    pub difficulty: PeaksDifficultyConfig,
}

#[derive(Clone)]
struct ScoredCandidate {
    park_node: u32,
    score: f64,
    built: BuiltAccess,
    passes_budget: bool,
}

/// One scored park candidate (for ``--explain``).
#[derive(Debug, Clone)]
pub struct ProbeCandidateReport {
    pub park_node: u32,
    pub score: f64,
    pub passes_budget: bool,
    pub jeep_m: f64,
    pub trail_m: f64,
    pub hike_m: f64,
    pub budget_gated: bool,
}

fn merge_park_nodes(base: &[u32], extra: &[u32]) -> Vec<u32> {
    let mut seen: HashSet<u32> = base.iter().copied().collect();
    let mut out = base.to_vec();
    for &n in extra {
        if seen.insert(n) {
            out.push(n);
        }
    }
    out
}

fn fallback_closest_park(
    tree: &PavedReachTree,
    routing: &OsmRouting,
    dest_lat: f64,
    dest_lon: f64,
) -> Option<u32> {
    tree.reachable_nodes()
        .map(|node| {
            let (lat, lon) = routing.graph.node_coords(node);
            (node, haversine_m(lat, lon, dest_lat, dest_lon))
        })
        .min_by(|a, b| a.1.partial_cmp(&b.1).unwrap_or(std::cmp::Ordering::Equal))
        .map(|(node, _)| node)
}

fn collect_park_nodes<E: HikeSampleElev>(
    ctx: &AccessContext<'_>,
    tree: &PavedReachTree,
    dest_lat: f64,
    dest_lon: f64,
    elev: &E,
) -> Vec<u32> {
    let mut nodes = tree.park_candidates(ctx.routing, dest_lat, dest_lon, ctx.max_jeep_m);
    if let Some(trails) = ctx.trails.filter(|t| !t.is_empty()) {
        let trail_nodes = trail_park_candidates(
            trails,
            ctx.routing,
            tree,
            dest_lat,
            dest_lon,
            ctx.hike_path_max_m,
            ctx.max_slope_deg,
            elev,
        );
        nodes = merge_park_nodes(&nodes, &trail_nodes);
    }
    if nodes.is_empty() {
        if let Some(node) = fallback_closest_park(tree, ctx.routing, dest_lat, dest_lon) {
            nodes.push(node);
        }
    }
    nodes
}

fn build_at_park_node<E: HikeSampleElev>(
    elev: &E,
    routing: &OsmRouting,
    trails: Option<&TrailRouting>,
    prev: &std::collections::HashMap<u32, (u32, crate::osm::GraphEdge)>,
    park_node: u32,
    dest_lat: f64,
    dest_lon: f64,
    max_slope_deg: f64,
    hike_path_max_m: f64,
    profile_sample_m: f64,
    gate_hike: bool,
) -> Option<BuiltAccess> {
    let jeep_route = reconstruct_jeep_route(&routing.graph, prev, park_node);
    let (park_lat, park_lon) = jeep_route.coords.last().copied()?;
    let trail_route = trails
        .filter(|t| !t.is_empty())
        .and_then(|t| route_trail_closest_approach(t, park_lat, park_lon, dest_lat, dest_lon, TRAIL_MAX_M));
    let (trail_end_lat, trail_end_lon, trail_coords) = if let Some(ref tr) = trail_route {
        let (tlat, tlon) = tr.coords.last().copied().unwrap_or((park_lat, park_lon));
        (tlat, tlon, tr.coords.clone())
    } else {
        (park_lat, park_lon, Vec::new())
    };
    let crow = haversine_m(trail_end_lat, trail_end_lon, dest_lat, dest_lon);
    // Gated leg: a hike is at least its crow distance, so a far trailhead can
    // never pass the length gate. Skip the A* instead of routing then rejecting.
    if gate_hike && crow > hike_path_max_m + 1.0 {
        return None;
    }
    let hike_detail = if crow <= LEG_MIN_M {
        zero_hike_at(elev, dest_lat, dest_lon)
    } else if gate_hike {
        resolve_hike(
            elev,
            trail_end_lat,
            trail_end_lon,
            dest_lat,
            dest_lon,
            max_slope_deg,
            hike_path_max_m,
            profile_sample_m,
            true,
        )?
    } else {
        resolve_hike(
            elev,
            trail_end_lat,
            trail_end_lon,
            dest_lat,
            dest_lon,
            max_slope_deg,
            hike_path_max_m.max(crow * 2.0 + 500.0),
            profile_sample_m,
            false,
        )?
    };
    let jeep_detail = profile_along_polyline(
        elev,
        &jeep_route.coords,
        profile_sample_m,
        &jeep_route.road_segments,
    )?;
    let trail_detail = if trail_coords.len() >= 2 {
        profile_polyline_detailed(elev, &trail_coords, profile_sample_m)?
    } else {
        zero_hike_at(elev, trail_end_lat, trail_end_lon)
    };
    let park_paved = jeep_detail.horiz_m <= LEG_MIN_M;
    Some(BuiltAccess {
        mode: classify_access_mode(park_paved, hike_detail.horiz_m),
        road_lat: park_lat,
        road_lon: park_lon,
        road_m: haversine_m(park_lat, park_lon, dest_lat, dest_lon),
        paved_lat: jeep_route.paved_lat,
        paved_lon: jeep_route.paved_lon,
        jeep_m: jeep_detail.horiz_m,
        hike_m: hike_detail.hike_m_3d,
        max_slope_deg: hike_detail.max_slope_deg,
        hike: stored_peak_hike(&hike_detail),
        jeep: stored_peak_jeep(&jeep_detail),
        hike_detail,
        trail_lat: trail_end_lat,
        trail_lon: trail_end_lon,
        trail_m: trail_detail.horiz_m,
        trail: stored_peak_hike(&trail_detail),
        trail_detail,
    })
}

fn preference_score(
    built: &BuiltAccess,
    pref: &AccessPreferenceConfig,
    difficulty: &PeaksDifficultyConfig,
) -> f64 {
    let hike_label = classify_hike(
        built.hike_detail.max_grade_pct,
        built.hike_detail.avg_grade_pct,
        built.hike_detail.gain_m,
        &difficulty.hike,
    );
    let jeep_label = classify_jeep(&built.jeep.segments, &difficulty.jeep);
    let diff_rank = difficulty_rank(hike_label).max(difficulty_rank(jeep_label)) as f64;
    pref.w_hike * built.hike_detail.hike_m_3d
        + pref.w_trail * built.trail_m
        + pref.w_jeep * built.jeep_m
        + pref.w_difficulty * diff_rank
}

fn hike_passes_budget(built: &BuiltAccess, hike_path_max_m: f64, max_slope_deg: f64) -> bool {
    if built.hike_detail.horiz_m <= LEG_MIN_M {
        return true;
    }
    let profile = HikeProfile {
        hike_m_3d: built.hike_detail.hike_m_3d,
        max_slope_deg: built.hike_detail.max_slope_deg,
        n_samples: built.hike_detail.profile.len(),
    };
    built.hike_detail.hike_m_3d <= hike_path_max_m + 1.0
        && profile_passes(&profile, max_slope_deg)
}

fn jeep_segments_for_budget(built: &BuiltAccess) -> Vec<JeepRoadSegment> {
    built
        .jeep
        .segments
        .iter()
        .map(|s| JeepRoadSegment {
            highway: s.highway.clone(),
            tracktype: s.tracktype.clone(),
            dist_m: s.dist_m,
        })
        .collect()
}

pub fn compute_budget_report(
    built: &BuiltAccess,
    budget: &JeepLengthBudget,
    hike_path_max_m: f64,
    max_slope_deg: f64,
) -> RouteBudgetReport {
    let jeep_route = BudgetLegReport {
        used: built.jeep_m,
        cap: budget.overall_m,
        over_by: (built.jeep_m - budget.overall_m).max(0.0),
    };

    let segments = jeep_segments_for_budget(built);
    let mut jeep_class = None;
    let mut jeep_class_key = None;
    let mut worst_over = 0.0f64;
    for (key, used, cap) in jeep_class_usage(&segments, budget) {
        let over = (used - cap).max(0.0);
        if over > worst_over + 1e-9 {
            worst_over = over;
            jeep_class_key = Some(key.clone());
            jeep_class = Some(BudgetLegReport {
                used,
                cap,
                over_by: over,
            });
        }
    }

    let hike_path = BudgetLegReport {
        used: built.hike_detail.hike_m_3d,
        cap: hike_path_max_m,
        over_by: (built.hike_detail.hike_m_3d - hike_path_max_m).max(0.0),
    };
    let hike_grade = BudgetLegReport {
        used: built.hike_detail.max_slope_deg,
        cap: max_slope_deg,
        over_by: (built.hike_detail.max_slope_deg - max_slope_deg).max(0.0),
    };

    RouteBudgetReport {
        jeep_route: Some(jeep_route),
        jeep_class,
        jeep_class_key,
        hike_path: Some(hike_path),
        hike_grade: Some(hike_grade),
    }
}

fn budget_report_passes(report: &RouteBudgetReport) -> bool {
    let ok = |leg: &Option<BudgetLegReport>| leg.as_ref().map(|l| l.over_by <= 1e-9).unwrap_or(true);
    ok(&report.jeep_route)
        && ok(&report.jeep_class)
        && ok(&report.hike_path)
        && ok(&report.hike_grade)
}

/// Map an ineligible probe's recommended budget report to a catalog drop reason.
pub fn probe_drop_reason(probe: &AccessProbe) -> FilterDrop {
    let report = &probe.recommended.budget;
    if report
        .jeep_route
        .as_ref()
        .is_some_and(|l| l.over_by > 1e-9)
    {
        return FilterDrop::Jeep;
    }
    if report
        .jeep_class
        .as_ref()
        .is_some_and(|l| l.over_by > 1e-9)
    {
        return FilterDrop::Jeep;
    }
    if report
        .hike_path
        .as_ref()
        .is_some_and(|l| l.over_by > 1e-9)
    {
        return FilterDrop::Hike;
    }
    if report
        .hike_grade
        .as_ref()
        .is_some_and(|l| l.over_by > 1e-9)
    {
        return FilterDrop::Slope;
    }
    FilterDrop::Jeep
}

fn score_candidates<E: HikeSampleElev>(
    elev: &E,
    ctx: &AccessContext<'_>,
    park_nodes: &[u32],
    tree: &PavedReachTree,
    dest_lat: f64,
    dest_lon: f64,
    gate_hike: bool,
) -> Vec<ScoredCandidate> {
    let prev = tree.prev();
    park_nodes
        .iter()
        .filter_map(|&park_node| {
            let built = build_at_park_node(
                elev,
                ctx.routing,
                ctx.trails,
                prev,
                park_node,
                dest_lat,
                dest_lon,
                ctx.max_slope_deg,
                ctx.hike_path_max_m,
                ctx.profile_sample_m,
                gate_hike,
            )?;
            let passes_budget = !gate_hike
                || hike_passes_budget(&built, ctx.hike_path_max_m, ctx.max_slope_deg);
            let score = preference_score(&built, &ctx.preference, &ctx.difficulty);
            Some(ScoredCandidate {
                park_node,
                score,
                built,
                passes_budget,
            })
        })
        .collect()
}

fn pick_best(candidates: Vec<ScoredCandidate>) -> Option<ScoredCandidate> {
    candidates.into_iter().min_by(|a, b| {
        a.score
            .partial_cmp(&b.score)
            .unwrap_or(std::cmp::Ordering::Equal)
    })
}

fn scored_to_report(c: &ScoredCandidate, budget_gated: bool) -> ProbeCandidateReport {
    ProbeCandidateReport {
        park_node: c.park_node,
        score: c.score,
        passes_budget: c.passes_budget,
        jeep_m: c.built.jeep_m,
        trail_m: c.built.trail_m,
        hike_m: c.built.hike_m,
        budget_gated,
    }
}

pub fn built_to_access_route(
    built: &BuiltAccess,
    budget_report: RouteBudgetReport,
    difficulty: &PeaksDifficultyConfig,
) -> AccessRoute {
    let hike_difficulty = classify_hike(
        built.hike_detail.max_grade_pct,
        built.hike_detail.avg_grade_pct,
        built.hike_detail.gain_m,
        &difficulty.hike,
    )
    .to_string();
    let jeep_difficulty = classify_jeep(&built.jeep.segments, &difficulty.jeep).to_string();
    let access_difficulty =
        access_difficulty_from_stored(Some(&hike_difficulty), Some(&jeep_difficulty));
    AccessRoute {
        paved_loc: Some([built.paved_lat, built.paved_lon]),
        road_loc: Some([built.road_lat, built.road_lon]),
        jeep_m: Some((built.jeep_m * 10.0).round() / 10.0),
        jeep: Some(built.jeep.clone()),
        hike_m: Some((built.hike_m * 10.0).round() / 10.0),
        hike: Some(built.hike.clone()),
        trail_loc: if built.trail_m > LEG_MIN_M {
            Some([built.trail_lat, built.trail_lon])
        } else {
            None
        },
        trail_m: if built.trail_m > LEG_MIN_M {
            Some((built.trail_m * 10.0).round() / 10.0)
        } else {
            None
        },
        trail: if built.trail_m > LEG_MIN_M {
            Some(built.trail.clone())
        } else {
            None
        },
        max_slope_deg: Some((built.max_slope_deg * 10.0).round() / 10.0),
        hike_difficulty: Some(hike_difficulty),
        jeep_difficulty: Some(jeep_difficulty),
        access_difficulty,
        budget: budget_report,
    }
}

/// Score paved-reachable park points and return recommended + optional budget route.
pub fn probe<E: HikeSampleElev>(
    elev: &E,
    dest_lat: f64,
    dest_lon: f64,
    ctx: &AccessContext<'_>,
) -> Option<AccessProbe> {
    if ctx.routing.paved.all_sources().is_empty() {
        return None;
    }

    let rec_nodes = collect_park_nodes(ctx, ctx.tree, dest_lat, dest_lon, elev);
    let best_rec = pick_best(score_candidates(
        elev,
        ctx,
        &rec_nodes,
        ctx.tree,
        dest_lat,
        dest_lon,
        false,
    ))?;

    let bud_nodes = collect_park_nodes(ctx, ctx.tree_budgeted, dest_lat, dest_lon, elev);
    let best_bud = pick_best(
        score_candidates(
            elev,
            ctx,
            &bud_nodes,
            ctx.tree_budgeted,
            dest_lat,
            dest_lon,
            true,
        )
        .into_iter()
        .filter(|c| c.passes_budget)
        .collect(),
    );

    let rec_report = compute_budget_report(
        &best_rec.built,
        &ctx.jeep_budget,
        ctx.hike_path_max_m,
        ctx.max_slope_deg,
    );
    let eligible = best_bud.is_some() || budget_report_passes(&rec_report);
    let recommended = built_to_access_route(&best_rec.built, rec_report, &ctx.difficulty);

    let budget_route = best_bud
        .filter(|b| b.park_node != best_rec.park_node)
        .map(|b| {
            let report = compute_budget_report(
                &b.built,
                &ctx.jeep_budget,
                ctx.hike_path_max_m,
                ctx.max_slope_deg,
            );
            built_to_access_route(&b.built, report, &ctx.difficulty)
        });

    Some(AccessProbe {
        probe_key: None,
        eligible,
        recommended,
        budget_route,
    })
}

/// Like [`probe`] but also returns scored park candidates for debugging.
pub fn probe_explain<E: HikeSampleElev>(
    elev: &E,
    dest_lat: f64,
    dest_lon: f64,
    ctx: &AccessContext<'_>,
) -> Option<(AccessProbe, Vec<ProbeCandidateReport>)> {
    if ctx.routing.paved.all_sources().is_empty() {
        return None;
    }

    let rec_nodes = collect_park_nodes(ctx, ctx.tree, dest_lat, dest_lon, elev);
    let rec_scored = score_candidates(
        elev,
        ctx,
        &rec_nodes,
        ctx.tree,
        dest_lat,
        dest_lon,
        false,
    );
    let best_rec = pick_best(rec_scored.clone())?;

    let bud_nodes = collect_park_nodes(ctx, ctx.tree_budgeted, dest_lat, dest_lon, elev);
    let bud_scored: Vec<ScoredCandidate> = score_candidates(
        elev,
        ctx,
        &bud_nodes,
        ctx.tree_budgeted,
        dest_lat,
        dest_lon,
        true,
    )
    .into_iter()
    .filter(|c| c.passes_budget)
    .collect();
    let best_bud = pick_best(bud_scored.clone());

    let rec_report = compute_budget_report(
        &best_rec.built,
        &ctx.jeep_budget,
        ctx.hike_path_max_m,
        ctx.max_slope_deg,
    );
    let eligible = best_bud.is_some() || budget_report_passes(&rec_report);
    let recommended = built_to_access_route(&best_rec.built, rec_report, &ctx.difficulty);

    let budget_route = best_bud
        .filter(|b| b.park_node != best_rec.park_node)
        .map(|b| {
            let report = compute_budget_report(
                &b.built,
                &ctx.jeep_budget,
                ctx.hike_path_max_m,
                ctx.max_slope_deg,
            );
            built_to_access_route(&b.built, report, &ctx.difficulty)
        });

    let mut reports: Vec<ProbeCandidateReport> = rec_scored
        .iter()
        .map(|c| scored_to_report(c, false))
        .collect();
    for c in &bud_scored {
        if !reports.iter().any(|r| r.park_node == c.park_node) {
            reports.push(scored_to_report(c, true));
        }
    }
    reports.sort_by(|a, b| {
        a.score
            .partial_cmp(&b.score)
            .unwrap_or(std::cmp::Ordering::Equal)
    });

    Some((
        AccessProbe {
            probe_key: None,
            eligible,
            recommended,
            budget_route,
        },
        reports,
    ))
}

/// Build a catalog filter pass from a probe's recommended route.
pub fn filter_pass_from_probe(
    cand: &crate::universe::RawCandidate,
    peak_lat: f64,
    peak_lon: f64,
    peak_elev: f64,
    probe: &AccessProbe,
) -> Result<FilterPass, FilterDrop> {
    let route = &probe.recommended;
    let road_loc = route.road_loc.ok_or(FilterDrop::Road)?;
    let paved_loc = route.paved_loc.ok_or(FilterDrop::Jeep)?;
    let hike = route.hike.clone().ok_or(FilterDrop::Hike)?;
    let jeep = route.jeep.clone().ok_or(FilterDrop::Jeep)?;
    let trail_loc = route.trail_loc.unwrap_or(road_loc);
    let trail_m = route.trail_m.unwrap_or(0.0);
    let trail = route.trail.clone().unwrap_or_else(|| hike.clone());
    Ok(FilterPass {
        cand: cand.clone(),
        peak_lat,
        peak_lon,
        peak_elev,
        road_lat: road_loc[0],
        road_lon: road_loc[1],
        road_m: haversine_m(road_loc[0], road_loc[1], peak_lat, peak_lon),
        hike_m_3d: route.hike_m.unwrap_or(hike.hike_m_3d),
        max_slope_deg: route.max_slope_deg.unwrap_or(hike.max_slope_deg),
        hike: hike.clone(),
        hike_detail: hike_detail_from_stored(&hike),
        paved_lat: paved_loc[0],
        paved_lon: paved_loc[1],
        jeep_m: route.jeep_m.unwrap_or(jeep.horiz_m),
        jeep,
        trail_lat: trail_loc[0],
        trail_lon: trail_loc[1],
        trail_m,
        trail,
        probe: probe.clone(),
    })
}
