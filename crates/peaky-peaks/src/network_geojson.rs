//! Access routing graphs as GeoJSON (paved / jeep / hiking roles).

use std::collections::{HashMap, HashSet};

use geojson::{Feature, FeatureCollection, Geometry, Value as GjValue};
use peaky_preset::load_preset;

use crate::access_warm::ProjectRouting;
use crate::hike::haversine_m;
use crate::osm::{graph_edge_is_paved_anchor, GraphEdge, JeepRoadGraph, OsmRoutingOpts};
use crate::trail::TrailRouting;

/// Douglas–Peucker tolerance: bends smaller than this on screen are removed.
const SIMPLIFY_PX: f64 = 1.5;
/// Drop polylines shorter than a short dash at this zoom.
const MIN_DRAW_PX: f64 = 12.0;
/// Parallel tracks closer than this share one corridor (longest original line wins).
const MERGE_PX: f64 = 8.0;

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

fn merge_cell_deg(merge_m: f64, lat_deg: f64) -> f64 {
    meters_to_lon_deg(merge_m, lat_deg)
}

fn simplify_epsilon_deg(line: &[[f64; 2]], zoom: f64) -> f64 {
    let lat = line.iter().map(|c| c[1]).sum::<f64>() / line.len().max(1) as f64;
    let simplify_m = SIMPLIFY_PX * meters_per_pixel(zoom, lat);
    meters_to_lon_deg(simplify_m, lat)
}

/// `(merge cell degrees, minimum length meters, run Douglas–Peucker)`.
fn drive_generalization(zoom: f64, center_lat: f64) -> (Option<f64>, f64, bool) {
    if zoom >= 14.0 {
        (None, 0.0, false)
    } else {
        let mpp = meters_per_pixel(zoom, center_lat);
        (
            Some(merge_cell_deg(MERGE_PX * mpp, center_lat)),
            MIN_DRAW_PX * mpp,
            true,
        )
    }
}

fn snap_deg(v: f64, grid: f64) -> f64 {
    if grid <= 0.0 {
        return v;
    }
    (v / grid).round() * grid
}

#[derive(Copy, Clone, Eq, PartialEq)]
enum ZoomTier {
    Full,
    Medium,
    Coarse,
}

