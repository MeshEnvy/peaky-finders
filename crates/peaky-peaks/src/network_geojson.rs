//! Access routing graphs as GeoJSON (paved / jeep / hiking roles).

use std::collections::{HashMap, HashSet};

use geojson::{Feature, FeatureCollection, Geometry, Value as GjValue};
use peaky_preset::load_preset;

use crate::access_warm::ProjectRouting;
use crate::hike::haversine_m;
use crate::network_index::GraphEdgeIndex;
use crate::osm::{JeepRoadGraph, OsmRoutingOpts};
use crate::segment_groups::role_layer_geojson;
use peaky_preset::RoadRole;

/// Drop polylines shorter than a short dash at this zoom.
const MIN_DRAW_PX: f64 = 12.0;
/// At a junction, continue only if a leg is within this many degrees of straight-through.
const STROKE_CONTINUE_MAX_DEG: f64 = 50.0;

/// Web Mercator meters per screen pixel (256 px world at z=0).
fn meters_per_pixel(z: f64, lat_deg: f64) -> f64 {
    156_543.0 * lat_deg.to_radians().cos().abs().max(0.2) / 2_f64.powf(z)
}

fn meters_to_lon_deg(m: f64, lat_deg: f64) -> f64 {
    m / (111_320.0 * lat_deg.to_radians().cos().abs().max(0.2))
}

/// 0 = local street, 1 = tertiary, 2 = secondary, 3 = primary, 4 = motorway/trunk.
fn highway_rank(highway: &str) -> u8 {
    match highway.strip_suffix("_link").unwrap_or(highway) {
        "motorway" | "trunk" => 4,
        "primary" => 3,
        "secondary" => 2,
        "tertiary" => 1,
        _ => 0,
    }
}

fn paved_min_zoom_for_rank(rank: u8) -> f64 {
    match rank {
        4 => 5.0,
        3 => 7.0,
        2 => 9.0,
        1 => 12.0,
        _ => 14.0,
    }
}

fn paved_visible_at_zoom(highway: &str, z: f64) -> bool {
    z >= paved_min_zoom_for_rank(highway_rank(highway))
}

/// Minimum polyline length (m) below this zoom; no geometry distortion.
fn drive_min_length_m(zoom: f64, center_lat: f64) -> f64 {
    if zoom >= 14.0 {
        0.0
    } else {
        MIN_DRAW_PX * meters_per_pixel(zoom, center_lat)
    }
}

fn edge_highway(graph: &JeepRoadGraph, a: u32, b: u32) -> &str {
    graph
        .edges(a)
        .iter()
        .find(|e| e.to == b)
        .map(|e| e.highway.as_str())
        .unwrap_or("unclassified")
}

/// Map viewport bounds (degrees). Edges with either endpoint inside are kept.
#[derive(Debug, Clone, Copy)]
pub struct NetworkViewBBox {
    pub west: f64,
    pub south: f64,
    pub east: f64,
    pub north: f64,
}

impl NetworkViewBBox {
    pub fn contains(&self, lat: f64, lon: f64) -> bool {
        lon >= self.west && lon <= self.east && lat >= self.south && lat <= self.north
    }
}

#[derive(Copy, Clone, Eq, PartialEq, Hash, Debug)]
pub enum NetworkLayer {
    Paved,
    Jeep,
    Hiking,
    Other,
    Private,
}

impl NetworkLayer {
    pub fn parse(s: &str) -> Option<Self> {
        match s {
            "paved" => Some(Self::Paved),
            "jeep" => Some(Self::Jeep),
            "hiking" => Some(Self::Hiking),
            "other" => Some(Self::Other),
            "private" => Some(Self::Private),
            _ => None,
        }
    }
}

fn undir_key(a: u32, b: u32) -> (u32, u32) {
    if a < b {
        (a, b)
    } else {
        (b, a)
    }
}

fn edge_in_view(graph: &JeepRoadGraph, a: u32, b: u32, view: NetworkViewBBox) -> bool {
    let (lat1, lon1) = graph.node_coords(a);
    let (lat2, lon2) = graph.node_coords(b);
    view.contains(lat1, lon1) || view.contains(lat2, lon2)
}

