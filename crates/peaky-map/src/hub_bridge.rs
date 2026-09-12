//! Hub-bridge progress: shortest remaining gap between anchor hubs over coverage polygons.

use geo::algorithm::{ClosestPoint, Simplify};
use geo::{Area, Centroid, Closest, MultiPolygon, Point};
use serde_json::{json, Value};

const EARTH_R_M: f64 = 6_371_008.8;
const MAX_GRAPH_COMPONENTS: usize = 64;
const SIMPLIFY_TOL_DEG: f64 = 0.001;

/// WGS84 hub anchors and nominal legs for progress scoring.
#[derive(Debug, Clone)]
pub struct HubBridgeConfig {
    /// Hub positions as `[lng, lat]`.
    pub vertices: Vec<[f64; 2]>,
    /// Stable hub ids, same order as `vertices`.
    pub hub_ids: Vec<String>,
    /// Undirected hub index pairs to score (e.g. triangle legs).
    pub link_pairs: Vec<(usize, usize)>,
}

impl HubBridgeConfig {
    pub fn bbox(&self, pad_deg: f64) -> [f64; 4] {
        bbox_around_vertices(&self.vertices, pad_deg)
    }
}

pub fn bbox_around_vertices(vertices: &[[f64; 2]], pad_deg: f64) -> [f64; 4] {
    let lngs: Vec<f64> = vertices.iter().map(|v| v[0]).collect();
    let lats: Vec<f64> = vertices.iter().map(|v| v[1]).collect();
    [
        lngs.iter().copied().fold(f64::INFINITY, f64::min) - pad_deg,
        lats.iter().copied().fold(f64::INFINITY, f64::min) - pad_deg,
        lngs.iter().copied().fold(f64::NEG_INFINITY, f64::max) + pad_deg,
        lats.iter().copied().fold(f64::NEG_INFINITY, f64::max) + pad_deg,
    ]
}

pub fn haversine_meters(a: (f64, f64), b: (f64, f64)) -> f64 {
    let (lng1, lat1) = a;
    let (lng2, lat2) = b;
    let r1 = lat1.to_radians();
    let r2 = lat2.to_radians();
    let d_lng = (lng2 - lng1).to_radians();
    let d_lat = r2 - r1;
    let s = (d_lat / 2.0).sin().powi(2) + r1.cos() * r2.cos() * (d_lng / 2.0).sin().powi(2);
    2.0 * EARTH_R_M * s.sqrt().min(1.0).asin()
}

fn lat_lng(p: Point<f64>) -> [f64; 2] {
    [p.y(), p.x()]
}

fn lng_lat(p: Point<f64>) -> (f64, f64) {
    (p.x(), p.y())
}

fn coverage_components(geoms: &[MultiPolygon<f64>]) -> Vec<MultiPolygon<f64>> {
    if geoms.is_empty() {
        return Vec::new();
    }
    let mut merged: Option<MultiPolygon<f64>> = None;
    for g in geoms {
        merged = Some(match merged {
            None => g.clone(),
            Some(m) => {
                let mut polys = m.0.clone();
                polys.extend(g.0.clone());
                MultiPolygon(polys)
            }
        });
    }
    let Some(merged) = merged else {
        return Vec::new();
    };
    merged
        .0
        .into_iter()
        .filter(|p| p.unsigned_area() > 0.0)
        .map(|p| MultiPolygon(vec![p]))
        .collect()
}

enum GraphNode {
    Coverage(MultiPolygon<f64>),
    Hub(Point<f64>),
}

fn simplified_for_distance(g: &MultiPolygon<f64>) -> MultiPolygon<f64> {
    let simplified: MultiPolygon<f64> = g.simplify(&SIMPLIFY_TOL_DEG);
    if simplified.0.is_empty() {
        g.clone()
    } else {
        simplified
    }
}

