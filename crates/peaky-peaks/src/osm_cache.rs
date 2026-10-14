//! On-disk OSM routing graph so serve / `peaky peaks` skip the PBF after the first parse.

use std::fs::File;
use std::io::{BufReader, BufWriter, Read};
use std::path::Path;
use std::time::UNIX_EPOCH;

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use tracing::{info, warn};

use peaky_preset::RoadRole;

use crate::osm::{
    GraphEdge, GraphNodeIndex, JeepRoadGraph, JeepRoadIndex, OsmRouting, OsmRoutingOpts,
    PavedAnchorIndex, PavedPoint, RoadPoint,
};
use crate::jeep::{JeepLengthBudget, PavedReachTree, PavedReachTreeDisk};
use crate::road_inventory::{
    inventory_from_disk, inventory_to_disk, OsmRoadInventory, RoadInventoryDisk,
    ROAD_INVENTORY_CACHE_VERSION,
};
use crate::roads_patch::osm_routing_base_for_cache;
use crate::segment_groups::{RoadSegmentGroup, SegmentGroupKey, SegmentGroupTags};

pub const OSM_ROUTING_CACHE_VERSION: u32 = 7;
pub const MERGED_ROUTING_CACHE_VERSION: u32 = 7;
pub const REACH_TREES_CACHE_VERSION: u32 = 1;

const NONE: u8 = 255;

/// On-disk caches write `version: u32` first. Peek before full bincode decode so stale
/// layouts cannot mis-read vector lengths and abort the process.
fn peek_cache_version(path: &Path) -> Option<u32> {
    let mut file = File::open(path).ok()?;
    let mut buf = [0u8; 4];
    file.read_exact(&mut buf).ok()?;
    Some(u32::from_le_bytes(buf))
}

fn remove_stale_cache(path: &Path, found: u32, expected: u32) {
    warn!(
        path = %path.display(),
        found,
        expected,
        "osm cache version mismatch, removing stale file"
    );
    let _ = std::fs::remove_file(path);
}

#[derive(Serialize, Deserialize)]
struct OsmRoutingDisk {
    version: u32,
    pbf_len: u64,
    pbf_mtime: u64,
    west: f64,
    south: f64,
    east: f64,
    north: f64,
    sample_step_m: f64,
    jeep_highways: Vec<String>,
    paved_highways: Vec<String>,
    segment_groups: Vec<SegmentGroupDisk>,
    jeep_way_count: u32,
    jeep_lat: Vec<f32>,
    jeep_lon: Vec<f32>,
    graph_lat: Vec<f32>,
    graph_lon: Vec<f32>,
    highways: Vec<String>,
    tracktypes: Vec<String>,
    surfaces: Vec<String>,
    e_from: Vec<u32>,
    e_to: Vec<u32>,
    e_len: Vec<f32>,
    e_hw: Vec<u8>,
    e_tt: Vec<u8>,
    e_surf: Vec<u8>,
    e_4wd: Vec<u8>,
    e_role: Vec<u8>,
    e_group: Vec<u32>,
    paved_lat: Vec<f32>,
    paved_lon: Vec<f32>,
    paved_idx: Vec<u32>,
}

#[derive(Serialize, Deserialize)]
struct MergedRoutingDisk {
    version: u32,
    gtlf_fingerprint: String,
    inner: OsmRoutingDisk,
}

#[derive(Serialize, Deserialize)]
struct SegmentGroupDisk {
    key_tag: u8,
    osm_id: i64,
    gtlf_layer: String,
    gtlf_hash: String,
    lat: Vec<f32>,
    lon: Vec<f32>,
    highway: String,
    tracktype: Option<String>,
    surface: Option<String>,
    fourwd_only: bool,
    role: u8,
    reason: Option<String>,
}

#[derive(Serialize, Deserialize)]
struct ReachTreesDisk {
    version: u32,
    pbf_len: u64,
    pbf_mtime: u64,
    gtlf_fingerprint: String,
    max_jeep_m: f64,
    budget_overall_m: f64,
    budget_class_keys: Vec<String>,
    budget_class_vals: Vec<f64>,
    unconstrained: PavedReachTreeDisk,
    budgeted: PavedReachTreeDisk,
}

