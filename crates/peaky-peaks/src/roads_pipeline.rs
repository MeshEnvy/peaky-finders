//! Unified road graph: source plugins → base → internal → master → routing cache.

use std::collections::{HashMap, HashSet};
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::Instant;

use anyhow::{Context, Result};
use geojson::{Feature, FeatureCollection, GeoJson, Geometry, Value as GjValue};
use peaky_preset::project::ProjectLayout;
use peaky_preset::{
    ensure_roads_book, load_roads_book, save_roads_book, source_config_yaml, RoadRole, RoadRule,
    RoadsBook,
};
use tracing::info;

use crate::osm::{GraphNodeIndex, OsmRouting, OsmRoutingBuild, OsmRoutingOpts};
use crate::road_inventory::{
    inventory_role_from_road, InventoryRole, OsmInventoryWay, OsmRoadInventory,
};
use crate::segment_groups::{RoadSegmentGroup, SegmentGroupKey, SegmentGroupTags};
use crate::trail::{build_trail_graph, TrailRouting};

const MASTER_CACHE_VERSION: u32 = 3;
const BASE_CACHE_VERSION: u32 = 2;

/// How `update-sources` and the runtime road refresh treat cached GeoJSON.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SourceFetch {
    /// Run every source plugin, even when a cache file exists.
    Refresh,
    /// Run a plugin only when that source's GeoJSON is missing.
    AssumeFresh,
    /// Do not run plugins. A missing cache file is an error.
    NoFetch,
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct RoadSegment {
    pub id: String,
    pub kind: String,
    pub surface: String,
    pub grade: String,
    pub access: String,
    pub role: RoadRole,
    pub coords: Vec<(f64, f64)>,
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
struct MasterCacheMeta {
    version: u32,
    source_fps: Vec<(String, String)>,
    base_fp: String,
    internal_fp: String,
    match_rules_fp: String,
    master_fp: String,
}

pub fn bytes_fingerprint(bytes: &[u8]) -> String {
    use std::collections::hash_map::DefaultHasher;
    use std::hash::{Hash, Hasher};
    let mut hasher = DefaultHasher::new();
    bytes.hash(&mut hasher);
    format!("{:016x}", hasher.finish())
}

pub fn file_fingerprint(path: &Path) -> Result<String> {
    let bytes = fs::read(path).with_context(|| format!("read {}", path.display()))?;
    Ok(bytes_fingerprint(&bytes))
}

fn source_geojson_path(cache_dir: &Path, cache_key: &str) -> PathBuf {
    cache_dir
        .join("sources")
        .join(format!("{cache_key}.geojson"))
}

fn source_fingerprint_path(geojson_path: &Path) -> PathBuf {
    geojson_path.with_extension("fingerprint")
}

fn layout_for(project_dir: &Path) -> ProjectLayout {
    ProjectLayout {
        project_dir: project_dir.to_path_buf(),
        config_path: project_dir.join("config.yaml"),
    }
}

fn source_cache_key(book: &RoadsBook, source_key: &str) -> Result<String> {
    let cfg = book
        .sources
        .get(source_key)
        .with_context(|| format!("unknown roads source {source_key}"))?;
    let raw = cfg
        .get("cache_key")
        .and_then(|v| v.as_str())
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .with_context(|| format!("source {source_key} missing cache_key"))?;
    if raw == "." || raw == ".." || raw.contains('/') || raw.contains('\\') {
        anyhow::bail!("source {source_key} cache_key must be a single path segment");
    }
    Ok(raw.to_string())
}

/// Hash of the GeoJSON file bytes. The same bytes always hash the same.
/// A matching hash does not mean the upstream feed is unchanged.
fn store_source_fingerprint(geojson_path: &Path) -> Result<String> {
    let fp = file_fingerprint(geojson_path)?;
    let path = source_fingerprint_path(geojson_path);
    fs::write(&path, format!("{fp}\n")).with_context(|| format!("write {}", path.display()))?;
    Ok(fp)
}

fn path_byte_len(path: &Path) -> u64 {
    fs::metadata(path).map(|m| m.len()).unwrap_or(0)
}

fn read_geojson_features(path: &Path) -> Result<Vec<Feature>> {
    let bytes = fs::read(path).with_context(|| format!("read {}", path.display()))?;
    let geo: GeoJson = serde_json::from_slice(&bytes).context("parse source geojson")?;
    Ok(match geo {
        GeoJson::FeatureCollection(fc) => fc.features,
        GeoJson::Feature(f) => vec![f],
        _ => Vec::new(),
    })
}

fn feature_to_segment(f: &Feature, role: Option<RoadRole>) -> Option<RoadSegment> {
    let props = f.properties.as_ref()?;
    let id = props.get("id")?.as_str()?.to_string();
    let kind = props
        .get("kind")
        .and_then(|v| v.as_str())
        .unwrap_or("road")
        .to_string();
    let surface = props
        .get("surface")
        .and_then(|v| v.as_str())
        .unwrap_or("unknown")
        .to_string();
    let grade = props
        .get("grade")
        .and_then(|v| v.as_str())
        .unwrap_or("unknown")
        .to_string();
    let access = props
        .get("access")
        .and_then(|v| v.as_str())
        .unwrap_or("unknown")
        .to_string();
    let geom = f.geometry.as_ref()?;
    let coords = line_lat_lon(geom)?;
    if coords.len() < 2 {
        return None;
    }
    Some(RoadSegment {
        id,
        kind,
        surface,
        grade,
        access,
        role: role.unwrap_or_else(|| {
            props
                .get("role")
                .and_then(|v| v.as_str())
                .and_then(RoadRole::parse)
                .unwrap_or(RoadRole::Other)
        }),
        coords,
    })
}

fn line_lat_lon(geom: &Geometry) -> Option<Vec<(f64, f64)>> {
    match &geom.value {
        GjValue::LineString(ls) => Some(
            ls.iter()
                .filter(|p| p.len() >= 2)
                .map(|p| (p[1], p[0]))
                .collect(),
        ),
        GjValue::MultiLineString(mls) => {
            let mut longest = Vec::new();
            for part in mls {
                if part.len() >= 2 && part.len() > longest.len() {
                    longest = part
                        .iter()
                        .filter(|p| p.len() >= 2)
                        .map(|p| (p[1], p[0]))
                        .collect();
                }
            }
            if longest.len() >= 2 {
                Some(longest)
            } else {
                None
            }
        }
        _ => None,
    }
}

fn merge_layer_onto(base: &mut Vec<RoadSegment>, layer: Vec<RoadSegment>, source: &str) {
    crate::roads_merge::merge_layer(base, layer, source);
}

fn segments_from_geojson(path: &Path) -> Result<Vec<RoadSegment>> {
    let mut out = Vec::new();
    for f in read_geojson_features(path)? {
        if let Some(seg) = feature_to_segment(&f, None) {
            out.push(seg);
        }
    }
    Ok(out)
}

fn write_segments_geojson(path: &Path, segments: &[RoadSegment]) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let features: Vec<Feature> = segments
        .iter()
        .map(|s| {
            let coords: Vec<Vec<f64>> =
                s.coords.iter().map(|(lat, lon)| vec![*lon, *lat]).collect();
            let mut props = serde_json::Map::new();
            props.insert("id".into(), s.id.clone().into());
            props.insert("kind".into(), s.kind.clone().into());
            props.insert("surface".into(), s.surface.clone().into());
            props.insert("grade".into(), s.grade.clone().into());
            props.insert("access".into(), s.access.clone().into());
            props.insert("role".into(), s.role.as_str().into());
            Feature {
                bbox: None,
                geometry: Some(Geometry::new(GjValue::LineString(coords))),
                id: None,
                properties: Some(props),
                foreign_members: None,
            }
        })
        .collect();
    let fc = FeatureCollection {
        bbox: None,
        features,
        foreign_members: None,
    };
    fs::write(
        path,
        serde_json::to_string(&GeoJson::FeatureCollection(fc))?,
    )?;
    Ok(())
}