fn collect_role_edges(
    graph: &JeepRoadGraph,
    index: &GraphEdgeIndex,
    view: NetworkViewBBox,
    want: RoadRole,
) -> HashMap<(u32, u32), RoadRole> {
    let mut out = HashMap::new();
    for (a, b) in index.undirected_edges_in_bbox(view.west, view.south, view.east, view.north) {
        if !edge_in_view(graph, a, b, view) {
            continue;
        }
        let Some(edge) = graph.edges(a).iter().find(|e| e.to == b) else {
            continue;
        };
        if edge.role == want {
            out.insert((a, b), edge.role);
        }
    }
    out
}

fn filter_drive_edges_for_zoom(
    graph: &JeepRoadGraph,
    edges: &HashMap<(u32, u32), RoadRole>,
    role: RoadRole,
    zoom: f64,
) -> HashSet<(u32, u32)> {
    let mut seeds = HashSet::new();
    let mut extra = HashSet::new();
    for (&(a, b), &r) in edges {
        if r != role {
            continue;
        }
        let key = undir_key(a, b);
        if role == RoadRole::Paved && !paved_visible_at_zoom(edge_highway(graph, a, b), zoom) {
            // Local paved (unclassified, residential) joins at the tertiary zoom.
            // Strokes shorter than a dash are dropped after chaining.
            if zoom < paved_min_zoom_for_rank(1) {
                extra.insert(key);
            } else {
                seeds.insert(key);
            }
        } else {
            seeds.insert(key);
        }
    }
    if extra.is_empty() {
        return seeds;
    }
    // A lower-class paved way that continues a road already on screen stays.
    // Side legs past the continuation angle stay on the class zoom ladder.
    let mut adj: HashMap<u32, Vec<u32>> = HashMap::new();
    for &(a, b) in seeds.iter().chain(extra.iter()) {
        adj.entry(a).or_default().push(b);
        adj.entry(b).or_default().push(a);
    }
    let mut kept = seeds.clone();
    let mut stack: Vec<(u32, u32)> = seeds.iter().copied().collect();
    while let Some((a, b)) = stack.pop() {
        for (start, came) in [(b, a), (a, b)] {
            let mut cur = start;
            let mut prev = came;
            loop {
                let Some(neighbors) = adj.get(&cur) else {
                    break;
                };
                let Some(next) = stroke_continue_neighbor(graph, cur, prev, neighbors, &kept)
                else {
                    break;
                };
                let ek = undir_key(cur, next);
                if !extra.contains(&ek) {
                    break;
                }
                kept.insert(ek);
                prev = cur;
                cur = next;
                stack.push(ek);
            }
        }
    }
    kept
}

fn polyline_length_m(coords: &[[f64; 2]]) -> f64 {
    coords
        .windows(2)
        .map(|w| haversine_m(w[0][1], w[0][0], w[1][1], w[1][0]))
        .sum()
}

fn filter_short_lines(lines: Vec<Vec<[f64; 2]>>, min_len_m: f64) -> Vec<Vec<[f64; 2]>> {
    lines
        .into_iter()
        .filter(|line| line.len() >= 2 && polyline_length_m(line) + 1.0 >= min_len_m)
        .collect()
}

fn node_bearing(graph: &JeepRoadGraph, from: u32, to: u32) -> f64 {
    let (lat1, lon1) = graph.node_coords(from);
    let (lat2, lon2) = graph.node_coords(to);
    let dx = (lon2 - lon1) * lat1.to_radians().cos();
    let dy = lat2 - lat1;
    dy.atan2(dx).to_degrees()
}

fn bearing_delta_deg(a: f64, b: f64) -> f64 {
    let mut d = (b - a).abs() % 360.0;
    if d > 180.0 {
        d = 360.0 - d;
    }
    d
}

fn stroke_continue_neighbor(
    graph: &JeepRoadGraph,
    cur: u32,
    came_from: u32,
    neighbors: &[u32],
    visited: &HashSet<(u32, u32)>,
) -> Option<u32> {
    let incoming = node_bearing(graph, came_from, cur);
    let mut best: Option<(u32, f64)> = None;
    for &n in neighbors {
        if n == came_from {
            continue;
        }
        let ek = undir_key(cur, n);
        if visited.contains(&ek) {
            continue;
        }
        if neighbors.len() == 2 {
            return Some(n);
        }
        let outgoing = node_bearing(graph, cur, n);
        let delta = bearing_delta_deg(incoming, outgoing);
        if delta <= STROKE_CONTINUE_MAX_DEG {
            if best.map(|(_, bd)| delta < bd).unwrap_or(true) {
                best = Some((n, delta));
            }
        }
    }
    best.map(|(n, _)| n)
}