fn segment_group_to_disk(g: &RoadSegmentGroup) -> Option<SegmentGroupDisk> {
    if matches!(g.key, SegmentGroupKey::Custom { .. }) {
        return None;
    }
    let (key_tag, osm_id, gtlf_layer, gtlf_hash) = match &g.key {
        SegmentGroupKey::Osm { osm_id } => (0u8, *osm_id, String::new(), String::new()),
        SegmentGroupKey::Gtlf {
            layer_id,
            coord_hash,
        } => (1u8, 0, layer_id.clone(), coord_hash.clone()),
        SegmentGroupKey::Custom { .. } => return None,
    };
    let mut lat = Vec::with_capacity(g.coords.len());
    let mut lon = Vec::with_capacity(g.coords.len());
    for &(la, lo) in &g.coords {
        lat.push(la as f32);
        lon.push(lo as f32);
    }
    Some(SegmentGroupDisk {
        key_tag,
        osm_id,
        gtlf_layer,
        gtlf_hash,
        lat,
        lon,
        highway: g.tags.highway.clone(),
        tracktype: g.tags.tracktype.clone(),
        surface: g.tags.surface.clone(),
        fourwd_only: g.tags.fourwd_only,
        role: road_role_to_u8(g.role),
        reason: g.reason.clone(),
    })
}

fn segment_group_from_disk(d: SegmentGroupDisk) -> RoadSegmentGroup {
    let coords: Vec<(f64, f64)> = d
        .lat
        .into_iter()
        .zip(d.lon)
        .map(|(lat, lon)| (lat as f64, lon as f64))
        .collect();
    let key = match d.key_tag {
        1 => SegmentGroupKey::Gtlf {
            layer_id: d.gtlf_layer,
            coord_hash: d.gtlf_hash,
        },
        _ => SegmentGroupKey::Osm { osm_id: d.osm_id },
    };
    RoadSegmentGroup {
        key,
        coords,
        tags: SegmentGroupTags {
            highway: d.highway,
            tracktype: d.tracktype,
            surface: d.surface,
            fourwd_only: d.fourwd_only,
        },
        role: road_role_from_u8(d.role),
        reason: d.reason,
    }
}

fn reach_cache_path(pbf_path: &Path) -> std::path::PathBuf {
    pbf_path
        .parent()
        .unwrap_or_else(|| Path::new("."))
        .join("routing-reach-v1.bin")
}


fn cache_path(pbf_path: &Path) -> std::path::PathBuf {
    pbf_path
        .parent()
        .unwrap_or_else(|| Path::new("."))
        .join("routing-v1.bin")
}

fn merged_cache_path(pbf_path: &Path) -> std::path::PathBuf {
    pbf_path
        .parent()
        .unwrap_or_else(|| Path::new("."))
        .join("routing-merged.bin")
}

/// Drop OSM routing caches so the next load reparses the PBF (PBF/GTLF source change only).
pub fn invalidate_osm_routing_caches(pbf_path: &Path) {
    for name in [
        "routing-v1.bin",
        "routing-merged.bin",
        "road-inventory-v1.bin",
        "routing-reach-v1.bin",
    ] {
        let path = pbf_path
            .parent()
            .unwrap_or_else(|| Path::new("."))
            .join(name);
        if path.is_file() {
            if let Err(e) = std::fs::remove_file(&path) {
                warn!(path = %path.display(), error = %e, "osm routing cache delete failed");
            }
        }
    }
}

fn inventory_cache_path(pbf_path: &Path) -> std::path::PathBuf {
    pbf_path
        .parent()
        .unwrap_or_else(|| Path::new("."))
        .join("road-inventory-v1.bin")
}

fn pbf_fingerprint(pbf_path: &Path) -> Result<(u64, u64)> {
    let meta = std::fs::metadata(pbf_path).context("OSM PBF metadata")?;
    let mtime = meta
        .modified()
        .ok()
        .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
        .map(|d| d.as_secs())
        .unwrap_or(0);
    Ok((meta.len(), mtime))
}