fn graph_nodes(
    components: &[MultiPolygon<f64>],
    config: &HubBridgeConfig,
) -> (Vec<GraphNode>, Vec<usize>) {
    let mut order: Vec<usize> = (0..components.len()).collect();
    order.sort_by(|a, b| {
        components[*b]
            .unsigned_area()
            .partial_cmp(&components[*a].unsigned_area())
            .unwrap_or(std::cmp::Ordering::Equal)
    });

    let mut nodes: Vec<GraphNode> = order
        .into_iter()
        .take(MAX_GRAPH_COMPONENTS)
        .map(|i| GraphNode::Coverage(simplified_for_distance(&components[i])))
        .collect();

    let mut hub_nodes = Vec::with_capacity(config.vertices.len());
    for vert in &config.vertices {
        hub_nodes.push(nodes.len());
        nodes.push(GraphNode::Hub(Point::new(vert[0], vert[1])));
    }
    (nodes, hub_nodes)
}

fn nearest_points_nodes(a: &GraphNode, b: &GraphNode) -> (Point<f64>, Point<f64>) {
    match (a, b) {
        (GraphNode::Hub(pa), GraphNode::Hub(pb)) => (*pa, *pb),
        (GraphNode::Hub(p), GraphNode::Coverage(m)) | (GraphNode::Coverage(m), GraphNode::Hub(p)) => {
            let pt = geo::Point::new(p.x(), p.y());
            match m.closest_point(&pt) {
                Closest::SinglePoint(cp) => {
                    if matches!(a, GraphNode::Hub(_)) {
                        (*p, cp)
                    } else {
                        (cp, *p)
                    }
                }
                _ => (*p, *p),
            }
        }
        (GraphNode::Coverage(ma), GraphNode::Coverage(mb)) => {
            nearest_points_multipolygons(ma, mb)
        }
    }
}

fn nearest_points_multipolygons(a: &MultiPolygon<f64>, b: &MultiPolygon<f64>) -> (Point<f64>, Point<f64>) {
    use geo::algorithm::Intersects;
    if a.intersects(b) {
        let ca = a.centroid().unwrap_or(Point::new(0.0, 0.0));
        let cb = b.centroid().unwrap_or(Point::new(0.0, 0.0));
        return (ca, cb);
    }

    let mut best_d = f64::INFINITY;
    let mut best = (Point::new(0.0, 0.0), Point::new(0.0, 0.0));

    for pa in sample_boundary_points(a) {
        let pt = Point::new(pa.0, pa.1);
        if let Closest::SinglePoint(cp) = b.closest_point(&pt) {
            let d = haversine_meters((pa.0, pa.1), (cp.x(), cp.y()));
            if d < best_d {
                best_d = d;
                best = (pt, cp);
            }
        }
    }
    for pb in sample_boundary_points(b) {
        let pt = Point::new(pb.0, pb.1);
        if let Closest::SinglePoint(cp) = a.closest_point(&pt) {
            let d = haversine_meters((pb.0, pb.1), (cp.x(), cp.y()));
            if d < best_d {
                best_d = d;
                best = (cp, pt);
            }
        }
    }
    best
}

fn sample_boundary_points(m: &MultiPolygon<f64>) -> Vec<(f64, f64)> {
    let mut pts = Vec::new();
    for poly in &m.0 {
        for coord in poly.exterior().0.iter() {
            pts.push((coord.x, coord.y));
        }
    }
    pts
}

fn edge_matrix(nodes: &[GraphNode]) -> (Vec<Vec<f64>>, Vec<(Point<f64>, Point<f64>)>) {
    let n = nodes.len();
    let mut gap = vec![vec![0.0; n]; n];
    let mut pts = Vec::new();

    for a in 0..n {
        for b in (a + 1)..n {
            let (pa, pb) = nearest_points_nodes(&nodes[a], &nodes[b]);
            let d = haversine_meters(lng_lat(pa), lng_lat(pb));
            gap[a][b] = d;
            gap[b][a] = d;
            pts.push((pa, pb));
        }
    }
    (gap, pts)
}

