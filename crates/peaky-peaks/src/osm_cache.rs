//! On-disk OSM routing graph so serve / `peaky peaks` skip the PBF after the first parse.

use std::fs::File;
use std::io::{BufReader, BufWriter};
use std::path::Path;
use std::time::UNIX_EPOCH;

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use tracing::{info, warn};

use crate::osm::{
    GraphEdge, JeepRoadGraph, JeepRoadIndex, OsmRouting, OsmRoutingOpts, PavedAnchorIndex,
    PavedPoint, RoadPoint,
};

pub const OSM_ROUTING_CACHE_VERSION: u32 = 1;

const NONE: u8 = 255;

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
    paved_lat: Vec<f32>,
    paved_lon: Vec<f32>,
    paved_idx: Vec<u32>,
}

fn cache_path(pbf_path: &Path) -> std::path::PathBuf {
    pbf_path
        .parent()
        .unwrap_or_else(|| Path::new("."))
        .join("routing-v1.bin")
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
    let file = File::open(&path).ok()?;
    let disk: OsmRoutingDisk = match bincode::deserialize_from(BufReader::new(file)) {
        Ok(d) => d,
        Err(e) => {
            warn!(path = %path.display(), error = %e, "osm routing cache unreadable");
            return None;
        }
    };
    if disk.version != OSM_ROUTING_CACHE_VERSION {
        return None;
    }
    let (pbf_len, pbf_mtime) = pbf_fingerprint(pbf_path).ok()?;
    if disk.pbf_len != pbf_len || disk.pbf_mtime != pbf_mtime {
        return None;
    }
    if (disk.sample_step_m - opts.road_sample_step_m).abs() > 0.01 {
        return None;
    }
    if disk.jeep_highways != opts.jeep_highways || disk.paved_highways != opts.paved_highways {
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
        .min(disk.e_4wd.len());
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
    Ok(OsmRouting {
        jeep_roads: JeepRoadIndex::from_samples(jeep, disk.jeep_way_count as usize),
        graph: JeepRoadGraph::from_parts(nodes, adj),
        paved: PavedAnchorIndex::from_points(paved),
    })
}

/// Write the parsed graph next to the PBF. Overwrites any previous cache.
pub fn save_osm_routing_cache(
    pbf_path: &Path,
    west: f64,
    south: f64,
    east: f64,
    north: f64,
    opts: &OsmRoutingOpts,
    routing: &OsmRouting,
) -> Result<()> {
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
    for (from, edges) in routing.graph.adj_slice().iter().enumerate() {
        for edge in edges {
            e_from.push(from as u32);
            e_to.push(edge.to);
            e_len.push(edge.length_m as f32);
            e_hw.push(intern(&mut highways, &edge.highway));
            e_tt.push(intern_opt(&mut tracktypes, edge.tracktype.as_deref()));
            e_surf.push(intern_opt(&mut surfaces, edge.surface.as_deref()));
            e_4wd.push(u8::from(edge.fourwd_only));
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
    let disk = OsmRoutingDisk {
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
        paved_lat,
        paved_lon,
        paved_idx,
    };
    let path = cache_path(pbf_path);
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let tmp = path.with_extension("bin.tmp");
    {
        let file = File::create(&tmp).with_context(|| format!("create {}", tmp.display()))?;
        bincode::serialize_into(BufWriter::new(file), &disk)
            .with_context(|| format!("write {}", tmp.display()))?;
    }
    std::fs::rename(&tmp, &path).with_context(|| format!("rename {}", path.display()))?;
    info!(path = %path.display(), "peaks: OSM routing cache written");
    Ok(())
}