fn intern(table: &mut Vec<String>, value: &str) -> u8 {
    if let Some(i) = table.iter().position(|s| s == value) {
        return i.min(254) as u8;
    }
    if table.len() >= 254 {
        return 0;
    }
    table.push(value.to_string());
    (table.len() - 1) as u8
}

fn intern_opt(table: &mut Vec<String>, value: Option<&str>) -> u8 {
    match value {
        Some(s) if !s.is_empty() => intern(table, s),
        _ => NONE,
    }
}

fn lookup<'a>(table: &'a [String], idx: u8) -> Option<&'a str> {
    if idx == NONE {
        return None;
    }
    table.get(idx as usize).map(String::as_str)
}

fn bbox_covers(
    cached: (f64, f64, f64, f64),
    west: f64,
    south: f64,
    east: f64,
    north: f64,
) -> bool {
    cached.0 <= west + 1e-6
        && cached.1 <= south + 1e-6
        && cached.2 >= east - 1e-6
        && cached.3 >= north - 1e-6
}

/// Load a cached graph if it matches this PBF + opts and covers the requested bbox.
pub fn try_load_osm_routing_cache(
    pbf_path: &Path,
    west: f64,
    south: f64,
    east: f64,
    north: f64,
    opts: &OsmRoutingOpts,
) -> Option<OsmRouting> {
    let path = cache_path(pbf_path);
    let version = peek_cache_version(&path)?;
    if version != OSM_ROUTING_CACHE_VERSION {
        remove_stale_cache(&path, version, OSM_ROUTING_CACHE_VERSION);
        return None;
    }
    let file = File::open(&path).ok()?;
    info!(path = %path.display(), "peaks: OSM routing cache load start");
    let disk: OsmRoutingDisk = match bincode::deserialize_from(BufReader::new(file)) {
        Ok(d) => d,
        Err(e) => {
            warn!(path = %path.display(), error = %e, "osm routing cache unreadable");
            return None;
        }
    };
    info!(
        path = %path.display(),
        jeep_points = disk.jeep_lat.len(),
        "peaks: OSM routing cache deserialized, building indexes"
    );
    let (pbf_len, pbf_mtime) = pbf_fingerprint(pbf_path).ok()?;
    if disk.pbf_len != pbf_len || disk.pbf_mtime != pbf_mtime {
        return None;
    }
    if (disk.sample_step_m - opts.road_sample_step_m).abs() > 0.01 {
        return None;
    }
    if !bbox_covers((disk.west, disk.south, disk.east, disk.north), west, south, east, north) {
        return None;
    }
    match routing_from_disk(disk) {
        Ok(routing) => {
            info!(path = %path.display(), "peaks: OSM routing cache hit");
            Some(routing)
        }
        Err(e) => {
            warn!(path = %path.display(), error = %e, "osm routing cache rebuild failed");
            None
        }
    }
}

