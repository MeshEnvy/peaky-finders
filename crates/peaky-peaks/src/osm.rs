//! OSM jeep-class roads: R-tree index + routing graph + paved anchors.

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::time::Instant;

use anyhow::{Context, Result};
use osmpbf::{Element, ElementReader};
use rstar::{RTree, RTreeObject, AABB};
use tracing::info;

use crate::hike::haversine_m;

pub const JEEP_HIGHWAY_TAGS: &[&str] = &[
    "track",
    "unclassified",
    "service",
    "residential",
    "tertiary",
];

pub const PAVED_HIGHWAY_TAGS: &[&str] = &[
    "primary",
    "secondary",
    "tertiary",
    "trunk",
    "motorway",
    "unclassified",
    "residential",
];

/// Options for OSM jeep/paved indexing (from ``access/_meta.yaml``).
#[derive(Debug, Clone)]
pub struct OsmRoutingOpts {
    pub road_sample_step_m: f64,
    pub jeep_highways: Vec<String>,
    pub paved_highways: Vec<String>,
}

impl Default for OsmRoutingOpts {
    fn default() -> Self {
        Self {
            road_sample_step_m: 40.0,
            jeep_highways: JEEP_HIGHWAY_TAGS.iter().map(|s| (*s).to_string()).collect(),
            paved_highways: PAVED_HIGHWAY_TAGS
                .iter()
                .map(|s| (*s).to_string())
                .collect(),
        }
    }
}

impl From<&peaky_preset::AccessMeta> for OsmRoutingOpts {
    fn from(meta: &peaky_preset::AccessMeta) -> Self {
        Self {
            road_sample_step_m: meta.road_sample_step_m,
            jeep_highways: meta.road_highways.clone(),
            paved_highways: meta.paved_highways.clone(),
        }
    }
}

const GEOFABRIK_NV_URL: &str =
    "https://download.geofabrik.de/north-america/us/nevada-latest.osm.pbf";

#[derive(Debug, Clone, Copy)]
pub struct RoadPoint {
    pub lat: f64,
    pub lon: f64,
}

impl RTreeObject for RoadPoint {
    type Envelope = AABB<[f64; 2]>;

    fn envelope(&self) -> Self::Envelope {
        AABB::from_point([self.lon, self.lat])
    }
}

pub struct JeepRoadIndex {
    tree: RTree<RoadPoint>,
    pub point_count: usize,
    pub way_count: usize,
}

impl JeepRoadIndex {
    pub fn is_empty(&self) -> bool {
        self.point_count == 0
    }

    fn envelope_for_radius(lat: f64, lon: f64, max_m: f64) -> AABB<[f64; 2]> {
        let deg = (max_m / 111_000.0).max(0.002);
        AABB::from_corners([lon - deg, lat - deg], [lon + deg, lat + deg])
    }

    /// Jeep-road samples within ``max_m`` (haversine), unsorted.
    pub fn points_within(&self, lat: f64, lon: f64, max_m: f64) -> Vec<(f64, f64, f64)> {
        if self.point_count == 0 {
            return Vec::new();
        }
        let envelope = Self::envelope_for_radius(lat, lon, max_m);
        let mut out = Vec::new();
        for pt in self.tree.locate_in_envelope_intersecting(&envelope) {
            let d = haversine_m(lat, lon, pt.lat, pt.lon);
            if d <= max_m + 1.0 {
                out.push((pt.lat, pt.lon, d));
            }
        }
        out
    }

    /// Nearest jeep-road sample within ``max_m`` (haversine).
    pub fn nearest_within(&self, lat: f64, lon: f64, max_m: f64) -> Option<(f64, f64, f64)> {
        self.points_within(lat, lon, max_m)
            .into_iter()
            .min_by(|a, b| a.2.partial_cmp(&b.2).unwrap_or(std::cmp::Ordering::Equal))
    }
}

#[derive(Debug, Clone)]
pub struct GraphEdge {
    pub to: u32,
    pub length_m: f64,
    pub highway: String,
    pub tracktype: Option<String>,
    pub surface: Option<String>,
    pub fourwd_only: bool,
}

pub struct JeepRoadGraph {
    nodes: Vec<(f64, f64)>,
    adj: Vec<Vec<GraphEdge>>,
}

