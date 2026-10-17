//! Assemble jeep + hike legs from a park pick (four access modes).

use crate::hike::{
    default_max_slope_deg, haversine_m, stored_peak_hike, zero_hike_at, HikeProfileDetailed,
    HikeSampleElev,
};
use crate::hike_route::resolve_hike;
use std::collections::{HashMap, HashSet};

use crate::hike_route::profile_polyline_detailed;
use crate::jeep::{
    dijkstra_from_paved, jeep_class_line_from_path, profile_along_polyline, reconstruct_jeep_route,
    route_jeep_detailed, stored_peak_jeep, JeepLengthBudget, JeepProfileDetailed,
};
use crate::osm::{OsmRouting, PavedAnchorIndex};
use crate::park::ParkHike;
use crate::trail::{route_trail_closest_approach, TrailRouting, TRAIL_MAX_M, TRAIL_PARK_SNAP_M};
use peaky_preset::AccessMeta;

/// Minimum horizontal length before we treat a leg as real (m).
pub const LEG_MIN_M: f64 = 5.0;

pub use crate::park::ON_ROAD_SNAP_M;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AccessMode {
    Both,
    HikeOnly,
    JeepOnly,
    Neither,
}

impl AccessMode {
    pub fn needs_jeep(self) -> bool {
        matches!(self, Self::Both | Self::JeepOnly)
    }

    pub fn needs_hike(self) -> bool {
        matches!(self, Self::Both | Self::HikeOnly)
    }
}

pub fn classify_access_mode(park_paved: bool, hike_horiz_m: f64) -> AccessMode {
    let has_hike = hike_horiz_m > LEG_MIN_M;
    match (park_paved, has_hike) {
        (true, false) => AccessMode::Neither,
        (true, true) => AccessMode::HikeOnly,
        (false, false) => AccessMode::JeepOnly,
        (false, true) => AccessMode::Both,
    }
}

#[derive(Clone)]
pub struct BuiltAccess {
    pub mode: AccessMode,
    pub road_lat: f64,
    pub road_lon: f64,
    pub road_m: f64,
    pub paved_lat: f64,
    pub paved_lon: f64,
    pub jeep_m: f64,
    pub hike_m: f64,
    pub max_slope_deg: f64,
    /// Bushwhack only (leave-trail → dest).
    pub hike: peaky_preset::PeakHikeProfile,
    pub jeep: peaky_preset::PeakJeepProfile,
    pub hike_detail: HikeProfileDetailed,
    pub trail_lat: f64,
    pub trail_lon: f64,
    pub trail_m: f64,
    pub trail: peaky_preset::PeakHikeProfile,
    pub trail_detail: HikeProfileDetailed,
}

struct AccessCandidate {
    park_node: u32,
    bushwhack_horiz_m: f64,
    trail_horiz_m: f64,
    jeep_horiz_m: f64,
}

/// Crow search radius for the catalog's targeted Dijkstra (m).
const PARK_NODE_SEARCH_M: f64 = 2_500.0;
/// Paved anchors seeded within this crow radius before expanding (m).
const PAVED_SOURCE_CROW_M: f64 = 25_000.0;
const REACHABLE_PARK_RINGS_M: [f64; 6] = [2_500.0, 5_000.0, 10_000.0, 20_000.0, 40_000.0, 80_000.0];

fn paved_sources_for_catalog(
    paved: &PavedAnchorIndex,
    dest_lat: f64,
    dest_lon: f64,
    max_jeep_m: f64,
) -> Vec<(f64, f64, u32)> {
    for radius in [PAVED_SOURCE_CROW_M, 50_000.0, max_jeep_m + 5_000.0] {
        let sources = paved.sources_within(dest_lat, dest_lon, radius);
        if !sources.is_empty() {
            return sources;
        }
    }
    Vec::new()
}

fn paved_sources_for_display(
    paved: &PavedAnchorIndex,
    dest_lat: f64,
    dest_lon: f64,
    max_jeep_m: f64,
) -> Vec<(f64, f64, u32)> {
    paved.sources_within(dest_lat, dest_lon, max_jeep_m + 5_000.0)
}

fn trail_adjacent_park_nodes(
    routing: &OsmRouting,
    trails: &TrailRouting,
    dest_lat: f64,
    dest_lon: f64,
) -> HashSet<u32> {
    let search_m = PARK_NODE_SEARCH_M + TRAIL_PARK_SNAP_M;
    routing
        .graph_nodes
        .nodes_within(dest_lat, dest_lon, search_m)
        .into_iter()
        .filter(|&node| {
            let (lat, lon) = routing.graph.node_coords(node);
            trails
                .samples
                .nearest_within(lat, lon, TRAIL_PARK_SNAP_M)
                .is_some()
        })
        .collect()
}