fn apply_match_rules(book: &RoadsBook, segments: &mut [RoadSegment]) {
    for seg in segments.iter_mut() {
        seg.role = book.resolve_role(&seg.id, &seg.kind, &seg.surface, &seg.grade, &seg.access);
    }
}

fn assign_missing_custom_ids(book: &mut RoadsBook) -> bool {
    let mut changed = false;
    for rule in book.rules.iter_mut() {
        if rule.coords.is_some() && rule.id.is_none() {
            rule.id = Some(format!(
                "custom-{}",
                std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .map(|d| d.as_millis())
                    .unwrap_or(0)
            ));
            changed = true;
        }
    }
    changed
}

fn internal_segments_from_rules(book: &RoadsBook) -> Vec<RoadSegment> {
    let mut out = Vec::new();
    for rule in book.coord_rules() {
        let Some(coords) = &rule.coords else { continue };
        if coords.len() < 2 {
            continue;
        }
        let id = rule.id.clone().unwrap_or_else(|| "custom-anon".to_string());
        let lat_lon: Vec<(f64, f64)> = coords.iter().map(|[lat, lon]| (*lat, *lon)).collect();
        out.push(RoadSegment {
            id,
            kind: String::new(),
            surface: String::new(),
            grade: String::new(),
            access: "unknown".into(),
            role: rule.role,
            coords: lat_lon,
        });
    }
    out
}

fn tracktype_from_grade(grade: &str) -> Option<String> {
    if grade == "unknown" || grade.is_empty() {
        None
    } else if grade.chars().all(|c| c.is_ascii_digit()) {
        Some(format!("grade{grade}"))
    } else {
        Some(grade.to_string())
    }
}

fn parse_osm_id(id: &str) -> Option<i64> {
    id.strip_prefix("osm:")?.parse().ok()
}