fn extend_stroke(
    graph: &JeepRoadGraph,
    adj: &HashMap<u32, Vec<u32>>,
    visited: &mut HashSet<(u32, u32)>,
    path: &mut Vec<u32>,
    at_front: bool,
) {
    loop {
        let (cur, came_from) = if at_front {
            (path[0], path[1])
        } else {
            let len = path.len();
            (path[len - 1], path[len - 2])
        };
        let Some(neighbors) = adj.get(&cur) else {
            break;
        };
        let Some(other) = stroke_continue_neighbor(graph, cur, came_from, neighbors, visited)
        else {
            break;
        };
        visited.insert(undir_key(cur, other));
        if at_front {
            path.insert(0, other);
        } else {
            path.push(other);
        }
    }
}

fn chain_edges(graph: &JeepRoadGraph, edges: HashSet<(u32, u32)>) -> Vec<Vec<[f64; 2]>> {
    if edges.is_empty() {
        return Vec::new();
    }
    let mut adj: HashMap<u32, Vec<u32>> = HashMap::new();
    for (a, b) in &edges {
        adj.entry(*a).or_default().push(*b);
        adj.entry(*b).or_default().push(*a);
    }
    let mut visited = HashSet::new();
    let mut lines = Vec::new();
    for (a, b) in edges {
        let key = undir_key(a, b);
        if visited.contains(&key) {
            continue;
        }
        let mut path = vec![a, b];
        visited.insert(key);
        extend_stroke(graph, &adj, &mut visited, &mut path, true);
        extend_stroke(graph, &adj, &mut visited, &mut path, false);

        let mut coords: Vec<[f64; 2]> = path
            .iter()
            .map(|&n| {
                let (lat, lon) = graph.node_coords(n);
                [lon, lat]
            })
            .collect();
        coords = dedupe_consecutive_coords(coords);
        if coords.len() >= 2 {
            lines.push(coords);
        }
    }
    lines
}

fn dedupe_consecutive_coords(coords: Vec<[f64; 2]>) -> Vec<[f64; 2]> {
    let mut out = Vec::with_capacity(coords.len());
    for c in coords {
        if out
            .last()
            .map(|p: &[f64; 2]| p[0] == c[0] && p[1] == c[1])
            .unwrap_or(false)
        {
            continue;
        }
        out.push(c);
    }
    out
}

fn empty_fc() -> FeatureCollection {
    FeatureCollection {
        bbox: None,
        features: Vec::new(),
        foreign_members: None,
    }
}

/// One MultiLineString feature keeps JSON and MapLibre overhead low.
fn lines_to_fc(lines: Vec<Vec<[f64; 2]>>) -> FeatureCollection {
    if lines.is_empty() {
        return empty_fc();
    }
    let multi: Vec<Vec<Vec<f64>>> = lines
        .into_iter()
        .map(|coords| coords.into_iter().map(|c| vec![c[0], c[1]]).collect())
        .collect();
    FeatureCollection {
        bbox: None,
        features: vec![Feature {
            bbox: None,
            geometry: Some(Geometry::new(GjValue::MultiLineString(multi))),
            id: None,
            properties: None,
            foreign_members: None,
        }],
        foreign_members: None,
    }
}

fn drive_role_fc(
    graph: &JeepRoadGraph,
    edges: &HashMap<(u32, u32), RoadRole>,
    role: RoadRole,
    zoom: f64,
    view: NetworkViewBBox,
) -> FeatureCollection {
    let center_lat = (view.south + view.north) * 0.5;
    let set = filter_drive_edges_for_zoom(graph, edges, role, zoom);
    let chained = chain_edges(graph, set);
    let min_m = drive_min_length_m(zoom, center_lat);
    lines_to_fc(filter_short_lines(chained, min_m))
}

fn layer_role(layer: NetworkLayer) -> RoadRole {
    match layer {
        NetworkLayer::Paved => RoadRole::Paved,
        NetworkLayer::Jeep => RoadRole::Jeep,
        NetworkLayer::Hiking => RoadRole::Hiking,
        NetworkLayer::Other => RoadRole::Other,
        NetworkLayer::Private => RoadRole::Private,
    }
}