fn route_to_park_node(
    graph: &crate::osm::JeepRoadGraph,
    sources: &[(f64, f64, u32)],
    max_jeep_m: f64,
    park_node: u32,
    budget: Option<&JeepLengthBudget>,
) -> Option<(
    std::collections::HashMap<u32, f64>,
    std::collections::HashMap<u32, (u32, crate::osm::GraphEdge)>,
)> {
    let (dist_cost, dist_horiz, prev) = dijkstra_from_paved(
        graph,
        sources,
        max_jeep_m,
        Some(park_node),
        budget,
        None,
        true,
    );
    if dist_cost.contains_key(&park_node) {
        Some((dist_horiz, prev))
    } else {
        None
    }
}

fn park_nodes_with_routes(
    routing: &OsmRouting,
    trails: Option<&TrailRouting>,
    sources: &[(f64, f64, u32)],
    dest_lat: f64,
    dest_lon: f64,
    max_jeep_m: f64,
    budget: Option<&JeepLengthBudget>,
) -> HashMap<
    u32,
    (
        std::collections::HashMap<u32, f64>,
        std::collections::HashMap<u32, (u32, crate::osm::GraphEdge)>,
    ),
> {
    let mut near_nodes: Vec<(u32, f64)> = routing
        .graph_nodes
        .nodes_within(dest_lat, dest_lon, PARK_NODE_SEARCH_M)
        .into_iter()
        .map(|n| {
            let (lat, lon) = routing.graph.node_coords(n);
            (n, haversine_m(lat, lon, dest_lat, dest_lon))
        })
        .collect();
    near_nodes.sort_by(|a, b| a.1.partial_cmp(&b.1).unwrap_or(std::cmp::Ordering::Equal));

    let mut routes = HashMap::new();
    let mut best_crow = f64::INFINITY;
    for &(node, crow) in &near_nodes {
        if crow > best_crow + 0.5 && best_crow.is_finite() {
            break;
        }
        if let Some(route) = route_to_park_node(&routing.graph, sources, max_jeep_m, node, budget) {
            if crow + 0.5 < best_crow {
                best_crow = crow;
                routes.clear();
            }
            if (crow - best_crow).abs() <= 0.5 {
                routes.insert(node, route);
            }
        }
    }

    if let Some(trails) = trails.filter(|t| !t.is_empty()) {
        for node in trail_adjacent_park_nodes(routing, trails, dest_lat, dest_lon) {
            if routes.contains_key(&node) {
                continue;
            }
            if let Some(route) =
                route_to_park_node(&routing.graph, sources, max_jeep_m, node, budget)
            {
                routes.insert(node, route);
            }
        }
    }

    routes
}

/// Closest paved-reachable graph nodes, by crow. Nearby orphan dirt is ignored.
fn closest_reachable_park_nodes(
    routing: &OsmRouting,
    dist_cost: &std::collections::HashMap<u32, f64>,
    dest_lat: f64,
    dest_lon: f64,
    max_search_m: f64,
) -> Vec<u32> {
    if dist_cost.is_empty() {
        return Vec::new();
    }
    for &ring_m in &REACHABLE_PARK_RINGS_M {
        let ring_m = ring_m.min(max_search_m);
        let mut found: Vec<(u32, f64)> = routing
            .graph_nodes
            .nodes_within(dest_lat, dest_lon, ring_m)
            .into_iter()
            .filter(|n| dist_cost.contains_key(n))
            .map(|n| {
                let (lat, lon) = routing.graph.node_coords(n);
                (n, haversine_m(lat, lon, dest_lat, dest_lon))
            })
            .collect();
        if found.is_empty() {
            if ring_m >= max_search_m {
                break;
            }
            continue;
        }
        found.sort_by(|a, b| a.1.partial_cmp(&b.1).unwrap_or(std::cmp::Ordering::Equal));
        let best = found[0].1;
        return found
            .into_iter()
            .filter(|(_, crow)| *crow <= best + 0.5)
            .map(|(n, _)| n)
            .collect();
    }
    Vec::new()
}

fn park_nodes_from_flood(
    routing: &OsmRouting,
    trails: Option<&TrailRouting>,
    sources: &[(f64, f64, u32)],
    dest_lat: f64,
    dest_lon: f64,
    max_jeep_m: f64,
    budget: Option<&JeepLengthBudget>,
) -> Option<(
    Vec<u32>,
    std::collections::HashMap<u32, f64>,
    std::collections::HashMap<u32, (u32, crate::osm::GraphEdge)>,
)> {
    let (dist_cost, dist_horiz, prev) = dijkstra_from_paved(
        &routing.graph,
        sources,
        max_jeep_m,
        None,
        budget,
        None,
        true,
    );
    if dist_cost.is_empty() {
        return None;
    }
    let mut nodes =
        closest_reachable_park_nodes(routing, &dist_cost, dest_lat, dest_lon, max_jeep_m);
    if let Some(trails) = trails.filter(|t| !t.is_empty()) {
        for node in trail_adjacent_park_nodes(routing, trails, dest_lat, dest_lon) {
            if dist_cost.contains_key(&node) && !nodes.contains(&node) {
                nodes.push(node);
            }
        }
    }
    if nodes.is_empty() {
        return None;
    }
    Some((nodes, dist_horiz, prev))
}