pub fn build_routing_from_master(
    segments: &[RoadSegment],
    west: f64,
    south: f64,
    east: f64,
    north: f64,
    opts: &OsmRoutingOpts,
) -> OsmRoutingBuild {
    use crate::osm::{
        sample_way, JeepRoadGraph, JeepRoadIndex, OsmRouting, PavedAnchorIndex, PavedPoint,
        RoadPoint,
    };

    let mut jeep_samples: Vec<RoadPoint> = Vec::new();
    let mut jeep_way_count = 0usize;
    let mut graph = JeepRoadGraph::new();
    let mut graph_node_map: HashMap<i64, u32> = HashMap::new();
    let mut weld = NodeWeld::default();
    let mut paved_points: Vec<PavedPoint> = Vec::new();
    let mut inventory_ways: Vec<OsmInventoryWay> = Vec::new();
    let mut segment_groups: Vec<RoadSegmentGroup> = Vec::new();
    let mut hiking_trail_lines: Vec<Vec<(f64, f64)>> = Vec::new();

    for seg in segments {
        if !segment_in_bbox(&seg.coords, west, south, east, north) {
            continue;
        }
        let tracktype = tracktype_from_grade(&seg.grade);
        let surface_opt = if seg.surface.is_empty() || seg.surface == "unknown" {
            None
        } else {
            Some(seg.surface.clone())
        };
        let inv_role = inventory_role_from_road(seg.role);
        let reason = if seg.role == RoadRole::Other {
            Some(seg.kind.clone())
        } else {
            None
        };
        let osm_id = parse_osm_id(&seg.id);
        inventory_ways.push(OsmInventoryWay {
            osm_id,
            coords: seg
                .coords
                .iter()
                .map(|(lat, lon)| (*lat as f32, *lon as f32))
                .collect(),
            highway: if seg.kind.is_empty() {
                "custom".into()
            } else {
                seg.kind.clone()
            },
            tracktype: tracktype.clone(),
            surface: surface_opt.clone(),
            fourwd_only: false,
            role: inv_role,
            reason: reason.clone(),
        });
        let group_id = segment_groups.len() as u32;
        let key = if let Some(oid) = osm_id {
            SegmentGroupKey::Osm { osm_id: oid }
        } else if seg.id.starts_with("custom-") || seg.kind.is_empty() {
            SegmentGroupKey::Custom { id: seg.id.clone() }
        } else if let Some(rest) = seg.id.split_once(':') {
            SegmentGroupKey::Gtlf {
                layer_id: rest.0.to_string(),
                coord_hash: rest.1.to_string(),
            }
        } else {
            SegmentGroupKey::Custom { id: seg.id.clone() }
        };
        segment_groups.push(RoadSegmentGroup {
            key,
            coords: seg.coords.clone(),
            tags: SegmentGroupTags {
                highway: if seg.kind.is_empty() {
                    "custom".into()
                } else {
                    seg.kind.clone()
                },
                tracktype,
                surface: surface_opt.clone(),
                fourwd_only: false,
            },
            role: seg.role,
            access: seg.access.clone(),
            reason,
        });
        let driveable = matches!(seg.role, RoadRole::Paved | RoadRole::Jeep);
        if !driveable {
            if seg.role == RoadRole::Hiking {
                hiking_trail_lines.push(seg.coords.clone());
            }
            continue;
        }
        if seg.role == RoadRole::Jeep {
            jeep_samples.extend(sample_way(&seg.coords, opts.road_sample_step_m));
            jeep_way_count += 1;
        }
        let mut vert_nodes = Vec::with_capacity(seg.coords.len());
        for &(lat, lon) in &seg.coords {
            vert_nodes.push(weld.node(&mut graph, &mut graph_node_map, lat, lon));
        }
        let anchor = seg.role == RoadRole::Paved;
        for i in 0..seg.coords.len() - 1 {
            let (lat1, lon1) = seg.coords[i];
            let (lat2, lon2) = seg.coords[i + 1];
            let a = vert_nodes[i];
            let b = vert_nodes[i + 1];
            if a == b {
                continue;
            }
            graph.add_way_segment_classified(
                a,
                b,
                lat1,
                lon1,
                lat2,
                lon2,
                &seg.kind,
                tracktype_from_grade(&seg.grade).as_deref(),
                surface_opt.as_deref(),
                false,
                seg.role,
                group_id,
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
    }

    let graph_nodes = GraphNodeIndex::from_graph(&graph);
    let routing = OsmRouting {
        graph,
        jeep_roads: JeepRoadIndex::from_samples(jeep_samples, jeep_way_count),
        paved: PavedAnchorIndex::from_points(paved_points),
        graph_nodes,
        segment_groups: segment_groups.clone(),
    };
    let in_bbox = inventory_ways.len();
    info!(
        segments_in_bbox = in_bbox,
        jeep_ways = jeep_way_count,
        graph_nodes = routing.graph.node_count(),
        jeep_samples = routing.jeep_roads.point_count,
        paved_anchors = routing.paved.anchor_count(),
        hiking_lines = hiking_trail_lines.len(),
        inventory_ways = inventory_ways.len(),
        west,
        south,
        east,
        north,
        "roads warm: build_routing_from_master done"
    );
    OsmRoutingBuild {
        routing,
        inventory: OsmRoadInventory {
            ways: inventory_ways,
        },
        hiking_trail_lines,
        segment_groups,
    }
}

/// Join vertices that land on the same junction so a jeep track connects to pavement.
const NODE_WELD_M: f64 = 8.0;

#[derive(Default)]
struct NodeWeld {
    cells: HashMap<(i32, i32), Vec<u32>>,
    next_id: i64,
}

impl NodeWeld {
    fn node(
        &mut self,
        graph: &mut crate::osm::JeepRoadGraph,
        ids: &mut HashMap<i64, u32>,
        lat: f64,
        lon: f64,
    ) -> u32 {
        if let Some(existing) = self.nearest(graph, lat, lon) {
            return existing;
        }
        self.next_id += 1;
        let idx = graph.get_or_insert_node_public(self.next_id, lat, lon, ids);
        let (cx, cy) = weld_cell(lat, lon);
        self.cells.entry((cx, cy)).or_default().push(idx);
        idx
    }

    fn nearest(&self, graph: &crate::osm::JeepRoadGraph, lat: f64, lon: f64) -> Option<u32> {
        let (cx, cy) = weld_cell(lat, lon);
        let mut best: Option<(u32, f64)> = None;
        for dx in -1..=1 {
            for dy in -1..=1 {
                let Some(ids) = self.cells.get(&(cx + dx, cy + dy)) else {
                    continue;
                };
                for &id in ids {
                    let (nlat, nlon) = graph.node_coords(id);
                    let dist = crate::hike::haversine_m(lat, lon, nlat, nlon);
                    if dist <= NODE_WELD_M && best.map(|(_, d)| dist < d).unwrap_or(true) {
                        best = Some((id, dist));
                    }
                }
            }
        }
        best.map(|(id, _)| id)
    }
}

fn weld_cell(lat: f64, lon: f64) -> (i32, i32) {
    let cos = lat.to_radians().cos().abs().max(0.2);
    let x = lon * 111_320.0 * cos;
    let y = lat * 111_320.0;
    (
        (x / NODE_WELD_M).floor() as i32,
        (y / NODE_WELD_M).floor() as i32,
    )
}

fn segment_in_bbox(coords: &[(f64, f64)], west: f64, south: f64, east: f64, north: f64) -> bool {
    coords
        .iter()
        .any(|(lat, lon)| *lon >= west && *lon <= east && *lat >= south && *lat <= north)
}

fn aoi_bounds(project_dir: &Path, book: &RoadsBook) -> Result<(f64, f64, f64, f64)> {
    let first = book
        .sources
        .values()
        .next()
        .context("roads.yaml has no sources")?;
    let aoi = first
        .get("aoi")
        .and_then(|v| v.as_str())
        .context("source missing aoi")?;
    let path = resolve_project_relative(project_dir, aoi)?;
    let bytes = fs::read(&path)?;
    let geo: GeoJson = serde_json::from_slice(&bytes)?;
    let mut west = f64::INFINITY;
    let mut south = f64::INFINITY;
    let mut east = f64::NEG_INFINITY;
    let mut north = f64::NEG_INFINITY;
    let mut absorb = |lon: f64, lat: f64| {
        west = west.min(lon);
        south = south.min(lat);
        east = east.max(lon);
        north = north.max(lat);
    };
    match geo {
        GeoJson::FeatureCollection(fc) => {
            for f in fc.features {
                if let Some(g) = f.geometry {
                    bbox_geom(&g, &mut absorb);
                }
            }
        }
        GeoJson::Feature(f) => {
            if let Some(g) = f.geometry {
                bbox_geom(&g, &mut absorb);
            }
        }
        _ => {}
    }
    if !west.is_finite() {
        anyhow::bail!("aoi has no bounds: {}", path.display());
    }
    Ok((west, south, east, north))
}

fn bbox_geom(g: &Geometry, absorb: &mut dyn FnMut(f64, f64)) {
    match &g.value {
        GjValue::Point(p) if p.len() >= 2 => absorb(p[0], p[1]),
        GjValue::LineString(ls) => {
            for p in ls {
                if p.len() >= 2 {
                    absorb(p[0], p[1]);
                }
            }
        }
        GjValue::Polygon(rings) => {
            for ring in rings {
                for p in ring {
                    if p.len() >= 2 {
                        absorb(p[0], p[1]);
                    }
                }
            }
        }
        GjValue::MultiPolygon(polys) => {
            for poly in polys {
                for ring in poly {
                    for p in ring {
                        if p.len() >= 2 {
                            absorb(p[0], p[1]);
                        }
                    }
                }
            }
        }
        _ => {}
    }
}

fn uv_on_path() -> bool {
    Command::new("uv")
        .arg("--version")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}

/// Prefer ``uv run --directory <project> python`` when the project has ``pyproject.toml``.
fn source_plugin_command(project_dir: &Path, source_key: &str) -> Result<(String, Vec<String>)> {
    let pyproject = project_dir.join("pyproject.toml");
    if pyproject.is_file() {
        if !uv_on_path() {
            anyhow::bail!(
                "project {} has pyproject.toml but `uv` is not on PATH (install uv: https://docs.astral.sh/uv/)",
                project_dir.display()
            );
        }
        let mut prefix = vec![
            "run".to_string(),
            "--directory".to_string(),
            project_dir.to_string_lossy().into_owned(),
            "--extra".to_string(),
            "land".to_string(),
        ];
        if source_key == "osm" {
            prefix.push("--extra".to_string());
            prefix.push("osm".to_string());
        }
        prefix.push("python".to_string());
        return Ok(("uv".to_string(), prefix));
    }
    Ok(("python3".to_string(), Vec::new()))
}

fn resolve_project_relative(project_dir: &Path, rel: &str) -> Result<PathBuf> {
    let direct = project_dir.join(rel);
    if direct.is_file() {
        return Ok(direct);
    }
    let hit = project_dir.join(rel.trim_start_matches("./"));
    if hit.is_file() {
        return Ok(hit);
    }
    anyhow::bail!("path not found under project: {rel}");
}

pub fn run_source_plugin(
    project_dir: &Path,
    source_key: &str,
    cache_key: &str,
    config_yaml: &str,
    force: bool,
) -> Result<PathBuf> {
    let cache_dir = layout_for(project_dir).roads_cache_dir();
    fs::create_dir_all(cache_dir.join("sources"))?;
    let out_path = source_geojson_path(&cache_dir, cache_key);
    if out_path.is_file() && !force {
        let fp = store_source_fingerprint(&out_path)?;
        info!(
            source = source_key,
            cache_key,
            path = %out_path.display(),
            bytes = path_byte_len(&out_path),
            fingerprint = %fp,
            "roads warm: source cache hit (skip plugin)"
        );
        return Ok(out_path);
    }
    let script = project_dir.join(format!("plugins/roads/sources/{source_key}.py"));
    if !script.is_file() {
        anyhow::bail!("missing source plugin {}", script.display());
    }
    let plugin_t0 = Instant::now();
    let (program, prefix) = source_plugin_command(project_dir, source_key)?;
    info!(
        source = source_key,
        cache_key,
        force,
        program,
        script = %script.display(),
        "roads warm: source plugin start"
    );
    let scratch = cache_dir
        .join("sources")
        .join(format!("{cache_key}.geojson.part"));
    if scratch.is_file() {
        fs::remove_file(&scratch).ok();
    }
    let mut cmd = Command::new(&program);
    for arg in &prefix {
        cmd.arg(arg);
    }
    let mut child = cmd
        .arg(&script)
        .arg(format!("--output={}", scratch.display()))
        .current_dir(project_dir)
        .stdin(Stdio::piped())
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit())
        .spawn()
        .with_context(|| format!("spawn source plugin ({program})"))?;
    child
        .stdin
        .take()
        .context("plugin stdin")?
        .write_all(config_yaml.as_bytes())
        .context("write plugin stdin")?;
    let status = child.wait().context("wait on source plugin")?;
    if !status.success() {
        fs::remove_file(&scratch).ok();
        anyhow::bail!(
            "source plugin {source_key} exited {}",
            status.code().unwrap_or(-1)
        );
    }
    if !scratch.is_file() {
        anyhow::bail!(
            "source plugin {source_key} did not write {}",
            scratch.display()
        );
    }
    fs::rename(&scratch, &out_path)
        .with_context(|| format!("install {} -> {}", scratch.display(), out_path.display()))?;
    let fp = store_source_fingerprint(&out_path)?;
    info!(
        source = source_key,
        cache_key,
        path = %out_path.display(),
        bytes = path_byte_len(&out_path),
        fingerprint = %fp,
        elapsed_secs = plugin_t0.elapsed().as_secs_f64(),
        "roads warm: source plugin done"
    );
    Ok(out_path)
}

/// Ensure each source GeoJSON is on disk and return `(cache_key, fingerprint)` in file order.
/// The fingerprint is a hash of those GeoJSON bytes. It does not mean the upstream feed is fresh.
pub fn update_all_sources(
    project_dir: &Path,
    book: &RoadsBook,
    fetch: SourceFetch,
) -> Result<Vec<(String, String)>> {
    let keys: Vec<String> = book.sources.keys().cloned().collect();
    let n = keys.len();
    let t0 = Instant::now();
    info!(
        sources = n,
        fetch = fetch_label(fetch),
        project = %project_dir.display(),
        "update-sources: start"
    );
    let cache_dir = layout_for(project_dir).roads_cache_dir();
    fs::create_dir_all(cache_dir.join("sources"))?;
    let mut seen = HashSet::new();
    let mut fps = Vec::with_capacity(n);
    for (i, key) in keys.iter().enumerate() {
        let cache_key = source_cache_key(book, key)?;
        if !seen.insert(cache_key.clone()) {
            anyhow::bail!("duplicate roads cache_key {cache_key}");
        }
        let step = format!("[{}/{n}]", i + 1);
        let path = match fetch {
            SourceFetch::NoFetch => {
                let path = source_geojson_path(&cache_dir, &cache_key);
                if !path.is_file() {
                    anyhow::bail!(
                        "road source {cache_key} has no cached GeoJSON at {}. Run update-sources without --no-fetch.",
                        path.display()
                    );
                }
                info!(source = %key, cache_key = %cache_key, step, "update-sources: use cache");
                path
            }
            SourceFetch::AssumeFresh => {
                info!(
                    source = %key,
                    cache_key = %cache_key,
                    step,
                    "update-sources: fetch if missing"
                );
                let yaml = source_config_yaml(book, key)?;
                run_source_plugin(project_dir, key, &cache_key, &yaml, false)?
            }
            SourceFetch::Refresh => {
                info!(source = %key, cache_key = %cache_key, step, "update-sources: plugin");
                let yaml = source_config_yaml(book, key)?;
                run_source_plugin(project_dir, key, &cache_key, &yaml, true)?
            }
        };
        let fp = read_source_fingerprint(&path)?;
        info!(
            source = %key,
            cache_key = %cache_key,
            path = %path.display(),
            bytes = path_byte_len(&path),
            fingerprint = %fp,
            "update-sources: cached"
        );
        fps.push((cache_key, fp));
    }
    info!(
        sources = n,
        elapsed_secs = t0.elapsed().as_secs_f64(),
        "update-sources: sources ready"
    );
    Ok(fps)
}

fn fetch_label(fetch: SourceFetch) -> &'static str {
    match fetch {
        SourceFetch::Refresh => "refresh",
        SourceFetch::AssumeFresh => "assume-fresh",
        SourceFetch::NoFetch => "no-fetch",
    }
}