fn routing_from_disk(disk: OsmRoutingDisk) -> Result<OsmRouting> {
    let n = disk.jeep_lat.len().min(disk.jeep_lon.len());
    let mut jeep = Vec::with_capacity(n);
    for i in 0..n {
        jeep.push(RoadPoint {
            lat: disk.jeep_lat[i] as f64,
            lon: disk.jeep_lon[i] as f64,
        });
    }
    let gn = disk.graph_lat.len().min(disk.graph_lon.len());
    let mut nodes = Vec::with_capacity(gn);
    for i in 0..gn {
        nodes.push((disk.graph_lat[i] as f64, disk.graph_lon[i] as f64));
    }
    let mut adj = vec![Vec::new(); gn];
    let edge_n = disk
        .e_from
        .len()
        .min(disk.e_to.len())
        .min(disk.e_len.len())
        .min(disk.e_hw.len())
        .min(disk.e_tt.len())
        .min(disk.e_surf.len())
        .min(disk.e_4wd.len())
        .min(disk.e_role.len())
        .min(disk.e_group.len());
    for i in 0..edge_n {
        let from = disk.e_from[i] as usize;
        if from >= gn {
            continue;
        }
        adj[from].push(GraphEdge {
            to: disk.e_to[i],
            length_m: disk.e_len[i] as f64,
            highway: lookup(&disk.highways, disk.e_hw[i])
                .unwrap_or("track")
                .to_string(),
            tracktype: lookup(&disk.tracktypes, disk.e_tt[i]).map(str::to_string),
            surface: lookup(&disk.surfaces, disk.e_surf[i]).map(str::to_string),
            fourwd_only: disk.e_4wd[i] != 0,
            role: road_role_from_u8(disk.e_role[i]),
            group_id: disk.e_group[i],
        });
    }
    let pn = disk
        .paved_lat
        .len()
        .min(disk.paved_lon.len())
        .min(disk.paved_idx.len());
    let mut paved = Vec::with_capacity(pn);
    for i in 0..pn {
        paved.push(PavedPoint {
            lat: disk.paved_lat[i] as f64,
            lon: disk.paved_lon[i] as f64,
            node_idx: disk.paved_idx[i],
        });
    }
    let graph = JeepRoadGraph::from_parts(nodes, adj);
    let segment_groups: Vec<RoadSegmentGroup> = disk
        .segment_groups
        .into_iter()
        .map(segment_group_from_disk)
        .collect();
    let jeep_point_count = jeep.len();
    let paved_count = paved.len();
    let routing = OsmRouting {
        jeep_roads: JeepRoadIndex::from_vec(jeep, disk.jeep_way_count as usize),
        graph_nodes: GraphNodeIndex::from_graph(&graph),
        graph,
        paved: PavedAnchorIndex::from_points(paved),
        segment_groups,
    };
    info!(
        jeep_points = jeep_point_count,
        graph_nodes = routing.graph.node_count(),
        paved_anchors = paved_count,
        segment_groups = routing.segment_groups.len(),
        "peaks: routing indexes ready (jeep R-tree lazy until queried)"
    );
    Ok(routing)
}

fn road_role_from_u8(v: u8) -> RoadRole {
    match v {
        0 => RoadRole::Paved,
        1 => RoadRole::Jeep,
        2 => RoadRole::Hiking,
        _ => RoadRole::Other,
    }
}

fn road_role_to_u8(role: RoadRole) -> u8 {
    match role {
        RoadRole::Paved => 0,
        RoadRole::Jeep => 1,
        RoadRole::Hiking => 2,
        RoadRole::Other => 3,
    }
}

