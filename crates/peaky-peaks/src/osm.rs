//! OSM jeep-class roads: R-tree index + routing graph + paved anchors.

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::time::Instant;

use anyhow::{Context, Result};
use osmpbf::{Element, ElementReader};
use rstar::{RTree, RTreeObject, AABB};
use rayon::prelude::*;
use tracing::info;

use crate::hike::haversine_m;

/// Extra bbox pad applied when parsing the PBF.
pub const OSM_BUILD_PAD_DEG: f64 = 0.05;

/// Parse / cache-load stages for serve pin progress.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OsmParsePhase {
    Cache,
    Nodes,
    Ways,
    Index,
    Gtlf,
    Ready,
}

impl OsmParsePhase {
    pub fn step(self) -> u32 {
        match self {
            Self::Cache | Self::Nodes => 1,
            Self::Ways => 2,
            Self::Index => 3,
            Self::Gtlf | Self::Ready => 4,
        }
    }

    pub fn total(self) -> u32 {
        4
    }

    pub fn label(self) -> &'static str {
        match self {
            Self::Cache => "OSM graph",
            Self::Nodes => "OSM nodes",
            Self::Ways => "OSM roads",
            Self::Index => "OSM index",
            Self::Gtlf => "BLM roads",
            Self::Ready => "ready",
        }
    }
}

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

/// Options for OSM jeep/paved indexing (from ``Preset::resolved_access_meta()``).
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

use peaky_preset::{load_preset, DEFAULT_OSM_PBF_URL, OsmSourceConfig};

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
    pub(crate) fn from_samples(samples: Vec<RoadPoint>, way_count: usize) -> Self {
        let point_count = samples.len();
        if point_count >= 1_000_000 {
            info!(jeep_points = point_count, "peaks: building jeep R-tree");
        }
        Self {
            tree: RTree::bulk_load(samples),
            point_count,
            way_count,
        }
    }

    pub(crate) fn sample_points(&self) -> Vec<RoadPoint> {
        self.tree.iter().copied().collect()
    }

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

    pub(crate) fn from_parts(nodes: Vec<(f64, f64)>, adj: Vec<Vec<GraphEdge>>) -> Self {
        Self { nodes, adj }
    }

    pub(crate) fn nodes_slice(&self) -> &[(f64, f64)] {
        &self.nodes
    }

    pub(crate) fn adj_slice(&self) -> &[Vec<GraphEdge>] {
        &self.adj
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

    pub(crate) fn get_or_insert_node_public(
        &mut self,
        osm_id: i64,
        lat: f64,
        lon: f64,
        map: &mut HashMap<i64, u32>,
    ) -> u32 {
        self.get_or_insert_node(osm_id, lat, lon, map)
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

    pub(crate) fn add_way_segment_public(
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
        self.add_way_segment(a, b, lat1, lon1, lat2, lon2, highway, tracktype, surface, fourwd_only);
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
    pub(crate) fn from_points(points: Vec<PavedPoint>) -> Self {
        Self {
            tree: RTree::bulk_load(points),
        }
    }

    pub(crate) fn sample_points(&self) -> Vec<PavedPoint> {
        self.tree.iter().copied().collect()
    }

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

    /// Every paved anchor as a Dijkstra source `(lat, lon, node_idx)`.
    pub fn all_sources(&self) -> Vec<(f64, f64, u32)> {
        self.tree
            .iter()
            .map(|pt| (pt.lat, pt.lon, pt.node_idx))
            .collect()
    }
}

#[derive(Debug, Clone, Copy)]
struct GraphNodePoint {
    lat: f64,
    lon: f64,
    node_idx: u32,
}

impl RTreeObject for GraphNodePoint {
    type Envelope = AABB<[f64; 2]>;

    fn envelope(&self) -> Self::Envelope {
        AABB::from_point([self.lon, self.lat])
    }
}

/// Spatial index for jeep graph nodes (endpoint snap during GTLF merge).
pub struct GraphNodeIndex {
    tree: RTree<GraphNodePoint>,
}

impl GraphNodeIndex {
    pub fn from_graph(graph: &JeepRoadGraph) -> Self {
        let points: Vec<GraphNodePoint> = graph
            .nodes_slice()
            .iter()
            .enumerate()
            .map(|(i, &(lat, lon))| GraphNodePoint {
                lat,
                lon,
                node_idx: i as u32,
            })
            .collect();
        Self {
            tree: RTree::bulk_load(points),
        }
    }

    pub fn empty() -> Self {
        Self {
            tree: RTree::new(),
        }
    }

    pub fn insert_node(&mut self, lat: f64, lon: f64, node_idx: u32) {
        self.tree.insert(GraphNodePoint {
            lat,
            lon,
            node_idx,
        });
    }

    pub fn nearest_node(&self, lat: f64, lon: f64, max_m: f64) -> Option<u32> {
        if self.tree.size() == 0 {
            return None;
        }
        let deg = (max_m / 111_000.0).max(0.002);
        let envelope = AABB::from_corners([lon - deg, lat - deg], [lon + deg, lat + deg]);
        let mut best: Option<(u32, f64)> = None;
        for pt in self.tree.locate_in_envelope_intersecting(&envelope) {
            let d = haversine_m(lat, lon, pt.lat, pt.lon);
            if d <= max_m + 1.0 && best.map(|(_, bd)| d < bd).unwrap_or(true) {
                best = Some((pt.node_idx, d));
            }
        }
        best.map(|(idx, _)| idx)
    }

    /// Graph node indices within ``max_m`` crow distance of ``(lat, lon)``.
    pub fn nodes_within(&self, lat: f64, lon: f64, max_m: f64) -> Vec<u32> {
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
                out.push(pt.node_idx);
            }
        }
        out
    }
}

