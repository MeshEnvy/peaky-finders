//! Weighted jeep routing on OSM graph (paved anchor → park point).

use std::cmp::Ordering;
use std::collections::{BinaryHeap, HashMap};

use serde::Serialize;

use peaky_preset::{jeep_budget_class_key, JeepExtremeBands, PeakJeepProfile};

use crate::hike::{
    grade_histogram, haversine_m, slope_deg_to_grade_pct, HikeProfilePoint, HikeSampleElev,
    HikeSegment, GradeHistogramBucket,
};
use crate::osm::{JeepRoadGraph, OsmRouting, PavedAnchorIndex};

const SNAP_PARK_M: f64 = 250.0;

/// Catalog-only jeep length caps (overall + per OSM class).
#[derive(Debug, Clone)]
pub struct JeepLengthBudget {
    pub overall_m: f64,
    class_m: HashMap<String, f64>,
}

impl JeepLengthBudget {
    pub fn from_extreme(ext: &JeepExtremeBands) -> Self {
        Self {
            overall_m: ext.max_route_m,
            class_m: ext.max_class_m.clone(),
        }
    }

    pub fn class_cap(&self, highway: &str, tracktype: Option<&str>) -> f64 {
        let key = jeep_budget_class_key(highway, tracktype);
        self.class_m
            .get(&key)
            .or_else(|| self.class_m.get("grade5"))
            .copied()
            .unwrap_or(self.overall_m)
    }
}

/// Per-class horizontal usage on a jeep route (for probe diagnostics).
pub fn jeep_class_usage(
    segments: &[JeepRoadSegment],
    budget: &JeepLengthBudget,
) -> Vec<(String, f64, f64)> {
    let mut totals = HashMap::<String, f64>::new();
    for s in segments {
        let key = jeep_budget_class_key(&s.highway, s.tracktype.as_deref());
        *totals.entry(key).or_insert(0.0) += s.dist_m;
    }
    let mut keys: Vec<_> = totals.keys().cloned().collect();
    keys.sort();
    keys.into_iter()
        .map(|k| {
            let used = totals[&k];
            let cap = budget
                .class_m
                .get(&k)
                .or_else(|| budget.class_m.get("grade5"))
                .copied()
                .unwrap_or(budget.overall_m);
            (k, used, cap)
        })
        .collect()
}

fn jeep_difficulty_from_osm(segments: &[JeepRoadSegment]) -> &'static str {
    let mapped: Vec<peaky_preset::PeakJeepRoadSegment> = segments
        .iter()
        .map(|s| peaky_preset::PeakJeepRoadSegment {
            highway: s.highway.clone(),
            tracktype: s.tracktype.clone(),
            dist_m: s.dist_m,
        })
        .collect();
    peaky_preset::jeep_difficulty(&mapped)
}

#[derive(Debug, Clone, Serialize)]
pub struct JeepRoadSegment {
    pub highway: String,
    pub tracktype: Option<String>,
    pub dist_m: f64,
}

#[derive(Debug, Clone, Serialize)]
pub struct JeepProfileDetailed {
    pub jeep_m_3d: f64,
    pub horiz_m: f64,
    pub gain_m: f64,
    pub loss_m: f64,
    pub max_slope_deg: f64,
    pub max_grade_pct: f64,
    pub avg_grade_pct: f64,
    pub difficulty: String,
    pub profile: Vec<HikeProfilePoint>,
    pub histogram: Vec<GradeHistogramBucket>,
    pub road_segments: Vec<JeepRoadSegment>,
}

#[derive(Debug, Clone)]
pub struct JeepRouteResult {
    pub paved_lat: f64,
    pub paved_lon: f64,
    pub coords: Vec<(f64, f64)>,
    pub horiz_m: f64,
    pub road_segments: Vec<JeepRoadSegment>,
}

fn round1(v: f64) -> f64 {
    (v * 10.0).round() / 10.0
}

fn round_coord(v: f64) -> f64 {
    (v * 1e7).round() / 1e7
}