fn read_source_fingerprint(path: &Path) -> Result<String> {
    let side = source_fingerprint_path(path);
    if side.is_file() {
        let text = fs::read_to_string(&side)?;
        let fp = text.trim();
        if !fp.is_empty() {
            return Ok(fp.to_string());
        }
    }
    store_source_fingerprint(path)
}

fn source_set_fingerprint(fps: &[(String, String)]) -> String {
    let mut bytes = Vec::new();
    bytes.extend(BASE_CACHE_VERSION.to_le_bytes());
    for (key, fp) in fps {
        bytes.extend(key.as_bytes());
        bytes.push(0);
        bytes.extend(fp.as_bytes());
        bytes.push(0);
    }
    bytes_fingerprint(&bytes)
}

fn master_cache_paths(cache_dir: &Path) -> (PathBuf, PathBuf) {
    (
        cache_dir.join("master.meta.json"),
        cache_dir.join("master.segments.json"),
    )
}

pub fn update_roads(config_path: &Path, fetch: SourceFetch) -> Result<()> {
    let layout = ProjectLayout::from_config_path(config_path)?;
    let project_dir = &layout.project_dir;
    let roads_path = layout.roads_path();
    let t0 = Instant::now();
    info!(
        project = %project_dir.display(),
        roads_yaml = %roads_path.display(),
        fetch = fetch_label(fetch),
        "roads warm: update_roads start"
    );
    let mut book = load_roads_book(&roads_path)?;
    if !book.has_unified_sources() {
        info!("roads warm: update_roads skip (no unified sources in roads.yaml)");
        return Ok(());
    }
    info!(
        sources = book.sources.len(),
        rules = book.rules.len(),
        "roads warm: loaded roads book"
    );
    if assign_missing_custom_ids(&mut book) {
        save_roads_book(&roads_path, &book)?;
        info!("roads warm: assigned missing custom road ids in roads.yaml");
    }
    let cache_dir = layout.roads_cache_dir();
    let source_fps = update_all_sources(project_dir, &book, fetch)?;
    let base_fp = source_set_fingerprint(&source_fps);
    let internal_fp = book.coords_rules_fingerprint();
    let match_fp = book.match_rules_fingerprint();
    let master_fp = format!("{base_fp}-{internal_fp}-{match_fp}");
    let (meta_path, segments_path) = master_cache_paths(&cache_dir);
    if master_cache_fresh(&meta_path, &segments_path, &master_fp)? {
        info!(
            master_fp = %master_fp,
            master_path = %segments_path.display(),
            elapsed_secs = t0.elapsed().as_secs_f64(),
            "roads warm: master cache hit"
        );
        return Ok(());
    }

    let base = load_or_build_base(&cache_dir, &book, &source_fps, &base_fp)?;
    let internal = internal_segments_from_rules(&book);
    info!(
        custom_segments = internal.len(),
        internal_fp = %internal_fp,
        match_rules_fp = %match_fp,
        "roads warm: apply custom roads and match rules"
    );
    let master_t0 = Instant::now();
    let mut master = base;
    merge_layer_onto(&mut master, internal, "custom");
    apply_match_rules(&book, &mut master);
    info!(
        master_segments = master.len(),
        elapsed_secs = master_t0.elapsed().as_secs_f64(),
        "roads warm: master graph assembled"
    );
    let write_t0 = Instant::now();
    write_segments_geojson(&segments_path, &master)?;
    let meta = MasterCacheMeta {
        version: MASTER_CACHE_VERSION,
        source_fps,
        base_fp,
        internal_fp,
        match_rules_fp: match_fp,
        master_fp,
    };
    fs::write(&meta_path, serde_json::to_string_pretty(&meta)?)?;
    info!(
        master_path = %segments_path.display(),
        bytes = path_byte_len(&segments_path),
        master_fp = %meta.master_fp,
        elapsed_secs = write_t0.elapsed().as_secs_f64(),
        total_secs = t0.elapsed().as_secs_f64(),
        "roads warm: wrote master graph"
    );
    Ok(())
}