pub struct OsmRouting {
    pub jeep_roads: JeepRoadIndex,
    pub graph: JeepRoadGraph,
    pub paved: PavedAnchorIndex,
    pub graph_nodes: GraphNodeIndex,
}

pub fn osm_cache_path(project_dir: &Path, cache_name: &str) -> PathBuf {
    project_dir
        .join(".peaky/cache/osm")
        .join(cache_name)
}

fn resolved_osm_source(project_dir: &Path) -> OsmSourceConfig {
    let config_path = project_dir.join("config.yaml");
    load_preset(&config_path)
        .map(|p| p.sources.osm)
        .unwrap_or_default()
}

pub fn ensure_osm_pbf(project_dir: &Path, force: bool) -> Result<PathBuf> {
    let osm = resolved_osm_source(project_dir);
    ensure_osm_pbf_with_source(project_dir, &osm, force)
}

pub fn ensure_osm_pbf_with_source(
    project_dir: &Path,
    osm: &OsmSourceConfig,
    force: bool,
) -> Result<PathBuf> {
    let path = osm_cache_path(project_dir, &osm.cache_name);
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
    let url = if osm.pbf_url.is_empty() {
        DEFAULT_OSM_PBF_URL
    } else {
        osm.pbf_url.as_str()
    };
    info!(url, "peaks: downloading OSM PBF…");
    let t0 = Instant::now();
    let client = reqwest::blocking::Client::builder()
        .timeout(std::time::Duration::from_secs(600))
        .build()
        .context("build HTTP client")?;
    let bytes = client
        .get(url)
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

pub(crate) fn graph_edge_is_paved_anchor(edge: &GraphEdge, paved_tags: &HashSet<&str>) -> bool {
    is_paved_anchor(
        WayTags {
            highway: edge.highway.as_str(),
            tracktype: edge.tracktype.as_deref(),
            surface: edge.surface.as_deref(),
            fourwd_only: edge.fourwd_only,
        },
        paved_tags,
    )
}

pub(crate) fn sample_way_public(nodes: &[(f64, f64)], step_m: f64) -> Vec<RoadPoint> {
    sample_way(nodes, step_m)
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
    build_osm_routing_with_progress(pbf_path, west, south, east, north, opts, &mut |_, _| {})
}

pub fn build_osm_routing_with_progress<F>(
    pbf_path: &Path,
    west: f64,
    south: f64,
    east: f64,
    north: f64,
    opts: &OsmRoutingOpts,
    progress: &mut F,
) -> Result<OsmRouting>
where
    F: FnMut(OsmParsePhase, f64),
{
    let pad = OSM_BUILD_PAD_DEG;
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
    progress(OsmParsePhase::Nodes, 0.06);

    let node_rows: Vec<(i64, f64, f64)> = ElementReader::from_path(pbf_path)
        .context("open OSM PBF")?
        .par_map_reduce(
            |element| {
                let (id, lat, lon) = match element {
                    Element::Node(node) => (node.id(), node.lat(), node.lon()),
                    Element::DenseNode(node) => (node.id(), node.lat(), node.lon()),
                    _ => return Vec::new(),
                };
                if node_in_bbox(lat, lon, west, south, east, north) {
                    vec![(id, lat, lon)]
                } else {
                    Vec::new()
                }
            },
            Vec::new,
            |mut a, mut b| {
                a.append(&mut b);
                a
            },
        )?;
    let mut osm_nodes: HashMap<i64, (f64, f64)> = HashMap::with_capacity(node_rows.len());
    for (id, lat, lon) in node_rows {
        osm_nodes.insert(id, (lat, lon));
    }
    info!(
        nodes = osm_nodes.len(),
        elapsed_secs = t0.elapsed().as_secs_f64(),
        "peaks: OSM nodes loaded"
    );
    progress(OsmParsePhase::Ways, 0.22);

    let mut jeep_samples: Vec<RoadPoint> = Vec::new();
    let mut jeep_way_count = 0usize;
    let mut graph = JeepRoadGraph::new();
    let mut graph_node_map: HashMap<i64, u32> = HashMap::new();
    let mut paved_points: Vec<PavedPoint> = Vec::new();
    let mut graph_way_count = 0usize;
    let mut way_hits = 0usize;
    let ways_t0 = Instant::now();

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
                way_hits += 1;
                if way_hits % 2500 == 0 {
                    let t = ways_t0.elapsed().as_secs_f64();
                    let frac = 0.22 + 0.62 * (t / 80.0).min(0.98);
                    progress(OsmParsePhase::Ways, frac);
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

    progress(OsmParsePhase::Index, 0.88);
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
    progress(OsmParsePhase::Ready, 1.0);

    let graph_nodes = GraphNodeIndex::from_graph(&graph);
    Ok(OsmRouting {
        jeep_roads: JeepRoadIndex {
            tree: jeep_tree,
            point_count: jeep_point_count,
            way_count: jeep_way_count,
        },
        graph,
        paved: PavedAnchorIndex { tree: paved_tree },
        graph_nodes,
    })
}

/// Supplemental line for jeep graph merge (GTLF roads / OHV trails).
pub struct SupplementalLine {
    pub coords: Vec<(f64, f64)>,
    pub highway: String,
    pub tracktype: Option<String>,
}

const MERGE_ENDPOINT_SNAP_M: f64 = 40.0;
const MERGE_DEDUP_M: f64 = 30.0;

fn line_fully_covered(index: &JeepRoadIndex, coords: &[(f64, f64)]) -> bool {
    if coords.is_empty() {
        return true;
    }
    // Dense GTLF vertices are overkill for 30 m dedup; subsample along the line.
    let check_step_m = MERGE_DEDUP_M * 0.5;
    sample_way(coords, check_step_m).iter().all(|pt| {
        index
            .nearest_within(pt.lat, pt.lon, MERGE_DEDUP_M)
            .is_some()
    })
}

/// Merge driveable GTLF lines into an OSM routing graph (snap endpoints, dedup).
pub fn merge_supplemental_jeep_lines(
    routing: &mut OsmRouting,
    lines: &[SupplementalLine],
    sample_step_m: f64,
) {
    merge_supplemental_jeep_lines_progress(routing, lines, sample_step_m, &mut |_| {});
}

/// Same as [`merge_supplemental_jeep_lines`], reporting 0.0–1.0 merge fraction.
pub fn merge_supplemental_jeep_lines_progress<F>(
    routing: &mut OsmRouting,
    lines: &[SupplementalLine],
    sample_step_m: f64,
    progress: &mut F,
) where
    F: FnMut(f64),
{
    let total = lines.len();
    if total == 0 {
        progress(1.0);
        return;
    }
    progress(0.02);
    info!(lines = total, "gtlf: merging supplemental jeep lines");
    let merge_started = Instant::now();
    let mut graph_node_map: HashMap<i64, u32> = HashMap::new();
    let mut next_id: i64 = -1_000_000;
    info!(
        jeep_points = routing.jeep_roads.point_count,
        "gtlf: cloning existing jeep samples for merge index"
    );
    let clone_started = Instant::now();
    let mut new_samples: Vec<RoadPoint> = routing.jeep_roads.sample_points();
    info!(
        jeep_points = new_samples.len(),
        elapsed_secs = clone_started.elapsed().as_secs_f64(),
        "gtlf: jeep sample clone ready"
    );
    let jeep_roads = &routing.jeep_roads;
    let covered: Vec<bool> = lines
        .par_iter()
        .map(|line| {
            line.coords.len() >= 2 && line_fully_covered(jeep_roads, &line.coords)
        })
        .collect();
    let skipped_covered = covered.iter().filter(|&&c| c).count();
    let to_add: Vec<usize> = covered
        .iter()
        .enumerate()
        .filter_map(|(idx, &skip)| if skip { None } else { Some(idx) })
        .collect();
    info!(
        total,
        to_add = to_add.len(),
        skipped_covered,
        "gtlf: jeep merge coverage classified"
    );
    progress(0.12);

    let mut added_ways = 0usize;
    let mut last_progress_log = Instant::now();
    const MERGE_PROGRESS_LINES: usize = 250;

    for (pass_idx, &idx) in to_add.iter().enumerate() {
        let line = &lines[idx];
        let should_log = pass_idx == 0
            || pass_idx % MERGE_PROGRESS_LINES == 0
            || last_progress_log.elapsed().as_secs() >= 10;
        if should_log {
            let frac = if to_add.is_empty() {
                0.12
            } else {
                0.12 + 0.73 * (pass_idx as f64 / to_add.len() as f64)
            };
            progress(frac);
            info!(
                processed = pass_idx,
                total = to_add.len(),
                added = added_ways,
                skipped_covered,
                pct = if to_add.is_empty() {
                    0
                } else {
                    (pass_idx * 100 / to_add.len()) as u32
                },
                elapsed_secs = merge_started.elapsed().as_secs_f64(),
                "gtlf: jeep merge progress"
            );
            last_progress_log = Instant::now();
        }
        if line.coords.len() < 2 {
            continue;
        }
        added_ways += 1;
        new_samples.extend(sample_way(&line.coords, sample_step_m));

        let mut coords = line.coords.clone();
        if let Some(node) = routing.graph_nodes.nearest_node(
            coords[0].0,
            coords[0].1,
            MERGE_ENDPOINT_SNAP_M,
        ) {
            coords[0] = routing.graph.node_coords(node);
        }
        if let Some(node) = routing.graph_nodes.nearest_node(
            coords.last().unwrap().0,
            coords.last().unwrap().1,
            MERGE_ENDPOINT_SNAP_M,
        ) {
            let last = coords.len() - 1;
            coords[last] = routing.graph.node_coords(node);
        }

        let mut vert_nodes = Vec::with_capacity(coords.len());
        for &(lat, lon) in &coords {
            let id = next_id;
            next_id -= 1;
            let n = routing
                .graph
                .get_or_insert_node_public(id, lat, lon, &mut graph_node_map);
            routing.graph_nodes.insert_node(lat, lon, n);
            vert_nodes.push(n);
        }
        for i in 0..coords.len() - 1 {
            let (lat1, lon1) = coords[i];
            let (lat2, lon2) = coords[i + 1];
            routing.graph.add_way_segment_public(
                vert_nodes[i],
                vert_nodes[i + 1],
                lat1,
                lon1,
                lat2,
                lon2,
                &line.highway,
                line.tracktype.as_deref(),
                None,
                false,
            );
        }
    }

    if added_ways == 0 {
        info!(
            total,
            skipped_covered,
            "gtlf: jeep merge skipped (all lines already covered)"
        );
        progress(1.0);
        return;
    }
    info!(
        total,
        added = added_ways,
        skipped_covered,
        sample_points = new_samples.len(),
        "gtlf: rebuilding jeep R-tree after merge"
    );
    progress(0.88);
    let index_started = Instant::now();
    routing.jeep_roads =
        JeepRoadIndex::from_samples(new_samples, routing.jeep_roads.way_count + added_ways);
    info!(
        total,
        added = added_ways,
        skipped_covered,
        jeep_ways = routing.jeep_roads.way_count,
        jeep_points = routing.jeep_roads.point_count,
        elapsed_secs = index_started.elapsed().as_secs_f64(),
        merge_secs = merge_started.elapsed().as_secs_f64(),
        "gtlf: jeep merge indexed"
    );
    progress(1.0);
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
    fn parse_phase_labels_name_the_work() {
        assert_eq!(OsmParsePhase::Cache.label(), "OSM graph");
        assert_eq!(OsmParsePhase::Gtlf.label(), "BLM roads");
        assert_eq!(OsmParsePhase::Gtlf.total(), 4);
    }

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

    #[test]
    fn supplemental_ohv_track_merges_into_jeep_graph() {
        let graph = JeepRoadGraph::new();
        let mut routing = OsmRouting {
            jeep_roads: JeepRoadIndex {
                tree: RTree::bulk_load(vec![]),
                point_count: 0,
                way_count: 0,
            },
            graph,
            paved: PavedAnchorIndex::test_from_points(&[]),
            graph_nodes: GraphNodeIndex::empty(),
        };
        let line = SupplementalLine {
            coords: vec![(38.0, -117.0), (38.01, -117.0)],
            highway: "track".to_string(),
            tracktype: Some("grade3".to_string()),
        };
        merge_supplemental_jeep_lines(&mut routing, &[line], 30.0);
        assert!(routing.jeep_roads.point_count >= 2);
        assert!(routing.graph.node_count() >= 2);
    }
}