/// Cost multiplier: longer easy roads beat shorter brutal tracks.
pub fn edge_cost_multiplier(
    highway: &str,
    tracktype: Option<&str>,
    surface: Option<&str>,
    fourwd_only: bool,
) -> f64 {
    let mut mult = match highway {
        "motorway" | "trunk" | "primary" => 0.8,
        "secondary" | "tertiary" => 0.9,
        "unclassified" | "residential" => 1.0,
        "service" => 1.2,
        "track" => {
            if fourwd_only {
                8.0
            } else {
                match tracktype {
                    Some("grade5") => 10.0,
                    Some("grade4") => 8.0,
                    Some("grade3") => 4.0,
                    Some("grade2") => 2.5,
                    Some("grade1") => 1.5,
                    _ => 3.0,
                }
            }
        }
        _ => 2.0,
    };
    if matches!(surface, Some("paved") | Some("asphalt") | Some("concrete")) {
        mult *= 0.85;
    }
    mult
}

#[derive(Copy, Clone, PartialEq)]
struct HeapState {
    cost: f64,
    node: u32,
}

impl Eq for HeapState {}

impl PartialOrd for HeapState {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

impl Ord for HeapState {
    fn cmp(&self, other: &Self) -> Ordering {
        other
            .cost
            .partial_cmp(&self.cost)
            .unwrap_or(Ordering::Equal)
    }
}

pub fn reconstruct_jeep_route(
    graph: &JeepRoadGraph,
    prev: &HashMap<u32, (u32, crate::osm::GraphEdge)>,
    dest_node: u32,
) -> JeepRouteResult {
    let mut path_nodes = vec![dest_node];
    let mut road_segments = Vec::new();
    let mut node = dest_node;
    let mut paved_node = node;
    while let Some((prev_node, edge)) = prev.get(&node) {
        road_segments.push(JeepRoadSegment {
            highway: edge.highway.clone(),
            tracktype: edge.tracktype.clone(),
            dist_m: edge.length_m,
        });
        node = *prev_node;
        paved_node = node;
        path_nodes.push(node);
    }
    path_nodes.reverse();
    road_segments.reverse();

    let (paved_lat, paved_lon) = graph.node_coords(paved_node);
    let coords: Vec<(f64, f64)> = path_nodes
        .iter()
        .map(|&idx| graph.node_coords(idx))
        .collect();
    let horiz_m: f64 = road_segments.iter().map(|s| s.dist_m).sum();

    JeepRouteResult {
        paved_lat,
        paved_lon,
        coords,
        horiz_m,
        road_segments,
    }
}

pub fn dijkstra_from_paved(
    graph: &JeepRoadGraph,
    sources: &[(f64, f64, u32)],
    max_jeep_m: f64,
    stop_at: Option<u32>,
    budget: Option<&JeepLengthBudget>,
) -> (
    HashMap<u32, f64>,
    HashMap<u32, f64>,
    HashMap<u32, (u32, crate::osm::GraphEdge)>,
) {
    let overall_cap = budget.map(|b| b.overall_m).unwrap_or(max_jeep_m);
    let budgeted = budget.is_some();
    let mut dist_cost = HashMap::<u32, f64>::new();
    let mut dist_horiz = HashMap::<u32, f64>::new();
    let mut class_at: HashMap<u32, HashMap<String, f64>> = HashMap::new();
    let mut prev: HashMap<u32, (u32, crate::osm::GraphEdge)> = HashMap::new();
    let mut heap = BinaryHeap::new();

    for &(_lat, _lon, node) in sources {
        dist_cost.insert(node, 0.0);
        dist_horiz.insert(node, 0.0);
        if budgeted {
            class_at.insert(node, HashMap::new());
        }
        heap.push(HeapState { cost: 0.0, node });
    }

    while let Some(HeapState { cost, node }) = heap.pop() {
        if cost > *dist_cost.get(&node).unwrap_or(&f64::INFINITY) + 1e-9 {
            continue;
        }
        if stop_at == Some(node) {
            break;
        }
        let horiz_base = dist_horiz.get(&node).copied().unwrap_or(0.0);
        for edge in graph.edges(node) {
            let horiz = horiz_base + edge.length_m;
            if horiz > overall_cap + 1.0 {
                continue;
            }
            let mult = edge_cost_multiplier(
                &edge.highway,
                edge.tracktype.as_deref(),
                edge.surface.as_deref(),
                edge.fourwd_only,
            );
            let next_cost = cost + edge.length_m * mult;
            let mut new_class = None;
            if let Some(b) = budget {
                let mut classes = class_at.get(&node).cloned().unwrap_or_default();
                let key = jeep_budget_class_key(&edge.highway, edge.tracktype.as_deref());
                let used = classes.entry(key).or_insert(0.0);
                *used += edge.length_m;
                if *used > b.class_cap(&edge.highway, edge.tracktype.as_deref()) + 1.0 {
                    continue;
                }
                new_class = Some(classes);
            }
            let cur = dist_cost.get(&edge.to).copied().unwrap_or(f64::INFINITY);
            if next_cost + 1e-9 < cur {
                dist_cost.insert(edge.to, next_cost);
                dist_horiz.insert(edge.to, horiz);
                if let Some(classes) = new_class {
                    class_at.insert(edge.to, classes);
                }
                prev.insert(edge.to, (node, edge.clone()));
                heap.push(HeapState {
                    cost: next_cost,
                    node: edge.to,
                });
            }
        }
    }
    (dist_cost, dist_horiz, prev)
}

const REACHABLE_PARK_RINGS_M: [f64; 6] = [2_500.0, 5_000.0, 10_000.0, 20_000.0, 40_000.0, 80_000.0];

/// Paved-anchor Dijkstra flood retained for park-candidate lookup.
#[derive(Debug, Clone)]
pub struct PavedReachTree {
    dist_cost: HashMap<u32, f64>,
    dist_horiz: HashMap<u32, f64>,
    prev: HashMap<u32, (u32, crate::osm::GraphEdge)>,
}

impl PavedReachTree {
    pub fn build(
        graph: &JeepRoadGraph,
        paved: &PavedAnchorIndex,
        max_jeep_m: f64,
        budget: Option<&JeepLengthBudget>,
    ) -> Self {
        let sources = paved.all_sources();
        let (dist_cost, dist_horiz, prev) =
            dijkstra_from_paved(graph, &sources, max_jeep_m, None, budget);
        Self {
            dist_cost,
            dist_horiz,
            prev,
        }
    }