fn routing_to_disk(
    pbf_path: &Path,
    west: f64,
    south: f64,
    east: f64,
    north: f64,
    opts: &OsmRoutingOpts,
    routing: &OsmRouting,
) -> Result<OsmRoutingDisk> {
    let (pbf_len, pbf_mtime) = pbf_fingerprint(pbf_path)?;
    let jeep = routing.jeep_roads.sample_points();
    let mut jeep_lat = Vec::with_capacity(jeep.len());
    let mut jeep_lon = Vec::with_capacity(jeep.len());
    for pt in &jeep {
        jeep_lat.push(pt.lat as f32);
        jeep_lon.push(pt.lon as f32);
    }
    let nodes = routing.graph.nodes_slice();
    let mut graph_lat = Vec::with_capacity(nodes.len());
    let mut graph_lon = Vec::with_capacity(nodes.len());
    for &(lat, lon) in nodes {
        graph_lat.push(lat as f32);
        graph_lon.push(lon as f32);
    }
    let mut highways = Vec::new();
    let mut tracktypes = Vec::new();
    let mut surfaces = Vec::new();
    let mut e_from = Vec::new();
    let mut e_to = Vec::new();
    let mut e_len = Vec::new();
    let mut e_hw = Vec::new();
    let mut e_tt = Vec::new();
    let mut e_surf = Vec::new();
    let mut e_4wd = Vec::new();
    let mut e_role = Vec::new();
    let mut e_group = Vec::new();
    for (from, edges) in routing.graph.adj_slice().iter().enumerate() {
        for edge in edges {
            e_from.push(from as u32);
            e_to.push(edge.to);
            e_len.push(edge.length_m as f32);
            e_hw.push(intern(&mut highways, &edge.highway));
            e_tt.push(intern_opt(&mut tracktypes, edge.tracktype.as_deref()));
            e_surf.push(intern_opt(&mut surfaces, edge.surface.as_deref()));
            e_4wd.push(u8::from(edge.fourwd_only));
            e_role.push(road_role_to_u8(edge.role));
            e_group.push(edge.group_id);
        }
    }
    let paved = routing.paved.sample_points();
    let mut paved_lat = Vec::with_capacity(paved.len());
    let mut paved_lon = Vec::with_capacity(paved.len());
    let mut paved_idx = Vec::with_capacity(paved.len());
    for pt in &paved {
        paved_lat.push(pt.lat as f32);
        paved_lon.push(pt.lon as f32);
        paved_idx.push(pt.node_idx);
    }
    Ok(OsmRoutingDisk {
        version: OSM_ROUTING_CACHE_VERSION,
        pbf_len,
        pbf_mtime,
        west,
        south,
        east,
        north,
        sample_step_m: opts.road_sample_step_m,
        jeep_highways: opts.jeep_highways.clone(),
        paved_highways: opts.paved_highways.clone(),
        segment_groups: routing
            .segment_groups
            .iter()
            .filter_map(segment_group_to_disk)
            .collect(),
        jeep_way_count: routing.jeep_roads.way_count as u32,
        jeep_lat,
        jeep_lon,
        graph_lat,
        graph_lon,
        highways,
        tracktypes,
        surfaces,
        e_from,
        e_to,
        e_len,
        e_hw,
        e_tt,
        e_surf,
        e_4wd,
        e_role,
        e_group,
        paved_lat,
        paved_lon,
        paved_idx,
    })
}

fn write_bincode<T: Serialize>(path: &Path, value: &T) -> Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let tmp = path.with_extension("bin.tmp");
    {
        let file = File::create(&tmp).with_context(|| format!("create {}", tmp.display()))?;
        bincode::serialize_into(BufWriter::new(file), value)
            .with_context(|| format!("write {}", tmp.display()))?;
    }
    std::fs::rename(&tmp, path).with_context(|| format!("rename {}", path.display()))?;
    Ok(())
}

fn disk_matches_request(
    disk: &OsmRoutingDisk,
    pbf_path: &Path,
    west: f64,
    south: f64,
    east: f64,
    north: f64,
    opts: &OsmRoutingOpts,
) -> bool {
    if disk.version != OSM_ROUTING_CACHE_VERSION {
        return false;
    }
    let Ok((pbf_len, pbf_mtime)) = pbf_fingerprint(pbf_path) else {
        return false;
    };
    if disk.pbf_len != pbf_len || disk.pbf_mtime != pbf_mtime {
        return false;
    }
    if (disk.sample_step_m - opts.road_sample_step_m).abs() > 0.01 {
        return false;
    }
    bbox_covers((disk.west, disk.south, disk.east, disk.north), west, south, east, north)
}

fn inventory_disk_matches(
    disk: &RoadInventoryDisk,
    pbf_path: &Path,
    west: f64,
    south: f64,
    east: f64,
    north: f64,
    opts: &OsmRoutingOpts,
) -> bool {
    if disk.version != ROAD_INVENTORY_CACHE_VERSION {
        return false;
    }
    let Ok((pbf_len, pbf_mtime)) = pbf_fingerprint(pbf_path) else {
        return false;
    };
    if disk.pbf_len != pbf_len || disk.pbf_mtime != pbf_mtime {
        return false;
    }
    bbox_covers((disk.west, disk.south, disk.east, disk.north), west, south, east, north)
}

/// Load OSM highway inventory written beside the PBF cache.
pub fn try_load_road_inventory_cache(
    pbf_path: &Path,
    west: f64,
    south: f64,
    east: f64,
    north: f64,
    opts: &OsmRoutingOpts,
) -> Option<OsmRoadInventory> {
    let path = inventory_cache_path(pbf_path);
    let version = peek_cache_version(&path)?;
    if version != ROAD_INVENTORY_CACHE_VERSION {
        remove_stale_cache(&path, version, ROAD_INVENTORY_CACHE_VERSION);
        return None;
    }
    let file = File::open(&path).ok()?;
    let disk: RoadInventoryDisk = bincode::deserialize_from(BufReader::new(file)).ok()?;
    if !inventory_disk_matches(&disk, pbf_path, west, south, east, north, opts) {
        return None;
    }
    Some(inventory_from_disk(disk))
}