impl JeepRoadGraph {
    pub fn new() -> Self {
        Self {
            nodes: Vec::new(),
            adj: Vec::new(),
        }
    }

    pub fn is_empty(&self) -> bool {
        self.nodes.is_empty()
    }

    pub fn node_count(&self) -> usize {
        self.nodes.len()
    }

    pub fn node_coords(&self, idx: u32) -> (f64, f64) {
        self.nodes[idx as usize]
    }

    pub fn edges(&self, idx: u32) -> &[GraphEdge] {
        &self.adj[idx as usize]
    }

    pub fn insert_node(&mut self, lat: f64, lon: f64) -> u32 {
        let idx = self.nodes.len() as u32;
        self.nodes.push((lat, lon));
        self.adj.push(Vec::new());
        idx
    }

    pub fn add_edge(&mut self, from: u32, edge: GraphEdge) {
        self.adj[from as usize].push(edge);
    }

    pub fn nearest_node(&self, lat: f64, lon: f64, max_m: f64) -> Option<u32> {
        let mut best: Option<(u32, f64)> = None;
        for (idx, &(nlat, nlon)) in self.nodes.iter().enumerate() {
            let d = haversine_m(lat, lon, nlat, nlon);
            if d <= max_m + 1.0 && best.map(|(_, bd)| d < bd).unwrap_or(true) {
                best = Some((idx as u32, d));
            }
        }
        best.map(|(idx, _)| idx)
    }

    fn get_or_insert_node(
        &mut self,
        osm_id: i64,
        lat: f64,
        lon: f64,
        map: &mut HashMap<i64, u32>,
    ) -> u32 {
        if let Some(&idx) = map.get(&osm_id) {
            return idx;
        }
        let idx = self.insert_node(lat, lon);
        map.insert(osm_id, idx);
        idx
    }

    fn add_way_segment(
        &mut self,
        a: u32,
        b: u32,
        lat1: f64,
        lon1: f64,
        lat2: f64,
        lon2: f64,
        highway: &str,
        tracktype: Option<&str>,
        surface: Option<&str>,
        fourwd_only: bool,
    ) {
        let len = haversine_m(lat1, lon1, lat2, lon2);
        if len < 0.5 {
            return;
        }
        let tt = tracktype.map(str::to_string);
        let surf = surface.map(str::to_string);
        let hw = highway.to_string();
        self.add_edge(
            a,
            GraphEdge {
                to: b,
                length_m: len,
                highway: hw.clone(),
                tracktype: tt.clone(),
                surface: surf.clone(),
                fourwd_only,
            },
        );
        self.add_edge(
            b,
            GraphEdge {
                to: a,
                length_m: len,
                highway: hw,
                tracktype: tt,
                surface: surf,
                fourwd_only,
            },
        );
    }
}

#[derive(Debug, Clone, Copy)]
pub struct PavedPoint {
    pub lat: f64,
    pub lon: f64,
    pub node_idx: u32,
}

impl RTreeObject for PavedPoint {
    type Envelope = AABB<[f64; 2]>;

    fn envelope(&self) -> Self::Envelope {
        AABB::from_point([self.lon, self.lat])
    }
}

pub struct PavedAnchorIndex {
    tree: RTree<PavedPoint>,
}

impl PavedAnchorIndex {
    pub fn from_nodes(_graph: &JeepRoadGraph, nodes: &[(u32, f64, f64)]) -> Self {
        let points: Vec<PavedPoint> = nodes
            .iter()
            .map(|&(idx, lat, lon)| PavedPoint {
                lat,
                lon,
                node_idx: idx,
            })
            .collect();
        Self {
            tree: RTree::bulk_load(points),
        }
    }

    #[cfg(test)]
    pub(crate) fn test_from_points(points: &[(f64, f64)]) -> Self {
        let loaded: Vec<PavedPoint> = points
            .iter()
            .enumerate()
            .map(|(i, &(lat, lon))| PavedPoint {
                lat,
                lon,
                node_idx: i as u32,
            })
            .collect();
        Self {
            tree: RTree::bulk_load(loaded),
        }
    }

    pub fn points_within(&self, lat: f64, lon: f64, max_m: f64) -> Vec<(f64, f64, f64)> {
        self.sources_within(lat, lon, max_m)
            .into_iter()
            .map(|(plat, plon, _)| (plat, plon, haversine_m(lat, lon, plat, plon)))
            .collect()
    }

