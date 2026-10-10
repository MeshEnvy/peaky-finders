//! Unified paved→jeep→trail→hike access probe.

use std::collections::HashSet;

use peaky_preset::{
    access_difficulty_from_stored, classify_hike, classify_jeep, AccessPreferenceConfig,
    AccessProbe, AccessRoute, BudgetLegReport, PeaksDifficultyConfig, RouteBudgetReport,
    SiteAccessOption,
};
use peaky_preset::difficulty::difficulty_rank;

use crate::access::{BuiltAccess, LEG_MIN_M};
use crate::catalog_filter::{FilterDrop, FilterPass};
use crate::hike::hike_detail_from_stored;
use crate::hike::{
    haversine_m, profile_passes, stored_peak_hike, zero_hike_at, HikeProfile, HikeSampleElev,
};
use crate::jeep::{
    jeep_class_usage, jeep_path_undirected_edges, reconstruct_jeep_route,
    shortest_horiz_prev_from_paved, stored_peak_jeep, JeepLengthBudget, JeepRoadSegment,
    PavedReachTree,
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
    pub paved_highways: Vec<String>,
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
    ctx: &AccessContext<'_>,
    prev: &std::collections::HashMap<u32, (u32, crate::osm::GraphEdge)>,
    park_node: u32,
    dest_lat: f64,
    dest_lon: f64,
    gate_hike: bool,
) -> Option<BuiltAccess> {
    let (park_lat, park_lon) = ctx.routing.graph.node_coords(park_node);
    let trail_route = ctx
        .trails
        .filter(|t| !t.is_empty())
        .and_then(|t| route_trail_closest_approach(t, park_lat, park_lon, dest_lat, dest_lon, TRAIL_MAX_M));
    let (trail_end_lat, trail_end_lon) = if let Some(ref tr) = trail_route {
        tr.coords.last().copied().unwrap_or((park_lat, park_lon))
    } else {
        (park_lat, park_lon)
    };
    let crow = haversine_m(trail_end_lat, trail_end_lon, dest_lat, dest_lon);
    if gate_hike && crow > ctx.hike_path_max_m + 1.0 {
        return None;
    }
    crate::access::build_from_park_node(
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
        &ctx.paved_highways,
    )
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
            let built = build_at_park_node(elev, ctx, prev, park_node, dest_lat, dest_lon, gate_hike)?;
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
        site_options: None,
        site_options_rev: 0,
        ..Default::default()
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
            site_options: None,
            site_options_rev: 0,
            ..Default::default()
        },
        reports,
    ))
}

const MAX_SITE_PARKS: usize = 4;
const MIN_SITE_PARK_SEP_M: f64 = 400.0;
const MAX_ALT_PATH_LENGTH_RATIO: f64 = 1.75;
const MAX_ALT_PATH_EDGE_SHARE: f64 = 0.70;
/// Extra hike still treated as the same pull-off. Farther than this is an early stop.
const SAME_PULL_OFF_HIKE_M: f64 = 40.0;

pub const SITE_ACCESS_OPTIONS_REV: u8 = 4;

pub fn site_access_option_id(park_lat: f64, park_lon: f64, path_index: u8) -> String {
    format!("{:.4}#{:.4}#{path_index}", park_lat, park_lon)
}

fn park_points_too_close(
    lat: f64,
    lon: f64,
    kept: &[(f64, f64)],
    min_sep_m: f64,
) -> bool {
    kept.iter()
        .any(|(klat, klon)| haversine_m(lat, lon, *klat, *klon) < min_sep_m)
}

/// Collapse a dense road-node ring to a handful of spatially distinct parks before scoring.
fn thin_park_nodes(
    routing: &OsmRouting,
    nodes: &[u32],
    dest_lat: f64,
    dest_lon: f64,
    min_sep_m: f64,
    cap: usize,
) -> Vec<u32> {
    let mut ranked: Vec<(u32, f64)> = nodes
        .iter()
        .map(|&node| {
            let (lat, lon) = routing.graph.node_coords(node);
            (node, haversine_m(lat, lon, dest_lat, dest_lon))
        })
        .collect();
    ranked.sort_by(|a, b| a.1.partial_cmp(&b.1).unwrap_or(std::cmp::Ordering::Equal));
    let mut kept = Vec::new();
    let mut kept_pts: Vec<(f64, f64)> = Vec::new();
    for (node, _) in ranked {
        if kept.len() >= cap {
            break;
        }
        let (lat, lon) = routing.graph.node_coords(node);
        if park_points_too_close(lat, lon, &kept_pts, min_sep_m) {
            continue;
        }
        kept_pts.push((lat, lon));
        kept.push(node);
    }
    kept
}