/// Write the parsed OSM-only graph next to the PBF. Overwrites any previous cache.
pub fn save_osm_routing_cache(
    pbf_path: &Path,
    west: f64,
    south: f64,
    east: f64,
    north: f64,
    opts: &OsmRoutingOpts,
    routing: &OsmRouting,
    inventory: &OsmRoadInventory,
) -> Result<()> {
    let base = osm_routing_base_for_cache(routing);
    let disk = routing_to_disk(pbf_path, west, south, east, north, opts, &base)?;
    let path = cache_path(pbf_path);
    write_bincode(&path, &disk)?;
    info!(path = %path.display(), "peaks: OSM routing cache written");
    let (pbf_len, pbf_mtime) = pbf_fingerprint(pbf_path)?;
    let inv_disk = inventory_to_disk(
        pbf_len,
        pbf_mtime,
        west,
        south,
        east,
        north,
        opts,
        inventory,
    );
    let inv_path = inventory_cache_path(pbf_path);
    write_bincode(&inv_path, &inv_disk)?;
    info!(path = %inv_path.display(), ways = inventory.ways.len(), "peaks: road inventory cache written");
    Ok(())
}

/// Write OSM+GTLF jeep graph, keyed by GTLF layer fingerprint.
pub fn save_merged_routing_cache(
    pbf_path: &Path,
    west: f64,
    south: f64,
    east: f64,
    north: f64,
    opts: &OsmRoutingOpts,
    routing: &OsmRouting,
    gtlf_fingerprint: &str,
) -> Result<()> {
    let base = osm_routing_base_for_cache(routing);
    let inner = routing_to_disk(pbf_path, west, south, east, north, opts, &base)?;
    let disk = MergedRoutingDisk {
        version: MERGED_ROUTING_CACHE_VERSION,
        gtlf_fingerprint: gtlf_fingerprint.to_string(),
        inner,
    };
    let path = merged_cache_path(pbf_path);
    write_bincode(&path, &disk)?;
    info!(
        path = %path.display(),
        fingerprint = %gtlf_fingerprint,
        "peaks: merged routing cache written"
    );
    Ok(())
}

/// Load a GTLF-merged graph if PBF, opts, bbox, and GTLF fingerprint match.
pub fn try_load_merged_routing_cache(
    pbf_path: &Path,
    west: f64,
    south: f64,
    east: f64,
    north: f64,
    opts: &OsmRoutingOpts,
    gtlf_fingerprint: &str,
) -> Option<OsmRouting> {
    if gtlf_fingerprint.is_empty() || gtlf_fingerprint == "disabled" {
        return None;
    }
    let path = merged_cache_path(pbf_path);
    let version = peek_cache_version(&path)?;
    if version != MERGED_ROUTING_CACHE_VERSION {
        remove_stale_cache(&path, version, MERGED_ROUTING_CACHE_VERSION);
        return None;
    }
    let file = File::open(&path).ok()?;
    info!(path = %path.display(), "peaks: merged routing cache load start");
    let disk: MergedRoutingDisk = match bincode::deserialize_from(BufReader::new(file)) {
        Ok(d) => d,
        Err(e) => {
            warn!(path = %path.display(), error = %e, "merged routing cache unreadable");
            return None;
        }
    };
    if disk.gtlf_fingerprint != gtlf_fingerprint {
        info!(
            cached = %disk.gtlf_fingerprint,
            want = %gtlf_fingerprint,
            "peaks: merged routing cache fingerprint miss"
        );
        return None;
    }
    if !disk_matches_request(&disk.inner, pbf_path, west, south, east, north, opts) {
        return None;
    }
    info!(
        path = %path.display(),
        jeep_points = disk.inner.jeep_lat.len(),
        "peaks: merged routing cache deserialized, building indexes"
    );
    match routing_from_disk(disk.inner) {
        Ok(routing) => {
            info!(path = %path.display(), "peaks: merged routing cache hit");
            Some(routing)
        }
        Err(e) => {
            warn!(path = %path.display(), error = %e, "merged routing cache rebuild failed");
            None
        }
    }
}