fn master_cache_fresh(meta_path: &Path, segments_path: &Path, master_fp: &str) -> Result<bool> {
    if !meta_path.is_file() || !segments_path.is_file() {
        return Ok(false);
    }
    let meta = read_master_meta(meta_path)?;
    Ok(meta.version == MASTER_CACHE_VERSION && meta.master_fp == master_fp)
}

#[derive(Debug, serde::Serialize, serde::Deserialize)]
struct BaseCacheMeta {
    version: u32,
    source_fps: Vec<(String, String)>,
    base_fp: String,
}

fn base_cache_paths(cache_dir: &Path) -> (PathBuf, PathBuf) {
    (
        cache_dir.join("base.meta.json"),
        cache_dir.join("base.segments.json"),
    )
}

fn load_or_build_base(
    cache_dir: &Path,
    book: &RoadsBook,
    source_fps: &[(String, String)],
    base_fp: &str,
) -> Result<Vec<RoadSegment>> {
    let (meta_path, segments_path) = base_cache_paths(cache_dir);
    if meta_path.is_file() && segments_path.is_file() {
        let meta: BaseCacheMeta = serde_json::from_slice(&fs::read(&meta_path)?)?;
        if meta.version == BASE_CACHE_VERSION
            && meta.base_fp == base_fp
            && meta.source_fps == source_fps
        {
            let t0 = Instant::now();
            let base = segments_from_geojson(&segments_path)?;
            info!(
                segments = base.len(),
                base_fp = %base_fp,
                elapsed_secs = t0.elapsed().as_secs_f64(),
                "roads warm: base cache hit"
            );
            return Ok(base);
        }
    }

    let mut base = Vec::new();
    let n = book.sources.len();
    for (i, key) in book.sources.keys().enumerate() {
        let cache_key = source_cache_key(book, key)?;
        let path = source_geojson_path(cache_dir, &cache_key);
        info!(
            source = %key,
            step = format!("[{}/{n}]", i + 1),
            path = %path.display(),
            "roads warm: load source into base"
        );
        let layer = segments_from_geojson(&path)?;
        let loaded = layer.len();
        merge_layer_onto(&mut base, layer, &cache_key);
        info!(
            source = %key,
            segments_loaded = loaded,
            base_segments = base.len(),
            "roads warm: source merged"
        );
    }
    write_segments_geojson(&segments_path, &base)?;
    let meta = BaseCacheMeta {
        version: BASE_CACHE_VERSION,
        source_fps: source_fps.to_vec(),
        base_fp: base_fp.to_string(),
    };
    fs::write(&meta_path, serde_json::to_string_pretty(&meta)?)?;
    info!(
        base_segments = base.len(),
        base_fp = %base_fp,
        path = %segments_path.display(),
        bytes = path_byte_len(&segments_path),
        "roads warm: wrote base graph"
    );
    Ok(base)
}

