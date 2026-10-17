//! Spatial grid index for routing-graph edges (access-network overlays).

use std::collections::{HashMap, HashSet};

use crate::osm::JeepRoadGraph;

/// Degree cell size for edge bucketing (~5 km latitude).
pub const NETWORK_INDEX_CELL_DEG: f64 = 0.05;

fn cell_for(lon: f64, lat: f64) -> (i32, i32) {
    (
        (lon / NETWORK_INDEX_CELL_DEG).floor() as i32,
        (lat / NETWORK_INDEX_CELL_DEG).floor() as i32,
    )
}

fn cell_range(west: f64, south: f64, east: f64, north: f64) -> (i32, i32, i32, i32) {
    let i_w = (west / NETWORK_INDEX_CELL_DEG).floor() as i32;
    let i_s = (south / NETWORK_INDEX_CELL_DEG).floor() as i32;
    let i_e = (east / NETWORK_INDEX_CELL_DEG).floor() as i32;
    let i_n = (north / NETWORK_INDEX_CELL_DEG).floor() as i32;
    (i_w, i_s, i_e, i_n)
}

fn undir_key(a: u32, b: u32) -> (u32, u32) {
    if a < b {
        (a, b)
    } else {
        (b, a)
    }
}

/// Undirected edges bucketed by endpoint cells (built once per graph).
pub struct GraphEdgeIndex {
    cells: HashMap<(i32, i32), Vec<(u32, u32)>>,
}

impl GraphEdgeIndex {
    pub fn build(graph: &JeepRoadGraph) -> Self {
        let mut cells: HashMap<(i32, i32), Vec<(u32, u32)>> = HashMap::new();
        for from in 0..graph.node_count() as u32 {
            for edge in graph.edges(from) {
                let to = edge.to;
                if from >= to {
                    continue;
                }
                let key = undir_key(from, to);
                let (lat1, lon1) = graph.node_coords(from);
                let (lat2, lon2) = graph.node_coords(to);
                let c1 = cell_for(lon1, lat1);
                let c2 = cell_for(lon2, lat2);
                cells.entry(c1).or_default().push(key);
                if c2 != c1 {
                    cells.entry(c2).or_default().push(key);
                }
            }
        }
        Self { cells }
    }

    /// Candidate undirected edges for a lon/lat bbox (deduped).
    pub fn undirected_edges_in_bbox(
        &self,
        west: f64,
        south: f64,
        east: f64,
        north: f64,
    ) -> HashSet<(u32, u32)> {
        let (i_w, i_s, i_e, i_n) = cell_range(west, south, east, north);
        let mut out = HashSet::new();
        for ix in i_w..=i_e {
            for iy in i_s..=i_n {
                if let Some(edges) = self.cells.get(&(ix, iy)) {
                    for &ek in edges {
                        out.insert(ek);
                    }
                }
            }
        }
        out
    }
}

/// Snapped viewport key for server-side GeoJSON byte cache.
#[derive(Clone, Copy, Eq, PartialEq, Hash, Debug)]
pub struct NetworkViewCacheKey {
    pub zoom_tier: i16,
    pub cell_w: i32,
    pub cell_s: i32,
    pub cell_e: i32,
    pub cell_n: i32,
}

impl NetworkViewCacheKey {
    pub fn from_view(west: f64, south: f64, east: f64, north: f64, zoom: f64) -> Self {
        let (i_w, i_s, i_e, i_n) = cell_range(west, south, east, north);
        Self {
            zoom_tier: zoom.round() as i16,
            cell_w: i_w,
            cell_s: i_s,
            cell_e: i_e,
            cell_n: i_n,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    #[test]
    fn index_matches_full_scan_on_tiny_graph() {
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

        let index = GraphEdgeIndex::build(&graph);
        let set = index.undirected_edges_in_bbox(-120.0, 38.0, -118.0, 40.0);
        assert_eq!(set.len(), 2);

        let narrow = index.undirected_edges_in_bbox(-119.01, 39.0, -118.99, 39.005);
        assert!(narrow.len() <= 2);
        assert!(narrow.contains(&undir_key(n0, n1)));
    }
}
