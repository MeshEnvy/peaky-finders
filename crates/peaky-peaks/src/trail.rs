//! Foot-trail graph routing (GTLF layers 4+5).

use std::cmp::Ordering;
use std::collections::{BinaryHeap, HashMap};

use crate::hike::haversine_m;
use crate::jeep::JeepRouteResult;
use crate::osm::{GraphEdge, JeepRoadGraph, JeepRoadIndex, RoadPoint};

/// Snap jeep park to a foot-trail node within this radius (m).
pub const TRAIL_PARK_SNAP_M: f64 = 80.0;
/// Max horizontal foot-trail route (m).
pub const TRAIL_MAX_M: f64 = 15_000.0;

pub struct TrailRouting {
    pub graph: JeepRoadGraph,
    pub samples: JeepRoadIndex,
}

impl TrailRouting {
    pub fn empty() -> Self {
        Self {
            graph: JeepRoadGraph::new(),
            samples: JeepRoadIndex::from_samples(Vec::new(), 0),
        }
    }

    pub fn is_empty(&self) -> bool {
        self.graph.is_empty()
    }
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

fn reconstruct_trail_route(
    graph: &JeepRoadGraph,
    prev: &HashMap<u32, (u32, GraphEdge)>,
    dest_node: u32,
) -> JeepRouteResult {
    let mut path_nodes = vec![dest_node];
    let mut node = dest_node;
    while let Some((prev_node, _edge)) = prev.get(&node) {
        node = *prev_node;
        path_nodes.push(node);
    }
    path_nodes.reverse();
    let coords: Vec<(f64, f64)> = path_nodes
        .iter()
        .map(|&idx| graph.node_coords(idx))
        .collect();
    let horiz_m: f64 = coords
        .windows(2)
        .map(|w| haversine_m(w[0].0, w[0].1, w[1].0, w[1].1))
        .sum();
    let (start_lat, start_lon) = coords.first().copied().unwrap_or((0.0, 0.0));
    JeepRouteResult {
        paved_lat: start_lat,
        paved_lon: start_lon,
        coords,
        horiz_m,
        road_segments: Vec::new(),
    }
}

/// Walk designated foot trail from park snap to the node closest to dest.
pub fn route_trail_closest_approach(
    trails: &TrailRouting,
    start_lat: f64,
    start_lon: f64,
    dest_lat: f64,
    dest_lon: f64,
    max_trail_m: f64,
) -> Option<JeepRouteResult> {
    if trails.graph.is_empty() {
        return None;
    }
    let start = trails.graph.nearest_node(start_lat, start_lon, TRAIL_PARK_SNAP_M)?;
    let mut dist = HashMap::<u32, f64>::new();
    let mut prev = HashMap::<u32, (u32, GraphEdge)>::new();
    let mut heap = BinaryHeap::new();
    dist.insert(start, 0.0);
    heap.push(HeapState { cost: 0.0, node: start });

    while let Some(HeapState { cost, node }) = heap.pop() {
        if cost > *dist.get(&node).unwrap_or(&f64::INFINITY) + 1e-9 {
            continue;
        }
        for edge in trails.graph.edges(node) {
            let next = cost + edge.length_m;
            if next > max_trail_m + 1.0 {
                continue;
            }
            let cur = dist.get(&edge.to).copied().unwrap_or(f64::INFINITY);
            if next + 1e-9 < cur {
                dist.insert(edge.to, next);
                prev.insert(edge.to, (node, edge.clone()));
                heap.push(HeapState {
                    cost: next,
                    node: edge.to,
                });
            }
        }
    }

    let mut best: Option<(u32, f64)> = None;
    for &node in dist.keys() {
        let (nlat, nlon) = trails.graph.node_coords(node);
        let crow = haversine_m(nlat, nlon, dest_lat, dest_lon);
        if best.map(|(_, d)| crow < d).unwrap_or(true) {
            best = Some((node, crow));
        }
    }
    let (dest_node, _) = best?;
    Some(reconstruct_trail_route(&trails.graph, &prev, dest_node))
}

pub fn build_trail_graph(
    lines: &[Vec<(f64, f64)>],
    sample_step_m: f64,
) -> TrailRouting {
    let mut graph = JeepRoadGraph::new();
    let mut node_map: HashMap<i64, u32> = HashMap::new();
    let mut samples: Vec<RoadPoint> = Vec::new();
    let mut way_count = 0usize;
    let mut next_id: i64 = -1;

    for coords in lines {
        if coords.len() < 2 {
            continue;
        }
        way_count += 1;
        samples.extend(crate::osm::sample_way_public(coords, sample_step_m));
        let mut vert_nodes = Vec::with_capacity(coords.len());
        for &(lat, lon) in coords {
            let id = next_id;
            next_id -= 1;
            let n = graph.get_or_insert_node_public(id, lat, lon, &mut node_map);
            vert_nodes.push(n);
        }
        for i in 0..coords.len() - 1 {
            let (lat1, lon1) = coords[i];
            let (lat2, lon2) = coords[i + 1];
            graph.add_way_segment_public(
                vert_nodes[i],
                vert_nodes[i + 1],
                lat1,
                lon1,
                lat2,
                lon2,
                "path",
                None,
                None,
                false,
            );
        }
    }

    TrailRouting {
        graph,
        samples: JeepRoadIndex::from_samples(samples, way_count),
    }
}