fn read_master_meta(path: &Path) -> Result<MasterCacheMeta> {
    let bytes = fs::read(path)?;
    Ok(serde_json::from_slice(&bytes)?)
}

pub fn load_master_segments(project_dir: &Path) -> Result<Vec<RoadSegment>> {
    let (_, segments_path) = master_cache_paths(&layout_for(project_dir).roads_cache_dir());
    segments_from_geojson(&segments_path)
}

pub fn build_project_routing_from_master(
    project_dir: &Path,
    west: f64,
    south: f64,
    east: f64,
    north: f64,
    opts: &OsmRoutingOpts,
) -> Result<crate::access_warm::ProjectRouting> {
    use crate::access_warm::ProjectRouting;

    let t0 = Instant::now();
    let config_path = project_dir.join("config.yaml");
    let book = ensure_roads_book(&config_path)?;
    if !book.has_unified_sources() {
        anyhow::bail!("project has no unified road sources");
    }
    let load_t0 = Instant::now();
    let segments = load_master_segments(project_dir)?;
    info!(
        master_segments = segments.len(),
        elapsed_secs = load_t0.elapsed().as_secs_f64(),
        "roads warm: loaded master segments from disk"
    );
    let pad = crate::osm::OSM_BUILD_PAD_DEG;
    info!(
        request_west = west,
        request_south = south,
        request_east = east,
        request_north = north,
        pad_deg = pad,
        build_west = west - pad,
        build_south = south - pad,
        build_east = east + pad,
        build_north = north + pad,
        road_sample_step_m = opts.road_sample_step_m,
        "roads warm: build_project_routing_from_master start"
    );
    let build_t0 = Instant::now();
    let build = build_routing_from_master(
        &segments,
        west - pad,
        south - pad,
        east + pad,
        north + pad,
        opts,
    );
    info!(
        elapsed_secs = build_t0.elapsed().as_secs_f64(),
        "roads warm: jeep/paved graph build finished"
    );
    let trail_t0 = Instant::now();
    let trails = build_trail_graph(&build.hiking_trail_lines, opts.road_sample_step_m);
    info!(
        trail_graph_nodes = trails.graph.node_count(),
        elapsed_secs = trail_t0.elapsed().as_secs_f64(),
        "roads warm: hiking trail graph built"
    );
    let customs = book.customs();
    info!(
        custom_roads = customs.len(),
        "roads warm: finalize ProjectRouting"
    );
    let routing =
        ProjectRouting::from_unified_graph(build.routing, trails, build.inventory, customs);
    info!(
        elapsed_secs = t0.elapsed().as_secs_f64(),
        "roads warm: build_project_routing_from_master done"
    );
    Ok(routing)
}