    pub fn dist_horiz(&self, node: u32) -> Option<f64> {
        self.dist_horiz.get(&node).copied()
    }

    pub fn prev(&self) -> &HashMap<u32, (u32, crate::osm::GraphEdge)> {
        &self.prev
    }

    pub fn contains(&self, node: u32) -> bool {
        self.dist_cost.contains_key(&node)
    }

    pub fn is_empty(&self) -> bool {
        self.dist_cost.is_empty()
    }

    pub fn reachable_nodes(&self) -> impl Iterator<Item = u32> + '_ {
        self.dist_cost.keys().copied()
    }

    /// Closest paved-reachable jeep graph nodes near ``dest``, by crow rings.
    pub fn park_candidates(
        &self,
        routing: &OsmRouting,
        dest_lat: f64,
        dest_lon: f64,
        max_search_m: f64,
    ) -> Vec<u32> {
        if self.dist_cost.is_empty() {
            return Vec::new();
        }
        for &ring_m in &REACHABLE_PARK_RINGS_M {
            let ring_m = ring_m.min(max_search_m);
            let mut found: Vec<(u32, f64)> = routing
                .graph_nodes
                .nodes_within(dest_lat, dest_lon, ring_m)
                .into_iter()
                .filter(|n| self.dist_cost.contains_key(n))
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
}

/// Route from nearest paved anchor to park point (``road_lat``/``road_lon``).
pub fn route_jeep_detailed(
    graph: &JeepRoadGraph,
    paved: &PavedAnchorIndex,
    road_lat: f64,
    road_lon: f64,
    max_jeep_m: f64,
    budget: Option<&JeepLengthBudget>,
) -> Option<JeepRouteResult> {
    if graph.is_empty() {
        return None;
    }
    let dest = graph.nearest_node(road_lat, road_lon, SNAP_PARK_M)?;
    let sources = paved.sources_within(road_lat, road_lon, max_jeep_m + 5000.0);
    if sources.is_empty() {
        return None;
    }
    if sources.iter().any(|&(_, _, node)| node == dest) {
        return Some(reconstruct_jeep_route(graph, &HashMap::new(), dest));
    }

    let (dist_cost, _dist_horiz, prev) =
        dijkstra_from_paved(graph, &sources, max_jeep_m, Some(dest), budget);
    if !dist_cost.contains_key(&dest) {
        return None;
    }
    Some(reconstruct_jeep_route(graph, &prev, dest))
}

/// Drive from pavement along the OSM graph; stop at the reachable node closest to dest.
///
/// Display passes ``budget=None`` (ungated). Catalog passes ``Some`` for class/overall caps.
pub fn route_jeep_closest_approach(
    graph: &JeepRoadGraph,
    paved: &PavedAnchorIndex,
    dest_lat: f64,
    dest_lon: f64,
    max_jeep_m: f64,
    budget: Option<&JeepLengthBudget>,
) -> Option<JeepRouteResult> {
    if graph.is_empty() {
        return None;
    }
    let sources = paved.sources_within(dest_lat, dest_lon, max_jeep_m + 5000.0);
    if sources.is_empty() {
        return None;
    }

    let (dist_cost, dist_horiz, prev) =
        dijkstra_from_paved(graph, &sources, max_jeep_m, None, budget);
    let mut best: Option<(u32, f64)> = None;
    for &node in dist_cost.keys() {
        let (nlat, nlon) = graph.node_coords(node);
        let crow = haversine_m(nlat, nlon, dest_lat, dest_lon);
        if budget.is_some() {
            let jeep_horiz = dist_horiz.get(&node).copied().unwrap_or(0.0);
            if jeep_horiz <= 5.0 && crow > SNAP_PARK_M {
                continue;
            }
        }
        if best.map(|(_, d)| crow + 0.5 < d).unwrap_or(true) {
            best = Some((node, crow));
        }
    }
    let (dest_node, _) = best?;
    Some(reconstruct_jeep_route(graph, &prev, dest_node))
}

/// DEM elevation profile along a routed polyline.
pub fn profile_along_polyline<E: HikeSampleElev>(
    elev: &E,
    coords: &[(f64, f64)],
    step_m: f64,
    road_segments: &[JeepRoadSegment],
) -> Option<JeepProfileDetailed> {
    if coords.is_empty() {
        return None;
    }
    if coords.len() < 2 {
        let (lat, lon) = coords[0];
        let elev_m = elev.sample_elev_m(lat, lon);
        return Some(JeepProfileDetailed {
            jeep_m_3d: 0.0,
            horiz_m: 0.0,
            gain_m: 0.0,
            loss_m: 0.0,
            max_slope_deg: 0.0,
            max_grade_pct: 0.0,
            avg_grade_pct: 0.0,
            difficulty: jeep_difficulty_from_osm(road_segments).to_string(),
            profile: vec![HikeProfilePoint {
                dist_m: 0.0,
                elev_m,
                lat,
                lon,
            }],
            histogram: vec![],
            road_segments: road_segments.to_vec(),
        });
    }
    let step = step_m.max(10.0);
    let mut prev_lat = coords[0].0;
    let mut prev_lon = coords[0].1;
    let mut prev_elev = elev.sample_elev_m(prev_lat, prev_lon);
    let mut jeep_3d = 0.0;
    let mut horiz_total = 0.0;
    let mut gain_m = 0.0;
    let mut loss_m = 0.0;
    let mut max_slope = 0.0f64;
    let mut segments = Vec::new();
    let mut profile = Vec::new();
    profile.push(HikeProfilePoint {
        dist_m: 0.0,
        elev_m: prev_elev,
        lat: prev_lat,
        lon: prev_lon,
    });

    for w in coords.windows(2) {
        let (lat1, lon1) = w[0];
        let (lat2, lon2) = w[1];
        let seg_len = haversine_m(lat1, lon1, lat2, lon2);
        if seg_len <= 0.5 {
            continue;
        }
        let n = ((seg_len / step).ceil() as usize).max(1);
        for i in 1..=n {
            let t = (i as f64) / (n as f64);
            let lat = lat1 + t * (lat2 - lat1);
            let lon = lon1 + t * (lon2 - lon1);
            let e = elev.sample_elev_m(lat, lon);
            let horiz = haversine_m(prev_lat, prev_lon, lat, lon);
            if horiz > 0.5 {
                let vert = e - prev_elev;
                let slope = vert.atan2(horiz).to_degrees().abs();
                let grade_pct = slope_deg_to_grade_pct(slope);
                if slope.is_finite() {
                    max_slope = max_slope.max(slope);
                }
                let seg_3d = (horiz * horiz + vert * vert).sqrt();
                jeep_3d += seg_3d;
                horiz_total += horiz;
                if vert > 0.0 {
                    gain_m += vert;
                } else {
                    loss_m += -vert;
                }
                segments.push(HikeSegment {
                    dist_m: seg_3d,
                    horiz_m: horiz,
                    grade_pct,
                    slope_deg: slope,
                });
                profile.push(HikeProfilePoint {
                    dist_m: horiz_total,
                    elev_m: e,
                    lat,
                    lon,
                });
            }
            prev_lat = lat;
            prev_lon = lon;
            prev_elev = e;
        }
    }

    let avg_grade_pct = if horiz_total > 0.0 {
        segments
            .iter()
            .map(|s| s.grade_pct * s.horiz_m)
            .sum::<f64>()
            / horiz_total
    } else {
        0.0
    };
    let max_grade_pct = slope_deg_to_grade_pct(max_slope);
    let histogram = grade_histogram(&segments);

    Some(JeepProfileDetailed {
        jeep_m_3d: jeep_3d,
        horiz_m: horiz_total,
        gain_m,
        loss_m,
        max_slope_deg: max_slope,
        max_grade_pct,
        avg_grade_pct,
        difficulty: jeep_difficulty_from_osm(road_segments).to_string(),
        profile,
        histogram,
        road_segments: road_segments.to_vec(),
    })
}

pub fn stored_peak_jeep(detail: &JeepProfileDetailed) -> PeakJeepProfile {
    PeakJeepProfile {
        jeep_m_3d: round1(detail.jeep_m_3d),
        horiz_m: round1(detail.horiz_m),
        gain_m: round1(detail.gain_m),
        loss_m: round1(detail.loss_m),
        max_slope_deg: round1(detail.max_slope_deg),
        max_grade_pct: round1(detail.max_grade_pct),
        avg_grade_pct: round1(detail.avg_grade_pct),
        difficulty: detail.difficulty.clone(),
        profile: detail
            .profile
            .iter()
            .map(|p| peaky_preset::PeakJeepProfilePoint {
                dist_m: round1(p.dist_m),
                elev_m: round1(p.elev_m),
                lat: round_coord(p.lat),
                lon: round_coord(p.lon),
            })
            .collect(),
        histogram: detail
            .histogram
            .iter()
            .filter(|b| b.dist_m > 0.5)
            .map(|b| peaky_preset::PeakHikeGradeBucket {
                label: b.label.clone(),
                min_grade_pct: b.min_grade_pct,
                max_grade_pct: b.max_grade_pct,
                dist_m: round1(b.dist_m),
                pct_of_route: round1(b.pct_of_route),
            })
            .collect(),
        segments: detail
            .road_segments
            .iter()
            .filter(|s| s.dist_m > 0.5)
            .map(|s| peaky_preset::PeakJeepRoadSegment {
                highway: s.highway.clone(),
                tracktype: s.tracktype.clone(),
                dist_m: round1(s.dist_m),
            })
            .collect(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::hike::HikeSampleElev;
    use crate::osm::{GraphEdge, JeepRoadGraph, PavedAnchorIndex};

    struct FlatElev(f64);

    impl HikeSampleElev for FlatElev {
        fn sample_elev_m(&self, _lat: f64, _lon: f64) -> f64 {
            self.0
        }
    }

    fn easy_edge(to: u32, len: f64) -> GraphEdge {
        GraphEdge {
            to,
            length_m: len,
            highway: "secondary".into(),
            tracktype: None,
            surface: Some("paved".into()),
            fourwd_only: false,
        }
    }

    fn hard_edge(to: u32) -> GraphEdge {
        GraphEdge {
            to,
            length_m: 200.0,
            highway: "track".into(),
            tracktype: Some("grade5".into()),
            surface: None,
            fourwd_only: true,
        }
    }

    fn test_graph_easy_detour() -> (JeepRoadGraph, PavedAnchorIndex) {
        // 0 --easy(1000m)-- 1 --hard(200m)-- 2
        // 0 --easy(1200m)------------------- 2
        let mut g = JeepRoadGraph::new();
        let n0 = g.insert_node(38.0, -117.0);
        let n1 = g.insert_node(38.005, -117.0);
        let n2 = g.insert_node(38.008, -117.0);
        g.add_edge(n0, easy_edge(n1, 1000.0));
        g.add_edge(n1, easy_edge(n0, 1000.0));
        g.add_edge(n1, hard_edge(n2));
        g.add_edge(n2, hard_edge(n1));
        g.add_edge(n0, easy_edge(n2, 1200.0));
        g.add_edge(n2, easy_edge(n0, 1200.0));

        let paved = PavedAnchorIndex::from_nodes(&g, &[(n0, 38.0, -117.0)]);
        (g, paved)
    }

    #[test]
    fn easy_detour_beats_short_hard_track() {
        let (graph, paved) = test_graph_easy_detour();
        let dest_lat = 38.008;
        let dest_lon = -117.0;
        let route =
            route_jeep_detailed(&graph, &paved, dest_lat, dest_lon, 5000.0, None).unwrap();
        assert!(route.horiz_m >= 1100.0);
        assert!(route.horiz_m <= 1300.0);
    }

    #[test]
    fn max_jeep_length_rejects_distant_paved() {
        let (graph, paved) = test_graph_easy_detour();
        assert!(route_jeep_detailed(&graph, &paved, 38.05, -117.0, 500.0, None).is_none());
    }

    #[test]
    fn already_on_paved_is_zero_jeep() {
        let (graph, paved) = test_graph_easy_detour();
        let route = route_jeep_detailed(&graph, &paved, 38.0, -117.0, 5000.0, None).unwrap();
        assert_eq!(route.horiz_m, 0.0);
        assert!(route.road_segments.is_empty());
        let flat = FlatElev(2000.0);
        let detail = profile_along_polyline(&flat, &route.coords, 30.0, &route.road_segments).unwrap();
        assert_eq!(detail.horiz_m, 0.0);
        assert_eq!(detail.difficulty, "easy");
    }

    fn paved_plus_disconnected_dirt() -> (JeepRoadGraph, PavedAnchorIndex) {
        let mut g = JeepRoadGraph::new();
        let n0 = g.insert_node(38.0, -117.0);
        let n1 = g.insert_node(38.012, -117.0);
        let n2 = g.insert_node(38.018, -117.0);
        let len = haversine_m(38.0, -117.0, 38.012, -117.0);
        g.add_edge(
            n0,
            GraphEdge {
                to: n1,
                length_m: len,
                highway: "track".into(),
                tracktype: None,
                surface: None,
                fourwd_only: false,
            },
        );
        g.add_edge(
            n1,
            GraphEdge {
                to: n0,
                length_m: len,
                highway: "track".into(),
                tracktype: None,
                surface: None,
                fourwd_only: false,
            },
        );
        let _ = n2;
        let paved = PavedAnchorIndex::from_nodes(&g, &[(n0, 38.0, -117.0)]);
        (g, paved)
    }

    #[test]
    fn closest_approach_stops_on_connected_road() {
        let (graph, paved) = paved_plus_disconnected_dirt();
        let route =
            route_jeep_closest_approach(&graph, &paved, 38.019, -117.0, 20_000.0, None).unwrap();
        let end = *route.coords.last().unwrap();
        assert!((end.0 - 38.012).abs() < 0.0002);
        assert!(route.horiz_m > 1000.0);
        assert!((route.paved_lat - 38.0).abs() < 0.0002);
    }

    #[test]
    fn closest_approach_needs_paved() {
        let (graph, _) = paved_plus_disconnected_dirt();
        let empty = PavedAnchorIndex::from_nodes(&graph, &[]);
        assert!(route_jeep_closest_approach(&graph, &empty, 38.019, -117.0, 20_000.0, None).is_none());
    }

    fn paved_long_track(
        len_m: f64,
        highway: &str,
        tracktype: Option<&str>,
    ) -> (JeepRoadGraph, PavedAnchorIndex) {
        let mut g = JeepRoadGraph::new();
        let n0 = g.insert_node(38.0, -117.0);
        let n1 = g.insert_node(38.09, -117.0);
        let edge = GraphEdge {
            to: n1,
            length_m: len_m,
            highway: highway.into(),
            tracktype: tracktype.map(str::to_string),
            surface: None,
            fourwd_only: false,
        };
        let rev = GraphEdge {
            to: n0,
            length_m: len_m,
            highway: highway.into(),
            tracktype: tracktype.map(str::to_string),
            surface: None,
            fourwd_only: false,
        };
        g.add_edge(n0, edge);
        g.add_edge(n1, rev);
        let paved = PavedAnchorIndex::from_nodes(&g, &[(n0, 38.0, -117.0)]);
        (g, paved)
    }

    #[test]
    fn catalog_budget_rejects_six_mi_unclassified() {
        const SIX_MI: f64 = 9656.0;
        let (graph, paved) = paved_long_track(SIX_MI, "unclassified", None);
        let budget = JeepLengthBudget::from_extreme(&JeepExtremeBands::default());
        assert!(route_jeep_closest_approach(
            &graph,
            &paved,
            38.09,
            -117.0,
            budget.overall_m,
            Some(&budget),
        )
        .is_none());
    }

    #[test]
    fn catalog_budget_rejects_six_mi_grade5() {
        const SIX_MI: f64 = 9656.0;
        let (graph, paved) = paved_long_track(SIX_MI, "track", Some("grade5"));
        let budget = JeepLengthBudget::from_extreme(&JeepExtremeBands::default());
        assert!(route_jeep_closest_approach(
            &graph,
            &paved,
            38.09,
            -117.0,
            budget.overall_m,
            Some(&budget),
        )
        .is_none());
    }

    #[test]
    fn catalog_budget_allows_six_mi_grade1() {
        const SIX_MI: f64 = 9656.0;
        let (graph, paved) = paved_long_track(SIX_MI, "track", Some("grade1"));
        let budget = JeepLengthBudget::from_extreme(&JeepExtremeBands::default());
        let route = route_jeep_closest_approach(
            &graph,
            &paved,
            38.09,
            -117.0,
            budget.overall_m,
            Some(&budget),
        )
        .expect("grade1 under overall and class caps");
        assert!(route.horiz_m >= SIX_MI - 1.0);
    }

    #[test]
    fn display_ungated_allows_long_unclassified() {
        const SIX_MI: f64 = 9656.0;
        let (graph, paved) = paved_long_track(SIX_MI, "unclassified", None);
        let route = route_jeep_closest_approach(&graph, &paved, 38.09, -117.0, 80_467.0, None)
            .expect("display has no per-class caps");
        assert!(route.horiz_m >= SIX_MI - 1.0);
    }

    #[test]
    fn profile_along_polyline_flat() {
        let coords = vec![(38.0, -117.0), (38.01, -117.0)];
        let flat = FlatElev(2000.0);
        let detail = profile_along_polyline(&flat, &coords, 30.0, &[]).unwrap();
        assert!(detail.horiz_m > 1000.0);
        assert!(detail.gain_m < 1.0);
    }
}