/// Persist trusted paved-reach floods beside the merged routing bin.
pub fn save_reach_trees_cache(
    pbf_path: &Path,
    gtlf_fingerprint: &str,
    max_jeep_m: f64,
    budget: &JeepLengthBudget,
    unconstrained: &PavedReachTree,
    budgeted: &PavedReachTree,
) -> Result<()> {
    let (pbf_len, pbf_mtime) = pbf_fingerprint(pbf_path)?;
    let (budget_overall_m, budget_class_keys, budget_class_vals) = budget.cache_key_vectors();
    let disk = ReachTreesDisk {
        version: REACH_TREES_CACHE_VERSION,
        pbf_len,
        pbf_mtime,
        gtlf_fingerprint: gtlf_fingerprint.to_string(),
        max_jeep_m,
        budget_overall_m,
        budget_class_keys,
        budget_class_vals,
        unconstrained: unconstrained.to_disk(),
        budgeted: budgeted.to_disk(),
    };
    write_bincode(&reach_cache_path(pbf_path), &disk)?;
    info!(path = %reach_cache_path(pbf_path).display(), "peaks: reach trees cache written");
    Ok(())
}

fn reach_disk_matches(
    disk: &ReachTreesDisk,
    pbf_path: &Path,
    gtlf_fingerprint: &str,
    max_jeep_m: f64,
    budget: &JeepLengthBudget,
) -> bool {
    if disk.version != REACH_TREES_CACHE_VERSION {
        return false;
    }
    let Ok((pbf_len, pbf_mtime)) = pbf_fingerprint(pbf_path) else {
        return false;
    };
    if disk.pbf_len != pbf_len || disk.pbf_mtime != pbf_mtime {
        return false;
    }
    if disk.gtlf_fingerprint != gtlf_fingerprint {
        return false;
    }
    if (disk.max_jeep_m - max_jeep_m).abs() > 0.5 {
        return false;
    }
    let (overall, keys, vals) = budget.cache_key_vectors();
    if (disk.budget_overall_m - overall).abs() > 0.5 {
        return false;
    }
    disk.budget_class_keys == keys && disk.budget_class_vals == vals
}