pub fn patch_preserves_trusted_reach(prev: &RoadsBook, next: &RoadsBook) -> bool {
    prev.match_rules_fingerprint() == next.match_rules_fingerprint()
        && prev.coords_rules_fingerprint() == next.coords_rules_fingerprint()
}

#[cfg(test)]
mod tests {
    use super::*;

    const DEMO_GEOJSON: &[u8] = b"{\"type\":\"FeatureCollection\",\"features\":[]}\n";

    fn demo_project(dir: &Path, script: &str) {
        fs::create_dir_all(dir.join("plugins/roads/sources")).unwrap();
        fs::write(
            dir.join("roads.yaml"),
            "sources:\n  demo:\n    cache_key: demo-cache\n    aoi: aoi.geojson\n",
        )
        .unwrap();
        fs::write(dir.join("plugins/roads/sources/demo.py"), script).unwrap();
    }

    fn load_demo(dir: &Path) -> RoadsBook {
        load_roads_book(&dir.join("roads.yaml")).unwrap()
    }

    #[test]
    fn identical_geojson_bytes_share_a_fingerprint() {
        let again = DEMO_GEOJSON.to_vec();
        assert_eq!(bytes_fingerprint(DEMO_GEOJSON), bytes_fingerprint(&again));
        assert_ne!(
            bytes_fingerprint(DEMO_GEOJSON),
            bytes_fingerprint(b"{\"type\":\"FeatureCollection\",\"features\":[1]}\n")
        );
    }