    pub fn nearest_within(&self, lat: f64, lon: f64, max_m: f64) -> Option<(f64, f64, f64)> {
        self.points_within(lat, lon, max_m)
            .into_iter()
            .min_by(|a, b| a.2.partial_cmp(&b.2).unwrap_or(std::cmp::Ordering::Equal))
    }

    pub fn sources_within(&self, lat: f64, lon: f64, max_m: f64) -> Vec<(f64, f64, u32)> {
        if self.tree.size() == 0 {
            return Vec::new();
        }
        let deg = (max_m / 111_000.0).max(0.002);
        let envelope = AABB::from_corners([lon - deg, lat - deg], [lon + deg, lat + deg]);
        let mut out = Vec::new();
        let mut seen = HashSet::new();
        for pt in self.tree.locate_in_envelope_intersecting(&envelope) {
            if haversine_m(lat, lon, pt.lat, pt.lon) > max_m + 1.0 {
                continue;
            }
            if seen.insert(pt.node_idx) {
                out.push((pt.lat, pt.lon, pt.node_idx));
            }
        }
        out
    }
}

pub struct OsmRouting {
    pub jeep_roads: JeepRoadIndex,
    pub graph: JeepRoadGraph,
    pub paved: PavedAnchorIndex,
}

pub fn osm_cache_path(project_dir: &Path) -> PathBuf {
    project_dir.join(".peaky/cache/osm/nevada-latest.osm.pbf")
}

pub fn ensure_osm_pbf(project_dir: &Path, force: bool) -> Result<PathBuf> {
    let path = osm_cache_path(project_dir);
    if path.is_file() && !force {
        info!(
            path = %path.display(),
            bytes = std::fs::metadata(&path).map(|m| m.len()).unwrap_or(0),
            "peaks: OSM cache hit"
        );
        return Ok(path);
    }
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).context("create osm cache dir")?;
    }
    info!(url = GEOFABRIK_NV_URL, "peaks: downloading OSM PBF…");
    let t0 = Instant::now();
    let client = reqwest::blocking::Client::builder()
        .timeout(std::time::Duration::from_secs(600))
        .build()
        .context("build HTTP client")?;
    let bytes = client
        .get(GEOFABRIK_NV_URL)
        .send()
        .context("download OSM PBF")?
        .error_for_status()
        .context("OSM PBF HTTP status")?
        .bytes()
        .context("read OSM PBF body")?;
    std::fs::write(&path, &bytes).context("write OSM PBF cache")?;
    info!(
        path = %path.display(),
        bytes = bytes.len(),
        elapsed_secs = t0.elapsed().as_secs_f64(),
        "peaks: OSM download done"
    );
    Ok(path)
}

#[derive(Debug, Clone, Copy)]
struct WayTags<'a> {
    highway: &'a str,
    tracktype: Option<&'a str>,
    surface: Option<&'a str>,
    fourwd_only: bool,
}

fn parse_way_tags<'a, I>(tags: I) -> Option<WayTags<'a>>
where
    I: IntoIterator<Item = (&'a str, &'a str)>,
{
    let mut highway: Option<&str> = None;
    let mut tracktype: Option<&str> = None;
    let mut surface: Option<&str> = None;
    let mut fourwd_only = false;
    for (k, v) in tags {
        match k {
            "highway" => highway = Some(v),
            "tracktype" => tracktype = Some(v),
            "surface" => surface = Some(v),
            "4wd_only" if v == "yes" => fourwd_only = true,
            _ => {}
        }
    }
    highway.map(|hw| WayTags {
        highway: hw,
        tracktype,
        surface,
        fourwd_only,
    })
}

fn is_jeep_highway(tags: WayTags<'_>, jeep_tags: &HashSet<&str>) -> bool {
    if !jeep_tags.contains(tags.highway) {
        return false;
    }
    if tags.highway == "track" {
        if tags.fourwd_only {
            return true;
        }
        if let Some(tt) = tags.tracktype {
            if matches!(tt, "grade1" | "grade2") {
                return false;
            }
        }
        if tags.surface == Some("paved") {
            return false;
        }
    }
    true
}

fn is_paved_highway(tags: WayTags<'_>, paved_tags: &HashSet<&str>) -> bool {
    paved_tags.contains(tags.highway)
}