fn zoom_tier(z: f64) -> ZoomTier {
    if z >= 12.0 {
        ZoomTier::Full
    } else if z >= 9.0 {
        ZoomTier::Medium
    } else {
        ZoomTier::Coarse
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

#[derive(Copy, Clone, Eq, PartialEq)]
pub enum NetworkLayer {
    Paved,
    Jeep,
    Hiking,
}

impl NetworkLayer {
    pub fn parse(s: &str) -> Option<Self> {
        match s {
            "paved" => Some(Self::Paved),
            "jeep" => Some(Self::Jeep),
            "hiking" => Some(Self::Hiking),
            _ => None,
        }
    }
}

#[derive(Copy, Clone, Eq, PartialEq)]
enum DriveRole {
    Paved,
    Jeep,
}

fn undir_key(a: u32, b: u32) -> (u32, u32) {
    if a < b {
        (a, b)
    } else {
        (b, a)
    }
}

/// Paved matches the route finder's paved anchors. Every other drive-graph edge is jeep.
fn overlay_drive_role(edge: &GraphEdge, paved_tags: &HashSet<&str>) -> DriveRole {
    if graph_edge_is_paved_anchor(edge, paved_tags) {
        DriveRole::Paved
    } else {
        DriveRole::Jeep
    }
}

fn edge_in_view(graph: &JeepRoadGraph, a: u32, b: u32, view: NetworkViewBBox) -> bool {
    let (lat1, lon1) = graph.node_coords(a);
    let (lat2, lon2) = graph.node_coords(b);
    view.contains(lat1, lon1) || view.contains(lat2, lon2)
}

fn collect_drive_edges(
    graph: &JeepRoadGraph,
    paved_tags: &HashSet<&str>,
    view: NetworkViewBBox,
) -> HashMap<(u32, u32), DriveRole> {
    let mut out = HashMap::new();
    for from in 0..graph.node_count() as u32 {
        for edge in graph.edges(from) {
            let to = edge.to;
            if from >= to || !edge_in_view(graph, from, to, view) {
                continue;
            }
            let role = overlay_drive_role(edge, paved_tags);
            out.insert((from, to), role);
        }
    }
    out
}

fn collect_all_undirected_edges(
    graph: &JeepRoadGraph,
    view: NetworkViewBBox,
) -> HashSet<(u32, u32)> {
    let mut out = HashSet::new();
    for from in 0..graph.node_count() as u32 {
        for edge in graph.edges(from) {
            let to = edge.to;
            if edge_in_view(graph, from, to, view) {
                out.insert(undir_key(from, to));
            }
        }
    }
    out
}

fn filter_drive_edges_for_zoom(
    graph: &JeepRoadGraph,
    edges: &HashMap<(u32, u32), DriveRole>,
    role: DriveRole,
    zoom: f64,
) -> HashSet<(u32, u32)> {
    edges
        .iter()
        .filter_map(|((a, b), &r)| {
            if r != role {
                return None;
            }
            if role == DriveRole::Paved
                && !paved_visible_at_zoom(edge_highway(graph, *a, *b), zoom)
            {
                None
            } else {
                Some((*a, *b))
            }
        })
        .collect()
}

fn filter_trail_edges_for_zoom(
    _graph: &JeepRoadGraph,
    edges: HashSet<(u32, u32)>,
    tier: ZoomTier,
) -> HashSet<(u32, u32)> {
    match tier {
        ZoomTier::Coarse => HashSet::new(),
        ZoomTier::Full | ZoomTier::Medium => edges,
    }
}

fn polyline_length_m(coords: &[[f64; 2]]) -> f64 {
    coords
        .windows(2)
        .map(|w| haversine_m(w[0][1], w[0][0], w[1][1], w[1][0]))
        .sum()
}

fn corridor_key(line: &[[f64; 2]], grid: f64) -> Vec<(i32, i32)> {
    let mut cells = Vec::new();
    for c in line {
        let cell = ((c[0] / grid).round() as i32, (c[1] / grid).round() as i32);
        if cells.last() != Some(&cell) {
            cells.push(cell);
        }
    }
    if cells.len() >= 2 {
        let rev: Vec<(i32, i32)> = cells.iter().rev().copied().collect();
        if rev < cells {
            cells = rev;
        }
    }
    cells
}

fn point_line_dist_deg(p: [f64; 2], a: [f64; 2], b: [f64; 2]) -> f64 {
    let scale = a[1].to_radians().cos().abs().max(0.2);
    let (ax, ay) = (a[0] * scale, a[1]);
    let (bx, by) = (b[0] * scale, b[1]);
    let (px, py) = (p[0] * scale, p[1]);
    let (dx, dy) = (bx - ax, by - ay);
    let len2 = dx * dx + dy * dy;
    if len2 < 1e-20 {
        return ((px - ax).powi(2) + (py - ay).powi(2)).sqrt();
    }
    let t = (((px - ax) * dx + (py - ay) * dy) / len2).clamp(0.0, 1.0);
    let (qx, qy) = (ax + t * dx, ay + t * dy);
    ((px - qx).powi(2) + (py - qy).powi(2)).sqrt()
}

fn douglas_peucker(pts: &[[f64; 2]], epsilon_deg: f64) -> Vec<[f64; 2]> {
    if pts.len() < 3 || epsilon_deg <= 0.0 {
        return pts.to_vec();
    }
    let mut keep = vec![false; pts.len()];
    keep[0] = true;
    keep[pts.len() - 1] = true;
    fn mark(pts: &[[f64; 2]], i: usize, j: usize, eps: f64, keep: &mut [bool]) {
        let mut max_d = 0.0;
        let mut max_i = i;
        for k in i + 1..j {
            let d = point_line_dist_deg(pts[k], pts[i], pts[j]);
            if d > max_d {
                max_d = d;
                max_i = k;
            }
        }
        if max_d > eps {
            keep[max_i] = true;
            mark(pts, i, max_i, eps, keep);
            mark(pts, max_i, j, eps, keep);
        }
    }
    mark(pts, 0, pts.len() - 1, epsilon_deg, &mut keep);
    pts.iter()
        .enumerate()
        .filter(|(i, _)| keep[*i])
        .map(|(_, p)| *p)
        .collect()
}

/// Grid cells group nearby tracks. The drawn line is the longest original polyline, then simplified.
fn generalize_lines(
    lines: Vec<Vec<[f64; 2]>>,
    merge_cell: Option<f64>,
    min_len_m: f64,
    zoom: f64,
    simplify: bool,
) -> Vec<Vec<[f64; 2]>> {
    let finish = |line: Vec<[f64; 2]>| -> Option<Vec<[f64; 2]>> {
        if line.len() < 2 {
            return None;
        }
        let out = if simplify {
            douglas_peucker(&line, simplify_epsilon_deg(&line, zoom))
        } else {
            line
        };
        (out.len() >= 2).then_some(out)
    };

    let Some(grid) = merge_cell else {
        return lines
            .into_iter()
            .filter(|line| line.len() >= 2 && polyline_length_m(line) + 1.0 >= min_len_m)
            .filter_map(finish)
            .collect();
    };
    let mut best: HashMap<Vec<(i32, i32)>, (f64, Vec<[f64; 2]>)> = HashMap::new();
    for line in lines {
        if line.len() < 2 {
            continue;
        }
        let len = polyline_length_m(&line);
        if len + 1.0 < min_len_m {
            continue;
        }
        let key = corridor_key(&line, grid);
        if key.len() < 2 {
            continue;
        }
        match best.get_mut(&key) {
            Some(slot) if slot.0 >= len => {}
            Some(slot) => *slot = (len, line),
            None => {
                best.insert(key, (len, line));
            }
        }
    }
    best.into_values().filter_map(|(_, line)| finish(line)).collect()
}

fn chain_edges(
    graph: &JeepRoadGraph,
    edges: HashSet<(u32, u32)>,
    grid_deg: Option<f64>,
) -> Vec<Vec<[f64; 2]>> {
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

        loop {
            let cur = path[0];
            let next = path[1];
            let Some(neighbors) = adj.get(&cur) else {
                break;
            };
            if neighbors.len() != 2 {
                break;
            }
            let other = match neighbors.iter().find(|&&n| n != next) {
                Some(&n) => n,
                None => break,
            };
            let ek = undir_key(cur, other);
            if visited.contains(&ek) {
                break;
            }
            visited.insert(ek);
            path.insert(0, other);
        }

        loop {
            let len = path.len();
            let cur = path[len - 1];
            let prev = path[len - 2];
            let Some(neighbors) = adj.get(&cur) else {
                break;
            };
            if neighbors.len() != 2 {
                break;
            }
            let other = match neighbors.iter().find(|&&n| n != prev) {
                Some(&n) => n,
                None => break,
            };
            let ek = undir_key(cur, other);
            if visited.contains(&ek) {
                break;
            }
            visited.insert(ek);
            path.push(other);
        }

        let mut coords: Vec<[f64; 2]> = path
            .iter()
            .map(|&n| {
                let (lat, lon) = graph.node_coords(n);
                if let Some(grid) = grid_deg {
                    [round4(snap_deg(lon, grid)), round4(snap_deg(lat, grid))]
                } else {
                    [round4(lon), round4(lat)]
                }
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

fn round4(v: f64) -> f64 {
    (v * 10_000.0).round() / 10_000.0
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
        .map(|coords| {
            coords
                .into_iter()
                .map(|c| vec![round4(c[0]), round4(c[1])])
                .collect()
        })
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
    edges: &HashMap<(u32, u32), DriveRole>,
    role: DriveRole,
    zoom: f64,
    view: NetworkViewBBox,
) -> FeatureCollection {
    let center_lat = (view.south + view.north) * 0.5;
    let set = filter_drive_edges_for_zoom(graph, edges, role, zoom);
    let chained = chain_edges(graph, set, None);
    let (merge, min_m, simplify) = drive_generalization(zoom, center_lat);
    lines_to_fc(generalize_lines(
        chained, merge, min_m, zoom, simplify,
    ))
}

fn hiking_fc(
    trails: &TrailRouting,
    view: NetworkViewBBox,
    zoom: f64,
    tier: ZoomTier,
) -> FeatureCollection {
    if zoom < 11.0 {
        return empty_fc();
    }
    let edges = collect_all_undirected_edges(&trails.graph, view);
    let filtered = filter_trail_edges_for_zoom(&trails.graph, edges, tier);
    let chained = chain_edges(&trails.graph, filtered, None);
    let center_lat = (view.south + view.north) * 0.5;
    let (merge, min_m, simplify) = match tier {
        ZoomTier::Full => (None, 0.0, false),
        ZoomTier::Medium => drive_generalization(zoom, center_lat),
        ZoomTier::Coarse => (None, 0.0, false),
    };
    lines_to_fc(generalize_lines(chained, merge, min_m, zoom, simplify))
}

/// One access-network layer clipped to the map viewport.
pub fn routing_network_layer_geojson(
    routing: &ProjectRouting,
    opts: &OsmRoutingOpts,
    layer: NetworkLayer,
    view: NetworkViewBBox,
    zoom: f64,
) -> FeatureCollection {
    let tier = zoom_tier(zoom);
    match layer {
        NetworkLayer::Hiking => {
            let empty_trails = TrailRouting::empty();
            hiking_fc(
                routing.trails_opt().unwrap_or(&empty_trails),
                view,
                zoom,
                tier,
            )
        }
        NetworkLayer::Paved | NetworkLayer::Jeep => {
            let paved_tags: HashSet<&str> = opts.paved_highways.iter().map(String::as_str).collect();
            let graph = &routing.osm().graph;
            let drive_edges = collect_drive_edges(graph, &paved_tags, view);
            let role = if layer == NetworkLayer::Paved {
                DriveRole::Paved
            } else {
                DriveRole::Jeep
            };
            drive_role_fc(graph, &drive_edges, role, zoom, view)
        }
    }
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
    OsmRoutingOpts::from(&preset.resolved_access_meta())
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

    fn wide_view() -> NetworkViewBBox {
        NetworkViewBBox {
            west: -120.0,
            south: 38.0,
            east: -118.0,
            north: 40.0,
        }
    }

    fn jeep_lines(graph: &JeepRoadGraph, zoom: f64) -> Vec<Vec<[f64; 2]>> {
        let view = wide_view();
        let center_lat = (view.south + view.north) * 0.5;
        let all = collect_drive_edges(graph, &HashSet::new(), view);
        let set = filter_drive_edges_for_zoom(graph, &all, DriveRole::Jeep, zoom);
        let chained = chain_edges(graph, set, None);
        let (merge, min_m, simplify) = drive_generalization(zoom, center_lat);
        generalize_lines(chained, merge, min_m, zoom, simplify)
    }

    fn add_track_run(graph: &mut JeepRoadGraph, steps: usize, dlat: f64, lon: f64, mut id: i64) {
        let mut map = HashMap::new();
        let mut prev: Option<(u32, f64)> = None;
        let mut lat = 39.0;
        for _ in 0..=steps {
            let n = graph.get_or_insert_node_public(id, lat, lon, &mut map);
            id -= 1;
            if let Some((pn, plat)) = prev {
                graph.add_way_segment_public(pn, n, plat, lon, lat, lon, "track", None, None, false);
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
        graph.add_way_segment_public(n0, n1, 39.0, -119.0, 39.01, -119.0, "primary", None, None, false);
        graph.add_way_segment_public(n1, n2, 39.01, -119.0, 39.02, -119.0, "primary", None, None, false);
        let paved_tags: HashSet<&str> = ["primary"].iter().copied().collect();
        let view = NetworkViewBBox {
            west: -120.0,
            south: 38.0,
            east: -118.0,
            north: 40.0,
        };
        let edges = collect_drive_edges(&graph, &paved_tags, view);
        assert_eq!(edges.len(), 2);
        let lines = chain_edges(&graph, edges.keys().copied().collect(), None);
        assert_eq!(lines.len(), 1);
        assert_eq!(lines[0].len(), 3);
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
    fn low_zoom_merges_parallel_tracks() {
        let mut graph = JeepRoadGraph::new();
        add_track_run(&mut graph, 20, 0.01, -119.0, -1);
        add_track_run(&mut graph, 20, 0.01, -119.005, -10_000);
        let lines = jeep_lines(&graph, 8.0);
        assert_eq!(lines.len(), 1);
        let lon = lines[0][0][0];
        assert!(
            (lon + 119.0).abs() < 0.001 || (lon + 119.005).abs() < 0.001,
            "line followed the snap grid at {lon}"
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
        let all = collect_drive_edges(&graph, &HashSet::new(), wide_view());
        assert!(all.values().all(|r| *r == DriveRole::Jeep));
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
        let all = collect_drive_edges(&graph, &HashSet::new(), wide_view());
        assert_eq!(all.len(), 2);
        assert!(all.values().all(|r| *r == DriveRole::Jeep));
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
        let all = collect_drive_edges(&graph, &HashSet::new(), wide_view());
        assert!(all.values().all(|r| *r == DriveRole::Paved));
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
        let view = wide_view();
        let all = collect_drive_edges(&graph, &HashSet::new(), view);
        assert!(all.values().all(|r| *r == DriveRole::Jeep));
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
        let all = collect_drive_edges(&graph, &HashSet::new(), wide_view());
        assert_eq!(all.len(), 2);
        assert!(all.values().all(|r| *r == DriveRole::Jeep));
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
        let all = collect_drive_edges(&graph, &HashSet::new(), wide_view());
        assert!(all.values().all(|r| *r == DriveRole::Jeep));
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
        let paved_tags: HashSet<&str> = ["primary"].iter().copied().collect();
        let view = NetworkViewBBox {
            west: -120.0,
            south: 38.0,
            east: -118.0,
            north: 40.0,
        };
        let all = collect_drive_edges(&graph, &paved_tags, view);
        let paved = filter_drive_edges_for_zoom(&graph, &all, DriveRole::Paved, 8.0);
        assert_eq!(paved.len(), 1);
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
        let tags: HashSet<&str> = ["residential"].into_iter().collect();
        let all = collect_drive_edges(&graph, &tags, wide_view());
        assert!(all.values().all(|r| *r == DriveRole::Paved));
        assert!(filter_drive_edges_for_zoom(&graph, &all, DriveRole::Paved, 11.0).is_empty());
        assert_eq!(
            filter_drive_edges_for_zoom(&graph, &all, DriveRole::Paved, 14.0).len(),
            1
        );
    }

    #[test]
    fn grid_snap_merges_collinear_segments() {
        let mut graph = JeepRoadGraph::new();
        let mut map = HashMap::new();
        let mut id = -1i64;
        let n0 = graph.get_or_insert_node_public(id, 39.0, -119.0, &mut map);
        id -= 1;
        let n1 = graph.get_or_insert_node_public(id, 39.001, -119.0, &mut map);
        id -= 1;
        let n2 = graph.get_or_insert_node_public(id, 39.002, -119.0, &mut map);
        graph.add_way_segment_public(n0, n1, 39.0, -119.0, 39.001, -119.0, "primary", None, None, false);
        graph.add_way_segment_public(n1, n2, 39.001, -119.0, 39.002, -119.0, "primary", None, None, false);
        let edges: HashSet<_> = [(n0, n1), (n1, n2)].into_iter().collect();
        let lines = chain_edges(&graph, edges, Some(0.002));
        assert_eq!(lines.len(), 1);
        assert!(lines[0].len() >= 2);
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
        let paved_tags: HashSet<&str> = ["primary"].iter().copied().collect();
        let view = NetworkViewBBox {
            west: -120.0,
            south: 38.0,
            east: -118.0,
            north: 40.0,
        };
        let edges = collect_drive_edges(&graph, &paved_tags, view);
        assert_eq!(edges.len(), 1);
    }
}