fn role_fc(
    routing: &ProjectRouting,
    role: RoadRole,
    view: NetworkViewBBox,
    zoom: f64,
) -> FeatureCollection {
    let detailed = zoom >= 12.0;
    let center_lat = (view.south + view.north) * 0.5;
    let min_m = if detailed {
        0.0
    } else {
        drive_min_length_m(zoom, center_lat)
    };
    role_layer_geojson(
        &routing.osm().segment_groups,
        role,
        view,
        detailed,
        min_m,
    )
}

/// One access-network layer clipped to the map viewport.
pub fn routing_network_layer_geojson(
    routing: &ProjectRouting,
    _opts: &OsmRoutingOpts,
    layer: NetworkLayer,
    view: NetworkViewBBox,
    zoom: f64,
) -> FeatureCollection {
    match layer {
        NetworkLayer::Hiking if zoom < 11.0 => empty_fc(),
        NetworkLayer::Hiking | NetworkLayer::Other | NetworkLayer::Private => {
            role_fc(routing, layer_role(layer), view, zoom)
        }
        NetworkLayer::Paved | NetworkLayer::Jeep if zoom >= 12.0 => {
            role_fc(routing, layer_role(layer), view, zoom)
        }
        NetworkLayer::Paved | NetworkLayer::Jeep => {
            let graph = &routing.osm().graph;
            let index = routing.drive_edge_index();
            let role = layer_role(layer);
            let edges = collect_role_edges(graph, index, view, role);
            drive_role_fc(graph, &edges, role, zoom, view)
        }
    }
}

/// Paved and jeep FeatureCollections from one drive-graph classification pass.
pub fn routing_network_drive_layers_geojson(
    routing: &ProjectRouting,
    _opts: &OsmRoutingOpts,
    view: NetworkViewBBox,
    zoom: f64,
) -> (FeatureCollection, FeatureCollection) {
    if zoom >= 12.0 {
        return (
            role_fc(routing, RoadRole::Paved, view, zoom),
            role_fc(routing, RoadRole::Jeep, view, zoom),
        );
    }
    let graph = &routing.osm().graph;
    let index = routing.drive_edge_index();
    let paved_edges = collect_role_edges(graph, index, view, RoadRole::Paved);
    let jeep_edges = collect_role_edges(graph, index, view, RoadRole::Jeep);
    let paved = drive_role_fc(graph, &paved_edges, RoadRole::Paved, zoom, view);
    let jeep = drive_role_fc(graph, &jeep_edges, RoadRole::Jeep, zoom, view);
    (paved, jeep)
}

/// JSON object `{ "paved": …, "jeep": … }` for a single combined request.
pub fn routing_network_drive_geojson_bytes(
    routing: &ProjectRouting,
    opts: &OsmRoutingOpts,
    view: NetworkViewBBox,
    zoom: f64,
) -> serde_json::Result<Vec<u8>> {
    let (paved, jeep) = routing_network_drive_layers_geojson(routing, opts, view, zoom);
    serde_json::to_vec(&serde_json::json!({ "paved": paved, "jeep": jeep }))
}

/// Serialize one layer as a GeoJSON FeatureCollection.
pub fn routing_network_layer_geojson_bytes(
    routing: &ProjectRouting,
    opts: &OsmRoutingOpts,
    layer: NetworkLayer,
    view: NetworkViewBBox,
    zoom: f64,
) -> serde_json::Result<Vec<u8>> {
    serde_json::to_vec(&routing_network_layer_geojson(
        routing, opts, layer, view, zoom,
    ))
}