fn is_paved_surface(surface: Option<&str>) -> bool {
    matches!(
        surface,
        Some("paved") | Some("asphalt") | Some("concrete") | Some("chipseal") | Some("tarmac")
    )
}

fn is_unpaved_surface(surface: Option<&str>) -> bool {
    matches!(
        surface,
        Some("unpaved")
            | Some("gravel")
            | Some("fine_gravel")
            | Some("compacted")
            | Some("dirt")
            | Some("ground")
            | Some("sand")
            | Some("mud")
            | Some("grass")
            | Some("earth")
    )
}

/// Paved Dijkstra sources only — not every jeep-class road.
fn is_paved_anchor(tags: WayTags<'_>, paved_tags: &HashSet<&str>) -> bool {
    if matches!(
        tags.highway,
        "track" | "footway" | "path" | "steps" | "service" | "bridleway"
    ) {
        return false;
    }
    if is_unpaved_surface(tags.surface) {
        return false;
    }
    if is_paved_surface(tags.surface) {
        return is_paved_highway(tags, paved_tags)
            || matches!(tags.highway, "tertiary" | "tertiary_link");
    }
    matches!(
        tags.highway,
        "motorway"
            | "trunk"
            | "primary"
            | "secondary"
            | "motorway_link"
            | "trunk_link"
            | "primary_link"
            | "secondary_link"
    )
}

fn sample_way(nodes: &[(f64, f64)], step_m: f64) -> Vec<RoadPoint> {
    if nodes.len() < 2 {
        return nodes
            .iter()
            .map(|(lat, lon)| RoadPoint {
                lat: *lat,
                lon: *lon,
            })
            .collect();
    }
    let mut out = Vec::new();
    for w in nodes.windows(2) {
        let (lat1, lon1) = w[0];
        let (lat2, lon2) = w[1];
        let seg_len = haversine_m(lat1, lon1, lat2, lon2);
        let n = ((seg_len / step_m).ceil() as usize).max(1);
        for i in 0..=n {
            let t = (i as f64) / (n as f64);
            out.push(RoadPoint {
                lat: lat1 + t * (lat2 - lat1),
                lon: lon1 + t * (lon2 - lon1),
            });
        }
    }
    out
}

/// Distance from a point to a segment (m), using linear lat/lon projection.
fn dist_point_to_segment_m(lat: f64, lon: f64, lat1: f64, lon1: f64, lat2: f64, lon2: f64) -> f64 {
    let seg_len = haversine_m(lat1, lon1, lat2, lon2);
    if seg_len < 0.5 {
        return haversine_m(lat, lon, lat1, lon1);
    }
    let cos = lat1.to_radians().cos().abs().max(1e-6);
    let x1 = lon1 * cos;
    let y1 = lat1;
    let x2 = lon2 * cos;
    let y2 = lat2;
    let x = lon * cos;
    let y = lat;
    let dx = x2 - x1;
    let dy = y2 - y1;
    let len2 = dx * dx + dy * dy;
    if len2 < 1e-18 {
        return haversine_m(lat, lon, lat1, lon1);
    }
    let t = ((x - x1) * dx + (y - y1) * dy) / len2;
    let t = t.clamp(0.0, 1.0);
    let proj_lat = lat1 + t * (lat2 - lat1);
    let proj_lon = lon1 + t * (lon2 - lon1);
    haversine_m(lat, lon, proj_lat, proj_lon)
}

/// Interpolated paved samples for proximity snap; each ties to the nearer graph endpoint.
fn paved_interpolated_samples(
    coords: &[(f64, f64)],
    vert_nodes: &[u32],
    sample_step: f64,
) -> Vec<PavedPoint> {
    if coords.len() < 2 || vert_nodes.len() != coords.len() {
        return Vec::new();
    }
    sample_way(coords, sample_step)
        .into_iter()
        .map(|pt| {
            let mut best_seg = 0usize;
            let mut best_d = f64::INFINITY;
            for i in 0..coords.len() - 1 {
                let d = dist_point_to_segment_m(
                    pt.lat,
                    pt.lon,
                    coords[i].0,
                    coords[i].1,
                    coords[i + 1].0,
                    coords[i + 1].1,
                );
                if d < best_d {
                    best_d = d;
                    best_seg = i;
                }
            }
            let (lat1, lon1) = coords[best_seg];
            let (lat2, lon2) = coords[best_seg + 1];
            let node_idx = if haversine_m(pt.lat, pt.lon, lat1, lon1)
                <= haversine_m(pt.lat, pt.lon, lat2, lon2)
            {
                vert_nodes[best_seg]
            } else {
                vert_nodes[best_seg + 1]
            };
            PavedPoint {
                lat: pt.lat,
                lon: pt.lon,
                node_idx,
            }
        })
        .collect()
}