/// Pick up to ``max_parks`` park nodes by preference score with minimum separation.
pub fn select_distinct_park_nodes(
    mut scored: Vec<ScoredCandidate>,
    min_sep_m: f64,
    max_parks: usize,
    extra: Option<ScoredCandidate>,
) -> Vec<ScoredCandidate> {
    scored.sort_by(|a, b| {
        a.score
            .partial_cmp(&b.score)
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    let mut kept: Vec<ScoredCandidate> = Vec::new();
    let mut kept_pts: Vec<(f64, f64)> = Vec::new();
    if let Some(winner) = pick_best(scored.clone()) {
        kept_pts.push((winner.built.road_lat, winner.built.road_lon));
        kept.push(winner);
    }
    for c in scored {
        if kept.len() >= max_parks {
            break;
        }
        if kept.iter().any(|k| k.park_node == c.park_node) {
            continue;
        }
        let (plat, plon) = (c.built.road_lat, c.built.road_lon);
        if park_points_too_close(plat, plon, &kept_pts, min_sep_m) {
            continue;
        }
        kept_pts.push((plat, plon));
        kept.push(c);
    }
    if let Some(bud) = extra {
        let (plat, plon) = (bud.built.road_lat, bud.built.road_lon);
        let already = kept
            .iter()
            .any(|k| k.park_node == bud.park_node);
        if !already {
            if kept.len() < max_parks {
                kept.push(bud);
            } else if let Some(worst_idx) = kept
                .iter()
                .enumerate()
                .skip(1)
                .max_by(|(_, a), (_, b)| {
                    a.score
                        .partial_cmp(&b.score)
                        .unwrap_or(std::cmp::Ordering::Equal)
                })
                .map(|(i, _)| i)
            {
                kept[worst_idx] = bud;
            }
        }
    }
    kept
}

fn option_hike_m(opt: &SiteAccessOption) -> f64 {
    opt.route.hike_m.unwrap_or(0.0)
}

/// Drop parks that stop short of a closer drive. Keep jeep-path alternates at the closest pull-off.
pub fn drop_early_jeep_stops(options: Vec<SiteAccessOption>) -> Vec<SiteAccessOption> {
    let Some(min_hike) = options.iter().map(option_hike_m).reduce(f64::min) else {
        return options;
    };
    let mut kept: Vec<SiteAccessOption> = options
        .into_iter()
        .filter(|opt| option_hike_m(opt) <= min_hike + SAME_PULL_OFF_HIKE_M)
        .collect();
    if !kept.iter().any(|opt| opt.recommended) {
        if let Some(opt) = kept.iter_mut().find(|opt| opt.id.ends_with("#0")) {
            opt.recommended = true;
        } else if let Some(opt) = kept.first_mut() {
            opt.recommended = true;
        }
    }
    kept
}

fn path_edge_share(first: &HashSet<(u32, u32)>, second: &HashSet<(u32, u32)>) -> f64 {
    if first.is_empty() {
        return 1.0;
    }
    let shared = first.intersection(second).count();
    shared as f64 / first.len() as f64
}

fn build_option_at_park<E: HikeSampleElev>(
    elev: &E,
    ctx: &AccessContext<'_>,
    park_node: u32,
    prev: &std::collections::HashMap<u32, (u32, crate::osm::GraphEdge)>,
    dest_lat: f64,
    dest_lon: f64,
    path_index: u8,
    recommended: bool,
) -> Option<SiteAccessOption> {
    let built = build_at_park_node(elev, ctx, prev, park_node, dest_lat, dest_lon, false)?;
    let report = compute_budget_report(
        &built,
        &ctx.jeep_budget,
        ctx.hike_path_max_m,
        ctx.max_slope_deg,
    );
    let route = built_to_access_route(&built, report.clone(), &ctx.difficulty);
    let passes_budget = budget_report_passes(&report);
    Some(SiteAccessOption {
        id: site_access_option_id(built.road_lat, built.road_lon, path_index),
        recommended,
        passes_budget,
        route,
    })
}

/// Site-only list: distinct parks and optional second jeep path per park.
pub fn build_site_access_options<E: HikeSampleElev>(
    elev: &E,
    dest_lat: f64,
    dest_lon: f64,
    ctx: &AccessContext<'_>,
    probe: &AccessProbe,
) -> Vec<SiteAccessOption> {
    let rec_road = probe.recommended.road_loc;
    let ring_nodes = ctx.tree.park_candidates_in_ring(
        ctx.routing,
        dest_lat,
        dest_lon,
        ctx.max_jeep_m,
    );
    tracing::info!(
        "site access options: ring nodes={} dest={dest_lat:.5},{dest_lon:.5}",
        ring_nodes.len()
    );
    let score_nodes = thin_park_nodes(ctx.routing, &ring_nodes, dest_lat, dest_lon, 250.0, 20);
    tracing::info!("site access options: scoring {} thinned parks", score_nodes.len());
    let ring_scored = score_candidates(
        elev,
        ctx,
        &score_nodes,
        ctx.tree,
        dest_lat,
        dest_lon,
        false,
    );
    let rec_park_node = ring_scored
        .iter()
        .find(|c| {
            rec_road.is_some_and(|[rlat, rlon]| {
                (c.built.road_lat - rlat).abs() < 1e-5 && (c.built.road_lon - rlon).abs() < 1e-5
            })
        })
        .map(|c| c.park_node)
        .or_else(|| pick_best(ring_scored.clone()).map(|c| c.park_node));

    let bud_nodes = collect_park_nodes(ctx, ctx.tree_budgeted, dest_lat, dest_lon, elev);
    let bud_best = pick_best(
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
    let budget_extra = bud_best.filter(|b| {
        rec_park_node.is_none_or(|rec| b.park_node != rec)
    });

    let parks = select_distinct_park_nodes(
        ring_scored,
        MIN_SITE_PARK_SEP_M,
        MAX_SITE_PARKS,
        budget_extra,
    );

    let paved_nodes: HashSet<u32> = ctx
        .routing
        .paved
        .all_sources()
        .iter()
        .map(|(_, _, node)| *node)
        .collect();
    let graph = &ctx.routing.graph;
    let mut options = Vec::new();
    for park in parks {
        let prev0 = ctx.tree.prev();
        let rec_flag = rec_park_node == Some(park.park_node);
        if let Some(opt) = build_option_at_park(
            elev,
            ctx,
            park.park_node,
            prev0,
            dest_lat,
            dest_lon,
            0,
            rec_flag,
        ) {
            options.push(opt);
        }

        let first_edges = jeep_path_undirected_edges(prev0, park.park_node);
        let first_horiz = ctx
            .tree
            .dist_horiz(park.park_node)
            .unwrap_or(f64::INFINITY);
        // Distance alternate to the same pull-off. Weighted search keeps the
        // easy road; this keeps a shorter track even when it shares the stem.
        let Some(prev_short) = shortest_horiz_prev_from_paved(
            graph,
            &paved_nodes,
            park.park_node,
            first_horiz,
        ) else {
            continue;
        };
        let route_short = reconstruct_jeep_route(graph, &prev_short, park.park_node);
        let short_edges = jeep_path_undirected_edges(&prev_short, park.park_node);
        let share = path_edge_share(&first_edges, &short_edges);
        let meaningfully_shorter = route_short.horiz_m + 200.0 < first_horiz;
        let distinct = share < MAX_ALT_PATH_EDGE_SHARE
            && route_short.horiz_m <= first_horiz * MAX_ALT_PATH_LENGTH_RATIO + 1.0;
        tracing::info!(
            "site access alt park={} weighted_m={first_horiz:.0} shortest_m={:.0} share={share:.2} keep={}",
            park.park_node,
            route_short.horiz_m,
            meaningfully_shorter || distinct
        );
        if meaningfully_shorter || distinct {
            if let Some(opt) = build_option_at_park(
                elev,
                ctx,
                park.park_node,
                &prev_short,
                dest_lat,
                dest_lon,
                1,
                false,
            ) {
                options.push(opt);
            }
        }
    }
    let mut options = drop_early_jeep_stops(options);
    if options.is_empty() {
        if let Some(road) = probe.recommended.road_loc {
            let report = probe.recommended.budget.clone();
            options.push(SiteAccessOption {
                id: site_access_option_id(road[0], road[1], 0),
                recommended: true,
                passes_budget: budget_report_passes(&report),
                route: probe.recommended.clone(),
            });
        }
    }
    options
}

const DRAFT_DUP_PULL_OFF_M: f64 = 50.0;
const DRAFT_DUP_JEEP_M: f64 = 200.0;

fn draft_coord5(v: f64) -> String {
    format!("{:.5}", v)
}

pub fn access_route_uses_custom(route: &AccessRoute) -> bool {
    route
        .jeep
        .as_ref()
        .is_some_and(|j| j.segments.iter().any(|s| s.highway == "custom"))
}

fn draft_duplicate_of_existing(route: &AccessRoute, options: &[SiteAccessOption]) -> bool {
    let Some([rlat, rlon]) = route.road_loc else {
        return true;
    };
    let jeep_m = route.jeep_m.unwrap_or(0.0);
    options.iter().any(|opt| {
        opt.route.road_loc.is_some_and(|[olat, olon]| {
            haversine_m(rlat, rlon, olat, olon) < DRAFT_DUP_PULL_OFF_M
                && (opt.route.jeep_m.unwrap_or(0.0) - jeep_m).abs() < DRAFT_DUP_JEEP_M
        })
    })
}

/// Append one draft custom-road option when the draft graph finds a shorter jeep path via ``highway=custom``.
pub fn append_draft_site_access_options<E: HikeSampleElev>(
    elev: &E,
    dest_lat: f64,
    dest_lon: f64,
    draft_ctx: &AccessContext<'_>,
    trusted_probe: &AccessProbe,
    options: &mut Vec<SiteAccessOption>,
) {
    let Some(draft_probe) = probe(elev, dest_lat, dest_lon, draft_ctx) else {
        return;
    };
    let route = draft_probe.recommended;
    if !access_route_uses_custom(&route) {
        return;
    }
    if draft_duplicate_of_existing(&route, options) {
        return;
    }
    let Some([rlat, rlon]) = route.road_loc else {
        return;
    };
    if trusted_probe.recommended.road_loc.is_some_and(|[tlat, tlon]| {
        haversine_m(rlat, rlon, tlat, tlon) < DRAFT_DUP_PULL_OFF_M
            && !access_route_uses_custom(&trusted_probe.recommended)
    }) {
        // Trusted already parks at the same pull-off without needing the custom edge.
        return;
    }
    let report = route.budget.clone();
    options.push(SiteAccessOption {
        id: format!(
            "draft-{}-{}-0",
            draft_coord5(rlat),
            draft_coord5(rlon)
        ),
        recommended: false,
        passes_budget: budget_report_passes(&report),
        route,
    });
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::jeep::JeepProfileDetailed;
    use peaky_preset::{resolve_site_display_route, AccessRoute};

    fn stub_scored(node: u32, lat: f64, lon: f64, score: f64) -> ScoredCandidate {
        ScoredCandidate {
            park_node: node,
            score,
            passes_budget: true,
            built: BuiltAccess {
                mode: crate::access::AccessMode::Both,
                road_lat: lat,
                road_lon: lon,
                road_m: 100.0,
                paved_lat: lat - 0.01,
                paved_lon: lon,
                jeep_m: 1000.0,
                hike_m: 200.0,
                max_slope_deg: 10.0,
                hike: stored_peak_hike(&zero_hike_at(&FlatElev(2000.0), lat, lon)),
                jeep: stored_peak_jeep(&JeepProfileDetailed {
                    jeep_m_3d: 1000.0,
                    horiz_m: 1000.0,
                    gain_m: 0.0,
                    loss_m: 0.0,
                    max_slope_deg: 5.0,
                    max_grade_pct: 5.0,
                    avg_grade_pct: 2.0,
                    difficulty: "easy".into(),
                    profile: vec![],
                    histogram: vec![],
                    road_segments: vec![],
                    jeep_line: vec![],
                }),
                hike_detail: zero_hike_at(&FlatElev(2000.0), lat, lon),
                trail_lat: lat,
                trail_lon: lon,
                trail_m: 0.0,
                trail: stored_peak_hike(&zero_hike_at(&FlatElev(2000.0), lat, lon)),
                trail_detail: zero_hike_at(&FlatElev(2000.0), lat, lon),
            },
        }
    }

    struct FlatElev(f64);
    impl HikeSampleElev for FlatElev {
        fn sample_elev_m(&self, _lat: f64, _lon: f64) -> f64 {
            self.0
        }
    }

    #[test]
    fn select_distinct_parks_keeps_two_one_km_apart() {
        let a = stub_scored(1, 39.0, -117.0, 10.0);
        let b = stub_scored(2, 39.009, -117.0, 12.0);
        let kept = select_distinct_park_nodes(vec![b, a.clone()], 400.0, 4, None);
        assert_eq!(kept.len(), 2);
        assert_eq!(kept[0].park_node, a.park_node);
    }

    #[test]
    fn drops_park_that_hikes_farther_than_the_drive() {
        let kept = drop_early_jeep_stops(vec![
            site_opt("41.0100#-117.7640#0", 18.0, true),
            site_opt("41.0100#-117.7640#1", 18.0, false),
            site_opt("41.0107#-117.7693#0", 430.0, false),
            site_opt("41.0107#-117.7693#1", 430.0, false),
        ]);
        assert_eq!(kept.len(), 2);
        assert!(kept.iter().all(|opt| opt.id.starts_with("41.0100#")));
    }

    fn site_opt(id: &str, hike_m: f64, recommended: bool) -> SiteAccessOption {
        SiteAccessOption {
            id: id.into(),
            recommended,
            passes_budget: true,
            route: AccessRoute {
                hike_m: Some(hike_m),
                ..AccessRoute::default()
            },
        }
    }

    #[test]
    fn access_route_uses_custom_segment() {
        let mut route = AccessRoute::default();
        assert!(!access_route_uses_custom(&route));
        route.jeep = Some(peaky_preset::PeakJeepProfile {
            jeep_m_3d: 0.0,
            horiz_m: 0.0,
            gain_m: 0.0,
            loss_m: 0.0,
            max_slope_deg: 0.0,
            max_grade_pct: 0.0,
            avg_grade_pct: 0.0,
            difficulty: "easy".into(),
            profile: vec![],
            histogram: vec![],
            segments: vec![peaky_preset::PeakJeepRoadSegment {
                highway: "custom".into(),
                tracktype: None,
                dist_m: 100.0,
            }],
            jeep_line: vec![],
        });
        assert!(access_route_uses_custom(&route));
    }

    #[test]
    fn resolve_site_display_route_stale_falls_back() {
        let opt_route = AccessRoute {
            jeep_m: Some(999.0),
            ..AccessRoute::default()
        };
        let probe = AccessProbe {
            probe_key: None,
            eligible: true,
            recommended: AccessRoute {
                jeep_m: Some(100.0),
                ..AccessRoute::default()
            },
            budget_route: None,
            site_options: Some(vec![SiteAccessOption {
                id: "39.0000#-117.0000#0".into(),
                recommended: true,
                passes_budget: true,
                route: opt_route.clone(),
            }]),
            site_options_rev: 0,
            ..Default::default()
        };
        let picked = resolve_site_display_route(&probe, Some("missing-id"));
        assert_eq!(picked.jeep_m, Some(100.0));
        let picked2 = resolve_site_display_route(&probe, Some("39.0000#-117.0000#0"));
        assert_eq!(picked2.jeep_m, Some(999.0));
    }
}