    #[test]
    fn update_sources_always_reruns_and_caches_by_cache_key() {
        let dir = tempfile::tempdir().unwrap();
        let project = dir.path();
        demo_project(
            project,
            r#"import sys
from pathlib import Path
sys.stdin.read()
out = None
for arg in sys.argv[1:]:
    if arg.startswith("--output="):
        out = Path(arg.split("=", 1)[1])
if out is None:
    raise SystemExit("missing --output")
out.write_bytes(b'{"type":"FeatureCollection","features":[]}\n')
counter = Path("plugin-runs.txt")
n = int(counter.read_text()) if counter.exists() else 0
counter.write_text(str(n + 1))
"#,
        );
        let book = load_demo(project);
        update_all_sources(project, &book, SourceFetch::Refresh).unwrap();
        update_all_sources(project, &book, SourceFetch::Refresh).unwrap();
        let geo_path = project.join(".peaky/cache/roads/sources/demo-cache.geojson");
        let geo = fs::read(&geo_path).unwrap();
        assert_eq!(geo, DEMO_GEOJSON);
        assert!(!project
            .join(".peaky/cache/roads/sources/demo.geojson")
            .is_file());
        let fp = fs::read_to_string(geo_path.with_extension("fingerprint")).unwrap();
        assert_eq!(fp.trim(), bytes_fingerprint(&geo));
        assert_eq!(
            fs::read_to_string(project.join("plugin-runs.txt")).unwrap(),
            "2"
        );
    }

    #[test]
    fn failed_plugin_leaves_no_partial_cache() {
        let dir = tempfile::tempdir().unwrap();
        let project = dir.path();
        demo_project(
            project,
            "import sys\nsys.stdin.read()\nraise SystemExit(1)\n",
        );
        let err =
            update_all_sources(project, &load_demo(project), SourceFetch::Refresh).unwrap_err();
        let msg = format!("{err:#}");
        assert!(msg.contains("exited"), "{msg}");
        let sources = project.join(".peaky/cache/roads/sources");
        assert!(!sources.join("demo-cache.geojson").is_file());
        assert!(!sources.join("demo-cache.geojson.part").is_file());
        assert!(!sources.join("demo-cache.fingerprint").is_file());
    }

    #[test]
    fn cache_key_rejects_a_path() {
        let dir = tempfile::tempdir().unwrap();
        let project = dir.path();
        fs::create_dir_all(project).unwrap();
        fs::write(
            project.join("roads.yaml"),
            "sources:\n  demo:\n    cache_key: ../demo\n    aoi: aoi.geojson\n",
        )
        .unwrap();
        let err =
            update_all_sources(project, &load_demo(project), SourceFetch::Refresh).unwrap_err();
        assert!(format!("{err:#}").contains("single path segment"));
    }

    #[test]
    fn assume_fresh_skips_a_present_plugin() {
        let dir = tempfile::tempdir().unwrap();
        let project = dir.path();
        demo_project(
            project,
            "import sys\nsys.stdin.read()\nraise SystemExit('plugin should not run')\n",
        );
        let cache = project.join(".peaky/cache/roads/sources");
        fs::create_dir_all(&cache).unwrap();
        fs::write(cache.join("demo-cache.geojson"), DEMO_GEOJSON).unwrap();
        update_all_sources(project, &load_demo(project), SourceFetch::AssumeFresh).unwrap();
        assert!(!project.join("plugin-runs.txt").exists());
    }

    #[test]
    fn no_fetch_errors_when_the_cache_is_missing() {
        let dir = tempfile::tempdir().unwrap();
        let project = dir.path();
        demo_project(project, "raise SystemExit('plugin should not run')\n");
        let err =
            update_all_sources(project, &load_demo(project), SourceFetch::NoFetch).unwrap_err();
        assert!(format!("{err:#}").contains("no cached GeoJSON"), "{err:#}");
    }

    #[test]
    fn no_fetch_builds_a_master_graph_from_the_cache() {
        let dir = tempfile::tempdir().unwrap();
        let project = dir.path();
        fs::write(project.join("config.yaml"), "sites: {}\n").unwrap();
        fs::write(
            project.join("roads.yaml"),
            "sources:\n  demo:\n    cache_key: demo-cache\n    aoi: aoi.geojson\nrules:\n  - match: { kind: motorway, surface: paved }\n    role: paved\n",
        )
        .unwrap();
        let cache = project.join(".peaky/cache/roads/sources");
        fs::create_dir_all(&cache).unwrap();
        fs::write(
            cache.join("demo-cache.geojson"),
            r#"{"type":"FeatureCollection","features":[{"type":"Feature","properties":{"id":"osm:1","kind":"motorway","surface":"paved","grade":"unknown"},"geometry":{"type":"LineString","coordinates":[[-119.8,39.5],[-119.79,39.5]]}}]}"#,
        )
        .unwrap();
        update_roads(&project.join("config.yaml"), SourceFetch::NoFetch).unwrap();
        let master =
            fs::read_to_string(project.join(".peaky/cache/roads/master.segments.json")).unwrap();
        assert!(master.contains("\"role\":\"paved\""), "{master}");
        assert!(project
            .join(".peaky/cache/roads/base.segments.json")
            .is_file());
        update_roads(&project.join("config.yaml"), SourceFetch::NoFetch).unwrap();
    }
}