/// Load preset access meta and build opts (serve handlers).
pub fn osm_routing_opts_from_preset(config_path: &std::path::Path) -> OsmRoutingOpts {
    let preset = load_preset(config_path).unwrap_or_default();
    let roads = peaky_preset::ensure_roads_book(config_path)
        .unwrap_or_else(|_| peaky_preset::RoadsBook::default());
    crate::osm::osm_routing_opts_with_roads(&preset.resolved_access_meta(), &roads)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::osm::JeepRoadGraph;

    fn add_seg(
        graph: &mut JeepRoadGraph,
        map: &mut HashMap<i64, u32>,
        next: &mut i64,
        a: (f64, f64),
        b: (f64, f64),
        highway: &str,
        surface: Option<&str>,
    ) {
        let na = graph.get_or_insert_node_public(*next, a.0, a.1, map);
        *next -= 1;
        let nb = graph.get_or_insert_node_public(*next, b.0, b.1, map);
        *next -= 1;
        graph.add_way_segment_public(na, nb, a.0, a.1, b.0, b.1, highway, None, surface, false);
    }

    fn add_seg_role(
        graph: &mut JeepRoadGraph,
        map: &mut HashMap<i64, u32>,
        next: &mut i64,
        a: (f64, f64),
        b: (f64, f64),
        highway: &str,
        surface: Option<&str>,
        role: RoadRole,
        group_id: u32,
    ) {
        let na = graph.get_or_insert_node_public(*next, a.0, a.1, map);
        *next -= 1;
        let nb = graph.get_or_insert_node_public(*next, b.0, b.1, map);
        *next -= 1;
        graph.add_way_segment_classified(
            na, nb, a.0, a.1, b.0, b.1, highway, None, surface, false, role, group_id,
        );
    }

    fn wide_view() -> NetworkViewBBox {
        NetworkViewBBox {
            west: -120.0,
            south: 38.0,
            east: -118.0,
            north: 40.0,
        }
    }

    fn drive_edges_in_view(
        graph: &JeepRoadGraph,
        view: NetworkViewBBox,
        role: RoadRole,
    ) -> HashMap<(u32, u32), RoadRole> {
        let index = GraphEdgeIndex::build(graph);
        collect_role_edges(graph, &index, view, role)
    }

    fn jeep_lines(graph: &JeepRoadGraph, zoom: f64) -> Vec<Vec<[f64; 2]>> {
        let view = wide_view();
        let center_lat = (view.south + view.north) * 0.5;
        let all = drive_edges_in_view(graph, view, RoadRole::Jeep);
        let set = filter_drive_edges_for_zoom(graph, &all, RoadRole::Jeep, zoom);
        let chained = chain_edges(graph, set);
        filter_short_lines(chained, drive_min_length_m(zoom, center_lat))
    }

    fn add_track_run(graph: &mut JeepRoadGraph, steps: usize, dlat: f64, lon: f64, mut id: i64) {
        let mut map = HashMap::new();
        let mut prev: Option<(u32, f64)> = None;
        let mut lat = 39.0;
        for _ in 0..=steps {
            let n = graph.get_or_insert_node_public(id, lat, lon, &mut map);
            id -= 1;
            if let Some((pn, plat)) = prev {
                graph
                    .add_way_segment_public(pn, n, plat, lon, lat, lon, "track", None, None, false);
            }
            prev = Some((n, lat));
            lat += dlat;
        }
    }

    #[test]
    fn chains_three_primary_nodes_into_one_line() {
        let mut graph = JeepRoadGraph::new();
        let mut map = HashMap::new();
        let mut id = -1i64;
        let n0 = graph.get_or_insert_node_public(id, 39.0, -119.0, &mut map);
        id -= 1;
        let n1 = graph.get_or_insert_node_public(id, 39.01, -119.0, &mut map);
        id -= 1;
        let n2 = graph.get_or_insert_node_public(id, 39.02, -119.0, &mut map);
        graph.add_way_segment_public(
            n0, n1, 39.0, -119.0, 39.01, -119.0, "primary", None, None, false,
        );
        graph.add_way_segment_public(
            n1, n2, 39.01, -119.0, 39.02, -119.0, "primary", None, None, false,
        );
        let view = NetworkViewBBox {
            west: -120.0,
            south: 38.0,
            east: -118.0,
            north: 40.0,
        };
        let edges = drive_edges_in_view(&graph, view, RoadRole::Paved);
        assert_eq!(edges.len(), 2);
        let lines = chain_edges(&graph, edges.keys().copied().collect());
        assert_eq!(lines.len(), 1);
        assert_eq!(lines[0].len(), 3);
    }

    #[test]
    fn through_track_stays_one_stroke_at_coarse_zoom() {
        let mut graph = JeepRoadGraph::new();
        let mut map = HashMap::new();
        let mut id = -1i64;
        let south = graph.get_or_insert_node_public(id, 39.0, -119.0, &mut map);
        id -= 1;
        let mid = graph.get_or_insert_node_public(id, 39.05, -119.0, &mut map);
        id -= 1;
        let north = graph.get_or_insert_node_public(id, 39.10, -119.0, &mut map);
        id -= 1;
        let west = graph.get_or_insert_node_public(id, 39.05, -119.002, &mut map);
        id -= 1;
        let east = graph.get_or_insert_node_public(id, 39.05, -118.998, &mut map);
        graph.add_way_segment_public(
            south, mid, 39.0, -119.0, 39.05, -119.0, "track", None, None, false,
        );
        graph.add_way_segment_public(
            mid, north, 39.05, -119.0, 39.10, -119.0, "track", None, None, false,
        );
        graph.add_way_segment_public(
            west, mid, 39.05, -119.002, 39.05, -119.0, "track", None, None, false,
        );
        graph.add_way_segment_public(
            mid, east, 39.05, -119.0, 39.05, -118.998, "track", None, None, false,
        );

        let coarse = jeep_lines(&graph, 8.0);
        assert_eq!(
            coarse.len(),
            1,
            "NS through-road should be one stroke at z8"
        );
        let line = &coarse[0];
        let min_lat = line.iter().map(|c| c[1]).fold(f64::INFINITY, f64::min);
        let max_lat = line.iter().map(|c| c[1]).fold(f64::NEG_INFINITY, f64::max);
        assert!(
            min_lat <= 39.05 && max_lat >= 39.05,
            "stroke spans the crossing latitude"
        );
        assert!(polyline_length_m(line) > 5_000.0);

        let fine = jeep_lines(&graph, 14.0);
        assert_eq!(fine.len(), 2, "short spur is its own line at z14");
        let mid_lon = -119.0;
        let at_mid = |line: &Vec<[f64; 2]>| {
            line.iter()
                .any(|c| (c[1] - 39.05).abs() < 0.001 && (c[0] - mid_lon).abs() < 0.001)
        };
        assert!(fine.iter().all(at_mid), "both lines meet at the crossing");
    }

    #[test]
    fn coarse_jeep_drops_short_track() {
        let mut graph = JeepRoadGraph::new();
        let mut map = HashMap::new();
        let mut id = -1i64;
        add_seg(
            &mut graph,
            &mut map,
            &mut id,
            (39.0, -119.0),
            (39.0005, -119.0),
            "track",
            None,
        );
        let lines = jeep_lines(&graph, 8.0);
        assert!(lines.is_empty());
    }

    #[test]
    fn coarse_jeep_keeps_road_made_of_short_edges() {
        let mut graph = JeepRoadGraph::new();
        // ~2.2 km of short segments. Kept once zoomed in past the regional cutoff.
        add_track_run(&mut graph, 10, 0.002, -119.0, -1);
        let lines = jeep_lines(&graph, 12.0);
        assert_eq!(lines.len(), 1);
        assert!(polyline_length_m(&lines[0]) > 1500.0);
        assert!(jeep_lines(&graph, 8.0).is_empty());
    }

    #[test]
    fn low_zoom_keeps_parallel_tracks_separate() {
        let mut graph = JeepRoadGraph::new();
        add_track_run(&mut graph, 20, 0.01, -119.0, -1);
        add_track_run(&mut graph, 20, 0.01, -119.005, -10_000);
        let lines = jeep_lines(&graph, 8.0);
        assert_eq!(
            lines.len(),
            2,
            "parallel tracks stay distinct without corridor merge"
        );
    }

    #[test]
    fn residential_without_surface_is_jeep() {
        let mut graph = JeepRoadGraph::new();
        let mut map = HashMap::new();
        let mut id = -1i64;
        add_seg(
            &mut graph,
            &mut map,
            &mut id,
            (39.0, -119.0),
            (39.01, -119.0),
            "residential",
            None,
        );
        let all = drive_edges_in_view(&graph, wide_view(), RoadRole::Jeep);
        assert!(all.len() == 1);
    }

    #[test]
    fn unclassified_without_surface_is_jeep() {
        let mut graph = JeepRoadGraph::new();
        let mut map = HashMap::new();
        let mut id = -1i64;
        add_seg(
            &mut graph,
            &mut map,
            &mut id,
            (39.0, -119.0),
            (39.01, -119.0),
            "unclassified",
            None,
        );
        add_seg(
            &mut graph,
            &mut map,
            &mut id,
            (39.02, -119.0),
            (39.03, -119.0),
            "tertiary",
            None,
        );
        let jeep = drive_edges_in_view(&graph, wide_view(), RoadRole::Jeep);
        assert_eq!(jeep.len(), 2);
    }

    #[test]
    fn roads_book_paved_tertiary_stays_paved_on_drive_overlay() {
        let mut graph = JeepRoadGraph::new();
        let mut map = HashMap::new();
        let mut id = -1i64;
        add_seg_role(
            &mut graph,
            &mut map,
            &mut id,
            (39.0, -119.0),
            (39.01, -119.0),
            "tertiary",
            None,
            RoadRole::Paved,
            1,
        );
        let paved = drive_edges_in_view(&graph, wide_view(), RoadRole::Paved);
        assert_eq!(paved.len(), 1);
    }

    #[test]
    fn secondary_without_surface_is_paved() {
        let mut graph = JeepRoadGraph::new();
        let mut map = HashMap::new();
        let mut id = -1i64;
        add_seg(
            &mut graph,
            &mut map,
            &mut id,
            (39.0, -119.0),
            (39.01, -119.0),
            "secondary",
            None,
        );
        let paved = drive_edges_in_view(&graph, wide_view(), RoadRole::Paved);
        assert_eq!(paved.len(), 1);
    }

    #[test]
    fn residential_dirt_stays_jeep() {
        let mut graph = JeepRoadGraph::new();
        let mut map = HashMap::new();
        let mut id = -1i64;
        add_seg(
            &mut graph,
            &mut map,
            &mut id,
            (39.0, -119.0),
            (39.01, -119.0),
            "residential",
            Some("dirt"),
        );
        let jeep = drive_edges_in_view(&graph, wide_view(), RoadRole::Jeep);
        assert_eq!(jeep.len(), 1);
    }

    #[test]
    fn service_roads_stay_jeep() {
        let mut graph = JeepRoadGraph::new();
        let mut map = HashMap::new();
        let mut id = -1i64;
        add_seg(
            &mut graph,
            &mut map,
            &mut id,
            (39.0, -119.0),
            (39.001, -119.0),
            "service",
            None,
        );
        add_seg(
            &mut graph,
            &mut map,
            &mut id,
            (39.002, -119.0),
            (39.003, -119.0),
            "service",
            Some("asphalt"),
        );
        let jeep = drive_edges_in_view(&graph, wide_view(), RoadRole::Jeep);
        assert_eq!(jeep.len(), 2);
    }

    #[test]
    fn unpaved_service_stays_jeep() {
        let mut graph = JeepRoadGraph::new();
        let mut map = HashMap::new();
        let mut id = -1i64;
        add_seg(
            &mut graph,
            &mut map,
            &mut id,
            (39.0, -119.0),
            (39.01, -119.0),
            "service",
            Some("gravel"),
        );
        let all = drive_edges_in_view(&graph, wide_view(), RoadRole::Jeep);
        assert!(all.len() == 1);
    }

    #[test]
    fn coarse_paved_keeps_long_primary() {
        let mut graph = JeepRoadGraph::new();
        let mut map = HashMap::new();
        let mut id = -1i64;
        add_seg(
            &mut graph,
            &mut map,
            &mut id,
            (39.0, -119.0),
            (39.05, -119.0),
            "primary",
            None,
        );
        let view = NetworkViewBBox {
            west: -120.0,
            south: 38.0,
            east: -118.0,
            north: 40.0,
        };
        let paved_edges = drive_edges_in_view(&graph, view, RoadRole::Paved);
        let paved = filter_drive_edges_for_zoom(&graph, &paved_edges, RoadRole::Paved, 8.0);
        assert_eq!(paved.len(), 1);
    }

    #[test]
    fn paved_continuation_keeps_unclassified_link() {
        let mut graph = JeepRoadGraph::new();
        let mut map = HashMap::new();
        let mut id = -1i64;
        let n0 = graph.get_or_insert_node_public(id, 39.00, -119.00, &mut map);
        id -= 1;
        let n1 = graph.get_or_insert_node_public(id, 39.01, -119.00, &mut map);
        id -= 1;
        let n2 = graph.get_or_insert_node_public(id, 39.02, -119.00, &mut map);
        graph.add_way_segment_classified(
            n0,
            n1,
            39.00,
            -119.00,
            39.01,
            -119.00,
            "tertiary",
            None,
            None,
            false,
            RoadRole::Paved,
            1,
        );
        graph.add_way_segment_classified(
            n1,
            n2,
            39.01,
            -119.00,
            39.02,
            -119.00,
            "unclassified",
            None,
            Some("asphalt"),
            false,
            RoadRole::Paved,
            2,
        );
        let paved_edges = drive_edges_in_view(&graph, wide_view(), RoadRole::Paved);
        let paved = filter_drive_edges_for_zoom(&graph, &paved_edges, RoadRole::Paved, 12.0);
        assert!(paved.contains(&undir_key(n0, n1)));
        assert!(paved.contains(&undir_key(n1, n2)));
        let hidden = filter_drive_edges_for_zoom(&graph, &paved_edges, RoadRole::Paved, 11.0);
        assert!(hidden.is_empty());
    }

    #[test]
    fn residential_street_hidden_at_city_zoom() {
        let mut graph = JeepRoadGraph::new();
        let mut map = HashMap::new();
        let mut id = -1i64;
        add_seg(
            &mut graph,
            &mut map,
            &mut id,
            (39.0, -119.0),
            (39.01, -119.0),
            "residential",
            Some("asphalt"),
        );
        let paved_edges = drive_edges_in_view(&graph, wide_view(), RoadRole::Paved);
        assert_eq!(paved_edges.len(), 1);
        assert!(
            filter_drive_edges_for_zoom(&graph, &paved_edges, RoadRole::Paved, 11.0).is_empty()
        );
        assert_eq!(
            filter_drive_edges_for_zoom(&graph, &paved_edges, RoadRole::Paved, 14.0).len(),
            1
        );
    }

    #[test]
    fn chains_collinear_segments() {
        let mut graph = JeepRoadGraph::new();
        let mut map = HashMap::new();
        let mut id = -1i64;
        let n0 = graph.get_or_insert_node_public(id, 39.0, -119.0, &mut map);
        id -= 1;
        let n1 = graph.get_or_insert_node_public(id, 39.001, -119.0, &mut map);
        id -= 1;
        let n2 = graph.get_or_insert_node_public(id, 39.002, -119.0, &mut map);
        graph.add_way_segment_public(
            n0, n1, 39.0, -119.0, 39.001, -119.0, "primary", None, None, false,
        );
        graph.add_way_segment_public(
            n1, n2, 39.001, -119.0, 39.002, -119.0, "primary", None, None, false,
        );
        let edges: HashSet<_> = [(n0, n1), (n1, n2)].into_iter().collect();
        let lines = chain_edges(&graph, edges);
        assert_eq!(lines.len(), 1);
        assert_eq!(lines[0].len(), 3);
    }

    #[test]
    fn undirected_edge_not_doubled() {
        let mut graph = JeepRoadGraph::new();
        let mut map = HashMap::new();
        let mut id = -1i64;
        add_seg(
            &mut graph,
            &mut map,
            &mut id,
            (39.0, -119.0),
            (39.01, -119.0),
            "track",
            None,
        );
        let view = NetworkViewBBox {
            west: -120.0,
            south: 38.0,
            east: -118.0,
            north: 40.0,
        };
        let edges = drive_edges_in_view(&graph, view, RoadRole::Jeep);
        assert_eq!(edges.len(), 1);
    }

    #[test]
    fn jeep_custom_in_graph_without_post_paste() {
        let mut graph = JeepRoadGraph::new();
        let mut map = HashMap::new();
        let mut id = -1i64;
        add_seg_role(
            &mut graph,
            &mut map,
            &mut id,
            (39.0, -119.0),
            (39.02, -119.0),
            "custom",
            None,
            RoadRole::Jeep,
            7,
        );
        let jeep = drive_edges_in_view(&graph, wide_view(), RoadRole::Jeep);
        assert_eq!(jeep.len(), 1);
    }

    #[test]
    fn other_role_absent_from_paved_and_jeep_layers() {
        let mut graph = JeepRoadGraph::new();
        let mut map = HashMap::new();
        let mut id = -1i64;
        add_seg_role(
            &mut graph,
            &mut map,
            &mut id,
            (39.0, -119.0),
            (39.01, -119.0),
            "footway",
            None,
            RoadRole::Other,
            3,
        );
        assert!(drive_edges_in_view(&graph, wide_view(), RoadRole::Paved).is_empty());
        assert!(drive_edges_in_view(&graph, wide_view(), RoadRole::Jeep).is_empty());
    }

    #[test]
    fn trusted_routing_skips_jeep_custom_edge() {
        use crate::osm::GraphEdge;
        let edge = GraphEdge {
            to: 1,
            length_m: 100.0,
            highway: "custom".into(),
            tracktype: None,
            surface: None,
            fourwd_only: false,
            role: RoadRole::Jeep,
            group_id: 1,
        };
        assert!(!edge.trusted_jeep_traversable());
    }
}