fn score_access_candidate<E: HikeSampleElev>(
    elev: &E,
    routing: &OsmRouting,
    trails: Option<&TrailRouting>,
    prev: &std::collections::HashMap<u32, (u32, crate::osm::GraphEdge)>,
    dist_horiz: &std::collections::HashMap<u32, f64>,
    park_node: u32,
    dest_lat: f64,
    dest_lon: f64,
    max_slope_deg: f64,
    hike_path_max_m: f64,
    profile_sample_m: f64,
    gate_hike: bool,
) -> Option<AccessCandidate> {
    let jeep_horiz = dist_horiz.get(&park_node).copied().unwrap_or(0.0);
    let (park_lat, park_lon) = routing.graph.node_coords(park_node);
    let (trail_end_lat, trail_end_lon, trail_horiz) =
        if let Some(trails) = trails.filter(|t| !t.is_empty()) {
            if let Some(route) = route_trail_closest_approach(
                trails,
                park_lat,
                park_lon,
                dest_lat,
                dest_lon,
                TRAIL_MAX_M,
            ) {
                let (tlat, tlon) = route.coords.last().copied().unwrap_or((park_lat, park_lon));
                (tlat, tlon, route.horiz_m)
            } else {
                (park_lat, park_lon, 0.0)
            }
        } else {
            (park_lat, park_lon, 0.0)
        };
    let crow = haversine_m(trail_end_lat, trail_end_lon, dest_lat, dest_lon);
    let bushwhack_horiz = if crow <= LEG_MIN_M {
        0.0
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
        )
        .map(|h| h.horiz_m)
        .unwrap_or(f64::INFINITY)
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
        )
        .map(|h| h.horiz_m)
        .unwrap_or(f64::INFINITY)
    };
    if !bushwhack_horiz.is_finite() {
        return None;
    }
    let _ = prev;
    Some(AccessCandidate {
        park_node,
        bushwhack_horiz_m: bushwhack_horiz,
        trail_horiz_m: trail_horiz,
        jeep_horiz_m: jeep_horiz,
    })
}

fn build_from_park_routes<E: HikeSampleElev>(
    elev: &E,
    routing: &OsmRouting,
    trails: Option<&TrailRouting>,
    routes: &HashMap<
        u32,
        (
            std::collections::HashMap<u32, f64>,
            std::collections::HashMap<u32, (u32, crate::osm::GraphEdge)>,
        ),
    >,
    dest_lat: f64,
    dest_lon: f64,
    max_slope_deg: f64,
    hike_path_max_m: f64,
    profile_sample_m: f64,
    gate_hike: bool,
    paved_highways: &[String],
) -> Option<BuiltAccess> {
    if routes.is_empty() {
        return None;
    }
    let candidates: Vec<AccessCandidate> = routes
        .iter()
        .filter_map(|(&node, (dist_horiz, prev))| {
            score_access_candidate(
                elev,
                routing,
                trails,
                prev,
                dist_horiz,
                node,
                dest_lat,
                dest_lon,
                max_slope_deg,
                hike_path_max_m,
                profile_sample_m,
                gate_hike,
            )
        })
        .collect();
    let best = pick_best_candidate(&candidates)?;
    let (_dist_horiz, prev) = routes.get(&best.park_node)?;
    build_from_park_node(
        elev,
        routing,
        trails,
        prev,
        best.park_node,
        dest_lat,
        dest_lon,
        max_slope_deg,
        hike_path_max_m,
        profile_sample_m,
        gate_hike,
        paved_highways,
    )
}

fn pick_best_candidate(candidates: &[AccessCandidate]) -> Option<&AccessCandidate> {
    candidates.iter().min_by(|a, b| {
        a.bushwhack_horiz_m
            .partial_cmp(&b.bushwhack_horiz_m)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| {
                (a.trail_horiz_m + a.bushwhack_horiz_m)
                    .partial_cmp(&(b.trail_horiz_m + b.bushwhack_horiz_m))
                    .unwrap_or(std::cmp::Ordering::Equal)
            })
            .then_with(|| {
                a.jeep_horiz_m
                    .partial_cmp(&b.jeep_horiz_m)
                    .unwrap_or(std::cmp::Ordering::Equal)
            })
    })
}