fn edge_endpoints(
    nodes: &[GraphNode],
    pts: &[(Point<f64>, Point<f64>)],
    u: usize,
    v: usize,
) -> (Point<f64>, Point<f64>) {
    if u > v {
        let (pb, pa) = edge_endpoints(nodes, pts, v, u);
        return (pa, pb);
    }
    let mut idx = 0usize;
    for a in 0..nodes.len() {
        for b in (a + 1)..nodes.len() {
            if a == u && b == v {
                return pts[idx];
            }
            idx += 1;
        }
    }
    (Point::new(0.0, 0.0), Point::new(0.0, 0.0))
}

fn dijkstra_path(gap: &[Vec<f64>], src: usize, dst: usize) -> (f64, Vec<(usize, usize)>) {
    let n = gap.len();
    let mut dist = vec![f64::INFINITY; n];
    let mut prev = vec![-1isize; n];
    let mut done = vec![false; n];
    dist[src] = 0.0;

    for _ in 0..n {
        let mut u = None;
        let mut best = f64::INFINITY;
        for k in 0..n {
            if !done[k] && dist[k] < best {
                best = dist[k];
                u = Some(k);
            }
        }
        let Some(u) = u else { break };
        if u == dst {
            break;
        }
        done[u] = true;
        for v in 0..n {
            if done[v] {
                continue;
            }
            let nd = dist[u] + gap[u][v];
            if nd < dist[v] {
                dist[v] = nd;
                prev[v] = u as isize;
            }
        }
    }

    let mut edges = Vec::new();
    let mut v = dst;
    while v != src && prev[v] >= 0 {
        let u = prev[v] as usize;
        edges.push((u, v));
        v = u;
    }
    edges.reverse();
    (dist[dst], edges)
}