fn node_in_bbox(lat: f64, lon: f64, west: f64, south: f64, east: f64, north: f64) -> bool {
    lon >= west && lon <= east && lat >= south && lat <= north
}

/// Parse PBF once: jeep-road R-tree, routing graph, paved anchor index.
pub fn build_osm_routing(
    pbf_path: &Path,
    west: f64,
    south: f64,
    east: f64,
    north: f64,
    opts: &OsmRoutingOpts,
) -> Result<OsmRouting> {
    let pad = 0.05;
    let west = west - pad;
    let south = south - pad;
    let east = east + pad;
    let north = north + pad;
    let jeep_tags: HashSet<&str> = opts.jeep_highways.iter().map(String::as_str).collect();
    let paved_tags: HashSet<&str> = opts.paved_highways.iter().map(String::as_str).collect();
    let sample_step = opts.road_sample_step_m;

    info!(
        path = %pbf_path.display(),
        bbox = format!("{west:.4},{south:.4},{east:.4},{north:.4}"),
        "peaks: parsing OSM ways…"
    );
    let t0 = Instant::now();

    let mut osm_nodes: HashMap<i64, (f64, f64)> = HashMap::new();
    ElementReader::from_path(pbf_path)
        .context("open OSM PBF")?
        .for_each(|element| {
            let (id, lat, lon) = match element {
                Element::Node(node) => (node.id(), node.lat(), node.lon()),
                Element::DenseNode(node) => (node.id(), node.lat(), node.lon()),
                _ => return,
            };
            if node_in_bbox(lat, lon, west, south, east, north) {
                osm_nodes.insert(id, (lat, lon));
            }
        })?;
    info!(
        nodes = osm_nodes.len(),
        elapsed_secs = t0.elapsed().as_secs_f64(),
        "peaks: OSM nodes loaded"
    );

    let mut jeep_samples: Vec<RoadPoint> = Vec::new();
    let mut jeep_way_count = 0usize;
    let mut graph = JeepRoadGraph::new();
    let mut graph_node_map: HashMap<i64, u32> = HashMap::new();
    let mut paved_points: Vec<PavedPoint> = Vec::new();
    let mut graph_way_count = 0usize;

    ElementReader::from_path(pbf_path)
        .context("reopen OSM PBF")?
        .for_each(|element| {
            if let Element::Way(way) = element {
                let Some(tags) = parse_way_tags(way.tags()) else {
                    return;
                };
                let jeep = is_jeep_highway(tags, &jeep_tags);
                let paved = is_paved_highway(tags, &paved_tags);
                if !jeep && !paved {
                    return;
                }
                let mut coords = Vec::new();
                let mut osm_ids = Vec::new();
                for nid in way.refs() {
                    if let Some((lat, lon)) = osm_nodes.get(&nid) {
                        coords.push((*lat, *lon));
                        osm_ids.push(nid);
                    }
                }
                if coords.len() < 2 {
                    return;
                }
                if jeep {
                    jeep_samples.extend(sample_way(&coords, sample_step));
                    jeep_way_count += 1;
                }
                if jeep || paved {
                    graph_way_count += 1;
                    let mut vert_nodes = Vec::with_capacity(coords.len());
                    for i in 0..coords.len() {
                        let (lat, lon) = coords[i];
                        let n = graph.get_or_insert_node(osm_ids[i], lat, lon, &mut graph_node_map);
                        vert_nodes.push(n);
                    }
                    let anchor = is_paved_anchor(tags, &paved_tags);
                    for i in 0..coords.len() - 1 {
                        let (lat1, lon1) = coords[i];
                        let (lat2, lon2) = coords[i + 1];
                        let a = vert_nodes[i];
                        let b = vert_nodes[i + 1];
                        graph.add_way_segment(
                            a,
                            b,
                            lat1,
                            lon1,
                            lat2,
                            lon2,
                            tags.highway,
                            tags.tracktype,
                            tags.surface,
                            tags.fourwd_only,
                        );
                        if anchor {
                            paved_points.push(PavedPoint {
                                lat: lat1,
                                lon: lon1,
                                node_idx: a,
                            });
                            paved_points.push(PavedPoint {
                                lat: lat2,
                                lon: lon2,
                                node_idx: b,
                            });
                        }
                    }
                    if anchor {
                        paved_points.extend(paved_interpolated_samples(
                            &coords,
                            &vert_nodes,
                            sample_step,
                        ));
                    }
                }
            }
        })?;

    let jeep_point_count = jeep_samples.len();
    let jeep_tree = RTree::bulk_load(jeep_samples);
    let paved_tree = RTree::bulk_load(paved_points);

    info!(
        jeep_ways = jeep_way_count,
        jeep_points = jeep_point_count,
        graph_ways = graph_way_count,
        graph_nodes = graph.node_count(),
        paved_anchors = paved_tree.size(),
        elapsed_secs = t0.elapsed().as_secs_f64(),
        "peaks: OSM routing ready"
    );

    Ok(OsmRouting {
        jeep_roads: JeepRoadIndex {
            tree: jeep_tree,
            point_count: jeep_point_count,
            way_count: jeep_way_count,
        },
        graph,
        paved: PavedAnchorIndex { tree: paved_tree },
    })
}