pub(crate) fn build_from_park_node<E: HikeSampleElev>(
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
    paved_highways: &[String],
) -> Option<BuiltAccess> {
    let jeep_route = reconstruct_jeep_route(&routing.graph, prev, park_node);
    let paved_tags: HashSet<&str> = paved_highways.iter().map(String::as_str).collect();
    let jeep_line = jeep_class_line_from_path(&routing.graph, &jeep_route.path_nodes, &paved_tags);
    let (park_lat, park_lon) = jeep_route.coords.last().copied()?;
    let trail_route = trails.filter(|t| !t.is_empty()).and_then(|t| {
        route_trail_closest_approach(t, park_lat, park_lon, dest_lat, dest_lon, TRAIL_MAX_M)
    });
    let (trail_end_lat, trail_end_lon, trail_coords) = if let Some(ref tr) = trail_route {
        let (tlat, tlon) = tr.coords.last().copied().unwrap_or((park_lat, park_lon));
        (tlat, tlon, tr.coords.clone())
    } else {
        (park_lat, park_lon, Vec::new())
    };
    let crow = haversine_m(trail_end_lat, trail_end_lon, dest_lat, dest_lon);
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
    let mut jeep_detail = profile_along_polyline(
        elev,
        &jeep_route.coords,
        profile_sample_m,
        &jeep_route.road_segments,
    )?;
    jeep_detail.jeep_line = jeep_line;
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

fn build_access_with_trails<E: HikeSampleElev>(
    elev: &E,
    routing: &OsmRouting,
    trails: Option<&TrailRouting>,
    dest_lat: f64,
    dest_lon: f64,
    max_jeep_m: f64,
    hike_path_max_m: f64,
    profile_sample_m: f64,
    max_slope_deg: f64,
    budget: Option<&JeepLengthBudget>,
    gate_hike: bool,
    paved_highways: Option<&[String]>,
) -> Option<BuiltAccess> {
    if routing.graph.is_empty() {
        return None;
    }
    let paved_owned;
    let paved_slice = match paved_highways {
        Some(s) => s,
        None => {
            paved_owned = AccessMeta::default().paved_highways;
            &paved_owned
        }
    };
    let display = budget.is_none();
    if !display {
        let sources = paved_sources_for_catalog(&routing.paved, dest_lat, dest_lon, max_jeep_m);
        if !sources.is_empty() {
            let routes = park_nodes_with_routes(
                routing, trails, &sources, dest_lat, dest_lon, max_jeep_m, budget,
            );
            if let Some(built) = build_from_park_routes(
                elev,
                routing,
                trails,
                &routes,
                dest_lat,
                dest_lon,
                max_slope_deg,
                hike_path_max_m,
                profile_sample_m,
                gate_hike,
                paved_slice,
            ) {
                return Some(built);
            }
        }
    }
    let sources = paved_sources_for_display(&routing.paved, dest_lat, dest_lon, max_jeep_m);
    if sources.is_empty() {
        return None;
    }
    let (nodes, dist_horiz, prev) = park_nodes_from_flood(
        routing, trails, &sources, dest_lat, dest_lon, max_jeep_m, budget,
    )?;
    let candidates: Vec<AccessCandidate> = nodes
        .iter()
        .filter_map(|&node| {
            score_access_candidate(
                elev,
                routing,
                trails,
                &prev,
                &dist_horiz,
                node,
                dest_lat,
                dest_lon,
                max_slope_deg,
                hike_path_max_m,
                profile_sample_m,
                gate_hike,
            )
        })
        .collect();
    let best = pick_best_candidate(&candidates)?;
    let _ = budget;
    build_from_park_node(
        elev,
        routing,
        trails,
        &prev,
        best.park_node,
        dest_lat,
        dest_lon,
        max_slope_deg,
        hike_path_max_m,
        profile_sample_m,
        gate_hike,
        paved_slice,
    )
}

fn zero_trail_leg<E: HikeSampleElev>(
    elev: &E,
    lat: f64,
    lon: f64,
) -> (
    f64,
    f64,
    f64,
    peaky_preset::PeakHikeProfile,
    HikeProfileDetailed,
) {
    let detail = zero_hike_at(elev, lat, lon);
    (lat, lon, 0.0, stored_peak_hike(&detail), detail)
}

fn zero_jeep_at<E: HikeSampleElev>(
    elev: &E,
    lat: f64,
    lon: f64,
    sample_m: f64,
) -> Option<JeepProfileDetailed> {
    profile_along_polyline(elev, &[(lat, lon)], sample_m, &[])
}

fn jeep_leg_from_park_strict<E: HikeSampleElev>(
    elev: &E,
    routing: &OsmRouting,
    picked: &ParkHike,
    max_jeep_m: f64,
    profile_sample_m: f64,
) -> Option<(f64, f64, f64, peaky_preset::PeakJeepProfile)> {
    let route = route_jeep_detailed(
        &routing.graph,
        &routing.paved,
        picked.road_lat,
        picked.road_lon,
        max_jeep_m,
        None,
    )?;
    let detail =
        profile_along_polyline(elev, &route.coords, profile_sample_m, &route.road_segments)?;
    Some((
        route.paved_lat,
        route.paved_lon,
        detail.horiz_m,
        stored_peak_jeep(&detail),
    ))
}

fn jeep_leg_from_park_display<E: HikeSampleElev>(
    elev: &E,
    routing: &OsmRouting,
    picked: &ParkHike,
    jeep_search_m: f64,
    profile_sample_m: f64,
) -> (f64, f64, f64, peaky_preset::PeakJeepProfile) {
    if let Some(leg) =
        jeep_leg_from_park_strict(elev, routing, picked, jeep_search_m, profile_sample_m)
    {
        return leg;
    }
    let detail = zero_jeep_at(elev, picked.road_lat, picked.road_lon, profile_sample_m)
        .expect("zero jeep at park");
    (
        picked.road_lat,
        picked.road_lon,
        0.0,
        stored_peak_jeep(&detail),
    )
}

pub fn build_access_from_park<E: HikeSampleElev>(
    elev: &E,
    routing: &OsmRouting,
    picked: &ParkHike,
    max_jeep_m: f64,
    profile_sample_m: f64,
) -> Option<BuiltAccess> {
    let mode = classify_access_mode(picked.park_paved, picked.hike.horiz_m);
    let (paved_lat, paved_lon, jeep_m, jeep) = if mode.needs_jeep() {
        jeep_leg_from_park_strict(elev, routing, picked, max_jeep_m, profile_sample_m)?
    } else {
        let detail = zero_jeep_at(elev, picked.road_lat, picked.road_lon, profile_sample_m)?;
        (
            picked.road_lat,
            picked.road_lon,
            0.0,
            stored_peak_jeep(&detail),
        )
    };

    let hike_detail = if mode.needs_hike() {
        picked.hike.clone()
    } else {
        zero_hike_at(elev, picked.road_lat, picked.road_lon)
    };
    let (trail_lat, trail_lon, trail_m, trail, trail_detail) =
        zero_trail_leg(elev, picked.road_lat, picked.road_lon);

    Some(BuiltAccess {
        mode,
        road_lat: picked.road_lat,
        road_lon: picked.road_lon,
        road_m: picked.road_m,
        paved_lat,
        paved_lon,
        jeep_m,
        hike_m: hike_detail.hike_m_3d,
        max_slope_deg: hike_detail.max_slope_deg,
        hike: stored_peak_hike(&hike_detail),
        jeep,
        hike_detail,
        trail_lat,
        trail_lon,
        trail_m,
        trail,
        trail_detail,
    })
}

/// Site/pin display: jeep from pavement, optional foot trail, then bushwhack.
pub fn build_access_from_paved_approach<E: HikeSampleElev>(
    elev: &E,
    routing: &OsmRouting,
    trails: Option<&TrailRouting>,
    dest_lat: f64,
    dest_lon: f64,
    max_jeep_m: f64,
    hike_path_max_m: f64,
    profile_sample_m: f64,
) -> Option<BuiltAccess> {
    build_access_with_trails(
        elev,
        routing,
        trails,
        dest_lat,
        dest_lon,
        max_jeep_m,
        hike_path_max_m,
        profile_sample_m,
        default_max_slope_deg(),
        None,
        false,
        None,
    )
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CatalogBuildDrop {
    Jeep,
    Slope,
}

/// Peak catalog: paved-first closest approach with jeep length budgets, then gated hike.
pub fn build_access_catalog<E: HikeSampleElev>(
    elev: &E,
    routing: &OsmRouting,
    trails: Option<&TrailRouting>,
    dest_lat: f64,
    dest_lon: f64,
    jeep_extreme: &peaky_preset::JeepExtremeBands,
    max_slope_deg: f64,
    hike_path_max_m: f64,
    profile_sample_m: f64,
) -> Result<BuiltAccess, CatalogBuildDrop> {
    let budget = JeepLengthBudget::from_extreme(jeep_extreme);
    let built = build_access_with_trails(
        elev,
        routing,
        trails,
        dest_lat,
        dest_lon,
        jeep_extreme.max_route_m,
        hike_path_max_m,
        profile_sample_m,
        max_slope_deg,
        Some(&budget),
        true,
        None,
    )
    .ok_or(CatalogBuildDrop::Jeep)?;
    let profile = crate::hike::HikeProfile {
        hike_m_3d: built.hike_detail.hike_m_3d,
        max_slope_deg: built.hike_detail.max_slope_deg,
        n_samples: built.hike_detail.profile.len(),
    };
    if built.mode.needs_hike() && !crate::hike::profile_passes(&profile, max_slope_deg) {
        return Err(CatalogBuildDrop::Slope);
    }
    Ok(built)
}

/// Site/draft fallback when the loaded graph has no paved anchors.
pub fn build_access_from_park_display<E: HikeSampleElev>(
    elev: &E,
    routing: &OsmRouting,
    picked: &ParkHike,
    jeep_search_m: f64,
    profile_sample_m: f64,
) -> BuiltAccess {
    let mode = classify_access_mode(picked.park_paved, picked.hike.horiz_m);
    let (paved_lat, paved_lon, jeep_m, jeep) = if mode.needs_jeep() {
        jeep_leg_from_park_display(elev, routing, picked, jeep_search_m, profile_sample_m)
    } else {
        let detail = zero_jeep_at(elev, picked.road_lat, picked.road_lon, profile_sample_m)
            .expect("zero jeep at park");
        (
            picked.road_lat,
            picked.road_lon,
            0.0,
            stored_peak_jeep(&detail),
        )
    };

    let hike_detail = if mode.needs_hike() {
        picked.hike.clone()
    } else {
        zero_hike_at(elev, picked.road_lat, picked.road_lon)
    };
    let (trail_lat, trail_lon, trail_m, trail, trail_detail) =
        zero_trail_leg(elev, picked.road_lat, picked.road_lon);

    BuiltAccess {
        mode,
        road_lat: picked.road_lat,
        road_lon: picked.road_lon,
        road_m: picked.road_m,
        paved_lat,
        paved_lon,
        jeep_m,
        hike_m: hike_detail.hike_m_3d,
        max_slope_deg: hike_detail.max_slope_deg,
        hike: stored_peak_hike(&hike_detail),
        jeep,
        hike_detail,
        trail_lat,
        trail_lon,
        trail_m,
        trail,
        trail_detail,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::hike::{default_max_slope_deg, HikeSampleElev, DEFAULT_MAX_HIKE_M};
    use crate::hike_route::resolve_hike;
    use crate::osm::{
        test_index_from_points, GraphEdge, JeepRoadGraph, OsmRouting, PavedAnchorIndex,
    };
    use crate::park::nearest_park_hike;
    use crate::trail::{build_trail_graph, route_trail_closest_approach, TRAIL_MAX_M};
    use peaky_preset::{DEFAULT_HIKE_PATH_MAX_M, DEFAULT_PLACE_ROAD_SEARCH_M};

    struct FlatElev(f64);

    impl HikeSampleElev for FlatElev {
        fn sample_elev_m(&self, _lat: f64, _lon: f64) -> f64 {
            self.0
        }
    }

    fn empty_routing(roads: crate::osm::JeepRoadIndex) -> OsmRouting {
        OsmRouting {
            jeep_roads: roads,
            graph: JeepRoadGraph::new(),
            paved: PavedAnchorIndex::test_from_points(&[]),
            graph_nodes: crate::osm::GraphNodeIndex::empty(),
            segment_groups: Vec::new(),
        }
    }

    #[test]
    fn classify_four_modes() {
        assert_eq!(classify_access_mode(true, 0.0), AccessMode::Neither);
        assert_eq!(classify_access_mode(true, 200.0), AccessMode::HikeOnly);
        assert_eq!(classify_access_mode(false, 0.0), AccessMode::JeepOnly);
        assert_eq!(classify_access_mode(false, 500.0), AccessMode::Both);
    }

    #[test]
    fn display_reaches_farther_road_than_catalog_search() {
        let roads = test_index_from_points(&[(38.0, -117.0)]);
        let dest_lat = 38.018;
        let dest_lon = -117.0;
        let elev = FlatElev(2000.0);
        let catalog_search = DEFAULT_HIKE_PATH_MAX_M;
        assert!(
            nearest_park_hike(
                &elev,
                &roads,
                None,
                dest_lat,
                dest_lon,
                catalog_search,
                default_max_slope_deg(),
                catalog_search,
                30.0,
                false,
            )
            .is_none(),
            "1 mi road search should miss ~2 km jeep road"
        );
        let picked = nearest_park_hike(
            &elev,
            &roads,
            None,
            dest_lat,
            dest_lon,
            DEFAULT_PLACE_ROAD_SEARCH_M,
            default_max_slope_deg(),
            DEFAULT_PLACE_ROAD_SEARCH_M,
            30.0,
            false,
        )
        .expect("20 km display search should reach ~2 km road");
        assert!(picked.road_m > catalog_search);
        assert!(picked.hike.horiz_m > 100.0);
    }

    #[test]
    fn display_without_paved_still_builds() {
        let road = (38.004, -117.0);
        let dest = (38.0045, -117.0);
        let roads = test_index_from_points(&[road]);
        let elev = FlatElev(2000.0);
        let picked = nearest_park_hike(
            &elev,
            &roads,
            None,
            dest.0,
            dest.1,
            DEFAULT_PLACE_ROAD_SEARCH_M,
            default_max_slope_deg(),
            DEFAULT_PLACE_ROAD_SEARCH_M,
            30.0,
            false,
        )
        .expect("park pick");
        let routing = empty_routing(roads);
        let built = build_access_from_park_display(
            &elev,
            &routing,
            &picked,
            DEFAULT_PLACE_ROAD_SEARCH_M,
            30.0,
        );
        assert!(built.hike_m > 0.0);
        assert_eq!(built.paved_lat, picked.road_lat);
    }

    #[test]
    fn display_steep_hike_stored() {
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
        let detail = resolve_hike(
            &ramp,
            rlat,
            rlon,
            peak_lat,
            peak_lon,
            default_max_slope_deg(),
            DEFAULT_PLACE_ROAD_SEARCH_M,
            30.0,
            false,
        )
        .expect("ungated display keeps steep hike");
        assert!(detail.max_slope_deg > default_max_slope_deg() + 0.5);
    }

    #[test]
    fn catalog_requires_paved_approach() {
        let mut routing = paved_to_track_plus_orphan_dirt();
        routing.paved = PavedAnchorIndex::from_nodes(&routing.graph, &[(0, 38.0, -117.0)]);
        let dest = (38.019, -117.0);
        let elev = FlatElev(2000.0);
        let extreme = peaky_preset::JeepExtremeBands::default();
        assert!(build_access_catalog(
            &elev,
            &routing,
            None,
            dest.0,
            dest.1,
            &extreme,
            default_max_slope_deg(),
            DEFAULT_HIKE_PATH_MAX_M,
            30.0,
        )
        .is_ok());
        let no_paved = empty_routing(test_index_from_points(&[(38.004, -117.0)]));
        assert!(build_access_catalog(
            &elev,
            &no_paved,
            None,
            dest.0,
            dest.1,
            &extreme,
            default_max_slope_deg(),
            DEFAULT_HIKE_PATH_MAX_M,
            30.0,
        )
        .is_err());
    }

    fn paved_to_track_plus_orphan_dirt() -> OsmRouting {
        let mut graph = JeepRoadGraph::new();
        let n0 = graph.insert_node(38.0, -117.0);
        let n1 = graph.insert_node(38.012, -117.0);
        let n2 = graph.insert_node(38.018, -117.0);
        let len = haversine_m(38.0, -117.0, 38.012, -117.0);
        graph.add_edge(
            n0,
            GraphEdge {
                to: n1,
                length_m: len,
                highway: "track".into(),
                tracktype: None,
                surface: None,
                fourwd_only: false,
                role: peaky_preset::RoadRole::Jeep,
                group_id: 0,
            },
        );
        graph.add_edge(
            n1,
            GraphEdge {
                to: n0,
                length_m: len,
                highway: "track".into(),
                tracktype: None,
                surface: None,
                fourwd_only: false,
                role: peaky_preset::RoadRole::Jeep,
                group_id: 0,
            },
        );
        let _ = n2;
        OsmRouting {
            jeep_roads: test_index_from_points(&[(38.012, -117.0), (38.018, -117.0)]),
            graph_nodes: crate::osm::GraphNodeIndex::from_graph(&graph),
            graph,
            paved: PavedAnchorIndex::from_nodes(&JeepRoadGraph::new(), &[(n0, 38.0, -117.0)]),
            segment_groups: Vec::new(),
        }
    }

    #[test]
    fn display_jeeps_from_paved_not_orphan_dirt() {
        let mut routing = paved_to_track_plus_orphan_dirt();
        routing.paved = PavedAnchorIndex::from_nodes(&routing.graph, &[(0, 38.0, -117.0)]);
        let dest = (38.019, -117.0);
        let elev = FlatElev(2000.0);
        let built = build_access_from_paved_approach(
            &elev,
            &routing,
            None,
            dest.0,
            dest.1,
            DEFAULT_PLACE_ROAD_SEARCH_M,
            DEFAULT_PLACE_ROAD_SEARCH_M,
            30.0,
        )
        .expect("paved approach");
        assert!(built.jeep_m > 1000.0);
        assert!((built.paved_lat - 38.0).abs() < 0.0002);
        assert!((built.road_lat - 38.012).abs() < 0.0002);
        assert!(built.hike_m > 500.0);
    }

    #[test]
    fn display_jeeps_past_nearby_orphan_when_connected_park_is_farther() {
        let mut graph = JeepRoadGraph::new();
        let n0 = graph.insert_node(38.0, -117.0);
        let n1 = graph.insert_node(38.055, -117.0);
        let n2 = graph.insert_node(38.078, -117.0);
        let n3 = graph.insert_node(38.079, -117.0);
        let connected = haversine_m(38.0, -117.0, 38.055, -117.0);
        let orphan = haversine_m(38.078, -117.0, 38.079, -117.0);
        for (a, b, len) in [
            (n0, n1, connected),
            (n1, n0, connected),
            (n2, n3, orphan),
            (n3, n2, orphan),
        ] {
            graph.add_edge(
                a,
                GraphEdge {
                    to: b,
                    length_m: len,
                    highway: "track".into(),
                    tracktype: None,
                    surface: None,
                    fourwd_only: false,
                    role: peaky_preset::RoadRole::Jeep,
                    group_id: 0,
                },
            );
        }
        let mut routing = OsmRouting {
            jeep_roads: test_index_from_points(&[
                (38.055, -117.0),
                (38.078, -117.0),
                (38.079, -117.0),
            ]),
            graph_nodes: crate::osm::GraphNodeIndex::from_graph(&graph),
            graph,
            paved: PavedAnchorIndex::from_nodes(&JeepRoadGraph::new(), &[(n0, 38.0, -117.0)]),
            segment_groups: Vec::new(),
        };
        routing.paved = PavedAnchorIndex::from_nodes(&routing.graph, &[(n0, 38.0, -117.0)]);
        let dest = (38.080, -117.0);
        assert!(
            haversine_m(dest.0, dest.1, 38.055, -117.0) > PARK_NODE_SEARCH_M,
            "connected park must sit outside the catalog 2.5 km shortcut"
        );
        let built = build_access_from_paved_approach(
            &FlatElev(2000.0),
            &routing,
            None,
            dest.0,
            dest.1,
            DEFAULT_PLACE_ROAD_SEARCH_M,
            DEFAULT_PLACE_ROAD_SEARCH_M,
            30.0,
        )
        .expect("paved approach reaches connected track");
        assert!(
            built.jeep_m > 4_000.0,
            "jeep from pavement, not zero at orphan dirt"
        );
        assert!((built.road_lat - 38.055).abs() < 0.0002);
        assert!((built.paved_lat - 38.0).abs() < 0.0002);
    }

    #[test]
    fn pick_best_candidate_minimizes_bushwhack() {
        let far_road = AccessCandidate {
            park_node: 0,
            bushwhack_horiz_m: 800.0,
            trail_horiz_m: 0.0,
            jeep_horiz_m: 200.0,
        };
        let trailhead = AccessCandidate {
            park_node: 1,
            bushwhack_horiz_m: 80.0,
            trail_horiz_m: 1200.0,
            jeep_horiz_m: 1200.0,
        };
        let candidates = [far_road, trailhead];
        let best = pick_best_candidate(&candidates).expect("best");
        assert_eq!(best.park_node, 1);
    }

    #[test]
    fn foot_trail_graph_not_jeep_routable() {
        let foot = build_trail_graph(&[vec![(38.0, -117.0), (38.01, -117.0)]], 30.0);
        assert!(!foot.is_empty());
        let jeep = empty_routing(test_index_from_points(&[(38.0, -117.0)]));
        assert!(
            route_trail_closest_approach(&foot, 38.0, -117.0, 38.01, -117.0, TRAIL_MAX_M,)
                .is_some()
        );
        assert!(build_access_from_paved_approach(
            &FlatElev(2000.0),
            &jeep,
            Some(&foot),
            38.01,
            -117.0,
            DEFAULT_PLACE_ROAD_SEARCH_M,
            DEFAULT_PLACE_ROAD_SEARCH_M,
            30.0,
        )
        .is_none());
    }

    #[test]
    fn dest_on_trail_has_zero_bushwhack() {
        let mut routing = paved_to_track_plus_orphan_dirt();
        routing.paved = PavedAnchorIndex::from_nodes(&routing.graph, &[(0, 38.0, -117.0)]);
        let foot = build_trail_graph(&[vec![(38.012, -117.0), (38.018, -117.0)]], 30.0);
        let elev = FlatElev(2000.0);
        let built = build_access_from_paved_approach(
            &elev,
            &routing,
            Some(&foot),
            38.018,
            -117.0,
            DEFAULT_PLACE_ROAD_SEARCH_M,
            DEFAULT_PLACE_ROAD_SEARCH_M,
            30.0,
        )
        .expect("trail approach to summit on trail");
        assert!(built.trail_m > 100.0);
        assert!(built.hike_detail.horiz_m <= LEG_MIN_M);
    }

    #[test]
    fn paved_approach_none_without_paved_anchors() {
        let roads = test_index_from_points(&[(38.004, -117.0)]);
        let routing = empty_routing(roads);
        let elev = FlatElev(2000.0);
        assert!(build_access_from_paved_approach(
            &elev,
            &routing,
            None,
            38.0045,
            -117.0,
            DEFAULT_PLACE_ROAD_SEARCH_M,
            DEFAULT_PLACE_ROAD_SEARCH_M,
            30.0,
        )
        .is_none());
    }
}