pub fn compute_hub_bridge_progress(
    geoms: &[MultiPolygon<f64>],
    config: &HubBridgeConfig,
    base_step_meters: f64,
) -> Value {
    let verts: Vec<(f64, f64)> = config.vertices.iter().map(|v| (v[0], v[1])).collect();
    let components = coverage_components(geoms);
    let (nodes, hub_nodes) = graph_nodes(&components, config);
    let (gap, pts) = edge_matrix(&nodes);
    let connect_threshold_m = (base_step_meters / 2.0).max(1.0);

    let mut links = Vec::new();
    let mut segments = Vec::new();
    let mut seen_gap_segments: std::collections::HashSet<(i64, i64, i64, i64)> =
        std::collections::HashSet::new();
    let mut total_m = 0.0;
    let mut covered_m = 0.0;
    let mut gap_m = 0.0;
    let mut links_closed = 0i64;

    for &(i, j) in &config.link_pairs {
        let target_m = haversine_meters(verts[i], verts[j]);
        let (mut remaining_m, path_edges) = dijkstra_path(&gap, hub_nodes[i], hub_nodes[j]);
        let mut closed = remaining_m <= connect_threshold_m;
        if closed {
            remaining_m = 0.0;
            links_closed += 1;
        }
        let link_covered_m = (target_m - remaining_m).max(0.0).min(target_m);

        let mut positions: Vec<[f64; 2]> = Vec::new();
        for &(u, v) in &path_edges {
            let (pa, pb) = edge_endpoints(&nodes, &pts, u, v);
            positions.push(lat_lng(pa));
            positions.push(lat_lng(pb));
            let edge_gap = gap[u][v];
            if closed || edge_gap <= 0.0 {
                continue;
            }
            let key = (
                (pa.y() * 1e6) as i64,
                (pa.x() * 1e6) as i64,
                (pb.y() * 1e6) as i64,
                (pb.x() * 1e6) as i64,
            );
            let rev = (key.2, key.3, key.0, key.1);
            if seen_gap_segments.contains(&key) || seen_gap_segments.contains(&rev) {
                continue;
            }
            seen_gap_segments.insert(key);
            segments.push(json!({
                "kind": "gap",
                "positionsLatLng": [lat_lng(pa), lat_lng(pb)],
                "lengthM": edge_gap,
            }));
        }

        if closed && !positions.is_empty() {
            segments.push(json!({
                "kind": "covered",
                "positionsLatLng": positions,
                "lengthM": target_m,
            }));
        }

        links.push(json!({
            "from": config.hub_ids[i],
            "to": config.hub_ids[j],
            "closed": closed,
            "targetM": target_m,
            "remainingM": remaining_m,
            "coveredM": link_covered_m,
            "positionsLatLng": positions,
        }));

        total_m += target_m;
        covered_m += link_covered_m;
        gap_m += remaining_m;
    }

    let fraction = if total_m > 0.0 { covered_m / total_m } else { 0.0 };

    json!({
        "totalM": total_m,
        "coveredM": covered_m,
        "gapM": gap_m,
        "fractionCovered": fraction,
        "linksClosed": links_closed,
        "links": links,
        "segments": segments,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use geo::{Coord, LineString, Polygon};

    fn sample_triangle_config() -> HubBridgeConfig {
        HubBridgeConfig {
            vertices: vec![
                [-119.8138, 39.5296],
                [-114.9644, 41.1116],
                [-115.1398, 36.1699],
            ],
            hub_ids: vec!["a".into(), "b".into(), "c".into()],
            link_pairs: vec![(0, 1), (1, 2), (2, 0)],
        }
    }

    fn buffer_hub(lng: f64, lat: f64, size_deg: f64) -> MultiPolygon<f64> {
        let d = size_deg;
        let ring: LineString = vec![
            Coord { x: lng - d, y: lat - d },
            Coord { x: lng + d, y: lat - d },
            Coord { x: lng + d, y: lat + d },
            Coord { x: lng - d, y: lat + d },
            Coord { x: lng - d, y: lat - d },
        ]
        .into();
        MultiPolygon(vec![Polygon::new(ring, vec![])])
    }

    #[test]
    fn empty_coverage_all_links_open() {
        let cfg = sample_triangle_config();
        let st = compute_hub_bridge_progress(&[], &cfg, 400.0);
        assert_eq!(st["linksClosed"], 0);
        assert_eq!(st["links"].as_array().unwrap().len(), 3);
        assert!((st["coveredM"].as_f64().unwrap()).abs() < 1e-6);
    }

    #[test]
    fn target_matches_vertex_distance() {
        let cfg = sample_triangle_config();
        let st = compute_hub_bridge_progress(&[], &cfg, 400.0);
        let expected = haversine_meters(
            (cfg.vertices[0][0], cfg.vertices[0][1]),
            (cfg.vertices[1][0], cfg.vertices[1][1]),
        ) + haversine_meters(
            (cfg.vertices[1][0], cfg.vertices[1][1]),
            (cfg.vertices[2][0], cfg.vertices[2][1]),
        ) + haversine_meters(
            (cfg.vertices[2][0], cfg.vertices[2][1]),
            (cfg.vertices[0][0], cfg.vertices[0][1]),
        );
        assert!((st["totalM"].as_f64().unwrap() - expected).abs() < 1.0);
    }

    #[test]
    fn large_buffers_increase_covered_m() {
        let cfg = sample_triangle_config();
        let empty = compute_hub_bridge_progress(&[], &cfg, 400.0);
        let a = buffer_hub(cfg.vertices[0][0], cfg.vertices[0][1], 3.5);
        let b = buffer_hub(cfg.vertices[1][0], cfg.vertices[1][1], 3.5);
        let partial = compute_hub_bridge_progress(&[a, b], &cfg, 400.0);
        assert!(
            partial["coveredM"].as_f64().unwrap() > empty["coveredM"].as_f64().unwrap()
        );
    }
}