/// Load cached trusted reach trees when keys match the live graph sources.
pub fn try_load_reach_trees_cache(
    pbf_path: &Path,
    gtlf_fingerprint: &str,
    max_jeep_m: f64,
    budget: &JeepLengthBudget,
) -> Option<(PavedReachTree, PavedReachTree)> {
    let path = reach_cache_path(pbf_path);
    let version = peek_cache_version(&path)?;
    if version != REACH_TREES_CACHE_VERSION {
        remove_stale_cache(&path, version, REACH_TREES_CACHE_VERSION);
        return None;
    }
    let file = File::open(&path).ok()?;
    let disk: ReachTreesDisk = bincode::deserialize_from(BufReader::new(file)).ok()?;
    if !reach_disk_matches(&disk, pbf_path, gtlf_fingerprint, max_jeep_m, budget) {
        return None;
    }
    info!(path = %path.display(), "peaks: reach trees cache hit");
    Some((
        PavedReachTree::from_disk(disk.unconstrained),
        PavedReachTree::from_disk(disk.budgeted),
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::osm::{
        GraphNodeIndex, JeepRoadGraph, JeepRoadIndex, OsmRouting, PavedAnchorIndex,
    };

    fn empty_routing() -> OsmRouting {
        let graph = JeepRoadGraph::new();
        OsmRouting {
            jeep_roads: JeepRoadIndex::from_samples(Vec::new(), 0),
            graph_nodes: GraphNodeIndex::from_graph(&graph),
            graph,
            paved: PavedAnchorIndex::from_points(Vec::new()),
            segment_groups: Vec::new(),
        }
    }

    fn test_opts() -> OsmRoutingOpts {
        OsmRoutingOpts {
            road_sample_step_m: 30.0,
            jeep_highways: vec!["track".into()],
            paved_highways: vec!["primary".into()],
            roads_fingerprint: String::new(),
        }
    }

    #[test]
    fn merged_cache_hits_matching_fingerprint() {
        let dir = tempfile::tempdir().unwrap();
        let pbf = dir.path().join("nevada-latest.osm.pbf");
        std::fs::write(&pbf, b"fake-pbf").unwrap();
        let opts = test_opts();
        let routing = empty_routing();
        save_merged_routing_cache(&pbf, -120.0, 36.0, -114.0, 42.0, &opts, &routing, "abc123")
            .unwrap();
        assert!(try_load_merged_routing_cache(
            &pbf, -119.0, 37.0, -115.0, 41.0, &opts, "abc123"
        )
        .is_some());
        assert!(try_load_merged_routing_cache(
            &pbf, -119.0, 37.0, -115.0, 41.0, &opts, "other"
        )
        .is_none());
        assert!(try_load_merged_routing_cache(
            &pbf, -119.0, 37.0, -115.0, 41.0, &opts, "disabled"
        )
        .is_none());
    }

    #[test]
    fn merged_cache_omits_custom_edges_on_disk() {
        use crate::osm::{JeepRoadGraph, JeepRoadIndex, GraphNodeIndex, PavedAnchorIndex};

        let dir = tempfile::tempdir().unwrap();
        let pbf = dir.path().join("nevada-latest.osm.pbf");
        std::fs::write(&pbf, b"fake-pbf").unwrap();
        let opts = test_opts();
        let mut graph = JeepRoadGraph::new();
        let a = graph.insert_node(38.0, -117.0);
        let b = graph.insert_node(38.001, -117.0);
        graph.add_way_segment_classified(
            a,
            b,
            38.0,
            -117.0,
            38.001,
            -117.0,
            "custom",
            None,
            None,
            false,
            peaky_preset::RoadRole::Jeep,
            0,
        );
        let graph_nodes = GraphNodeIndex::from_graph(&graph);
        let routing = OsmRouting {
            jeep_roads: JeepRoadIndex::from_samples(Vec::new(), 0),
            graph,
            paved: PavedAnchorIndex::from_points(Vec::new()),
            graph_nodes,
            segment_groups: vec![crate::segment_groups::RoadSegmentGroup {
                key: crate::segment_groups::SegmentGroupKey::Custom {
                    id: "draft-1".into(),
                },
                coords: vec![(38.0, -117.0), (38.001, -117.0)],
                tags: crate::segment_groups::SegmentGroupTags {
                    highway: "custom".into(),
                    tracktype: None,
                    surface: None,
                    fourwd_only: false,
                },
                role: peaky_preset::RoadRole::Jeep,
                reason: None,
            }],
        };
        save_merged_routing_cache(&pbf, -120.0, 36.0, -114.0, 42.0, &opts, &routing, "abc123")
            .unwrap();
        let loaded = try_load_merged_routing_cache(
            &pbf, -119.0, 37.0, -115.0, 41.0, &opts, "abc123",
        )
        .expect("load merged");
        assert!(loaded.segment_groups.is_empty());
        assert!(loaded.graph.adj_slice().iter().all(|adj| adj.is_empty()));
    }

    #[test]
    fn merged_cache_drops_stale_version_before_decode() {
        let dir = tempfile::tempdir().unwrap();
        let pbf = dir.path().join("nevada-latest.osm.pbf");
        std::fs::write(&pbf, b"fake-pbf").unwrap();
        let merged = merged_cache_path(&pbf);
        std::fs::write(&merged, 3u32.to_le_bytes()).unwrap();
        let opts = test_opts();
        assert!(try_load_merged_routing_cache(
            &pbf, -119.0, 37.0, -115.0, 41.0, &opts, "abc123"
        )
        .is_none());
        assert!(!merged.exists());
    }
}