/// Backward-compatible jeep-road index only.
pub fn build_jeep_road_index(
    pbf_path: &Path,
    west: f64,
    south: f64,
    east: f64,
    north: f64,
) -> Result<JeepRoadIndex> {
    Ok(build_osm_routing(
        pbf_path,
        west,
        south,
        east,
        north,
        &OsmRoutingOpts::default(),
    )?
    .jeep_roads)
}

#[cfg(test)]
pub(crate) fn test_paved_from_line(
    start: (f64, f64),
    end: (f64, f64),
    sample_step: f64,
) -> PavedAnchorIndex {
    let coords = [start, end];
    let vert_nodes = [0u32, 1u32];
    let mut points = vec![
        PavedPoint {
            lat: start.0,
            lon: start.1,
            node_idx: 0,
        },
        PavedPoint {
            lat: end.0,
            lon: end.1,
            node_idx: 1,
        },
    ];
    points.extend(paved_interpolated_samples(&coords, &vert_nodes, sample_step));
    PavedAnchorIndex {
        tree: RTree::bulk_load(points),
    }
}

#[cfg(test)]
pub(crate) fn test_index_from_points(points: &[(f64, f64)]) -> JeepRoadIndex {
    let samples: Vec<RoadPoint> = points
        .iter()
        .map(|(lat, lon)| RoadPoint {
            lat: *lat,
            lon: *lon,
        })
        .collect();
    let n = samples.len();
    JeepRoadIndex {
        tree: RTree::bulk_load(samples),
        point_count: n,
        way_count: n.max(1),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn jeep_highway_accepts_track_and_rejects_footway() {
        let jeep: HashSet<&str> = JEEP_HIGHWAY_TAGS.iter().copied().collect();
        assert!(is_jeep_highway(
            WayTags {
                highway: "track",
                tracktype: None,
                surface: None,
                fourwd_only: false,
            },
            &jeep
        ));
        assert!(!is_jeep_highway(
            WayTags {
                highway: "footway",
                tracktype: None,
                surface: None,
                fourwd_only: false,
            },
            &jeep
        ));
    }

    #[test]
    fn paved_highway_includes_primary() {
        let paved: HashSet<&str> = PAVED_HIGHWAY_TAGS.iter().copied().collect();
        assert!(is_paved_highway(
            WayTags {
                highway: "primary",
                tracktype: None,
                surface: None,
                fourwd_only: false,
            },
            &paved
        ));
    }

    #[test]
    fn unclassified_without_surface_is_not_paved_anchor() {
        let paved: HashSet<&str> = PAVED_HIGHWAY_TAGS.iter().copied().collect();
        assert!(!is_paved_anchor(
            WayTags {
                highway: "unclassified",
                tracktype: None,
                surface: None,
                fourwd_only: false,
            },
            &paved
        ));
        assert!(is_paved_anchor(
            WayTags {
                highway: "unclassified",
                tracktype: None,
                surface: Some("asphalt"),
                fourwd_only: false,
            },
            &paved
        ));
    }

    #[test]
    fn service_road_is_never_paved_anchor() {
        let paved: HashSet<&str> = PAVED_HIGHWAY_TAGS.iter().copied().collect();
        assert!(!is_paved_anchor(
            WayTags {
                highway: "service",
                tracktype: None,
                surface: Some("gravel"),
                fourwd_only: false,
            },
            &paved
        ));
    }

    #[test]
    fn primary_without_surface_is_paved_anchor() {
        let paved: HashSet<&str> = PAVED_HIGHWAY_TAGS.iter().copied().collect();
        assert!(is_paved_anchor(
            WayTags {
                highway: "primary",
                tracktype: None,
                surface: None,
                fourwd_only: false,
            },
            &paved
        ));
    }

    #[test]
    fn paved_vertices_only_miss_midpoint_pad() {
        let endpoints = PavedAnchorIndex::test_from_points(&[(38.0, -117.0), (38.0018, -117.0)]);
        let dest = (38.0009, -117.00005);
        assert!(
            endpoints.nearest_within(dest.0, dest.1, 40.0).is_none(),
            "sparse vertices should miss pad between them"
        );
    }

    #[test]
    fn paved_interpolated_hits_midpoint_pad() {
        use crate::park::ON_ROAD_SNAP_M;
        let paved = test_paved_from_line((38.0, -117.0), (38.0018, -117.0), 40.0);
        let dest = (38.0009, -117.00005);
        let hit = paved.nearest_within(dest.0, dest.1, ON_ROAD_SNAP_M);
        assert!(hit.is_some(), "40 m samples should reach midpoint pad");
        let (_, _, d) = hit.unwrap();
        assert!(d <= ON_ROAD_SNAP_M + 1.0);
    }

    #[test]
    fn nearest_within_finds_road_sample() {
        let idx = test_index_from_points(&[(38.0, -117.0), (38.001, -117.0)]);
        let hit = idx.nearest_within(38.0005, -117.0, 500.0);
        assert!(hit.is_some());
        let (_, _, d) = hit.unwrap();
        assert!(d < 500.0);
        assert_eq!(idx.points_within(38.0005, -117.0, 500.0).len(), 2);
    }

    #[test]
    fn road_at_six_tenths_mile_is_beyond_half_mile_cap() {
        use crate::hike::{haversine_m, DEFAULT_MAX_HIKE_M};
        let idx = test_index_from_points(&[(38.0, -117.0)]);
        let peak_lat = 38.0087;
        let peak_lon = -117.0;
        let dist = haversine_m(38.0, -117.0, peak_lat, peak_lon);
        assert!(dist > DEFAULT_MAX_HIKE_M);
        assert!(idx
            .nearest_within(peak_lat, peak_lon, DEFAULT_MAX_HIKE_M)
            .is_none());
    }

    #[test]
    fn distant_peak_has_no_road_within_half_mile() {
        use crate::hike::DEFAULT_MAX_HIKE_M;
        let idx = test_index_from_points(&[(38.0, -117.0)]);
        assert!(idx
            .nearest_within(39.0, -117.0, DEFAULT_MAX_HIKE_M)
            .is_none());
    }

    #[test]
    fn eip_like_site_at_road_passes_road_gate() {
        use crate::hike::{profile_hike, profile_passes, HikeSampleElev, DEFAULT_MAX_HIKE_M};

        struct FlatElev(f64);
        impl HikeSampleElev for FlatElev {
            fn sample_elev_m(&self, _lat: f64, _lon: f64) -> f64 {
                self.0
            }
        }

        let idx = test_index_from_points(&[(38.0, -117.0)]);
        let peak_lat = 38.0003;
        let peak_lon = -117.0;
        let road = idx.nearest_within(peak_lat, peak_lon, DEFAULT_MAX_HIKE_M);
        assert!(road.is_some());
        let (rlat, rlon, road_m) = road.unwrap();
        assert!(road_m < 100.0);
        let flat = FlatElev(2000.0);
        let profile = profile_hike(&flat, rlat, rlon, peak_lat, peak_lon, 30.0).unwrap();
        assert!(profile_passes(&profile, 45.0));
    }
}
