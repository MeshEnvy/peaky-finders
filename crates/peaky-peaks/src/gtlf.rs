//! BLM GTLF fetch, cache, and merge with OSM jeep + foot-trail graphs.

use std::collections::{hash_map::DefaultHasher, HashMap};
use std::hash::{Hash, Hasher};
use std::path::{Path, PathBuf};
use std::time::{Instant, SystemTime, UNIX_EPOCH};

use anyhow::{Context, Result};
use geojson::{Feature, GeoJson, Geometry, Value as GeoValue};
use peaky_geo::{download_featureserver_geojson_bbox, load_preset_aoi_union, LonLatBBox};
use peaky_preset::{load_preset, GtlfSourceConfig, RoadsBook};

use crate::osm::OsmRoutingOpts;
use tracing::info;

use crate::osm::{merge_supplemental_jeep_lines_progress, OsmRouting, SupplementalLine};
use crate::road_inventory::{GtlfInventoryLine, GtlfRoadInventory, InventoryRole};
use crate::trail::{build_trail_graph, TrailRouting, TRAIL_MAX_M};
use crate::OSM_BUILD_PAD_DEG;

const GTLF_CACHE_DIR: &str = ".peaky/cache/roads";
const GTLF_REFRESH_DAYS: u64 = 90;

pub struct GtlfBundle {
    pub foot_trails: TrailRouting,
    pub fingerprint: String,
}

impl GtlfBundle {
    pub fn empty() -> Self {
        Self {
            foot_trails: TrailRouting::empty(),
            fingerprint: "disabled".to_string(),
        }
    }
}

pub fn gtlf_cache_dir(project_dir: &Path) -> PathBuf {
    project_dir.join(GTLF_CACHE_DIR)
}

fn layer_cache_name(layer_url: &str) -> String {
    let id = layer_url
        .trim_end_matches('/')
        .rsplit('/')
        .next()
        .unwrap_or("0");
    format!("gtlf-layer-{id}.geojson")
}

fn file_age_days(path: &Path) -> Option<u64> {
    let meta = std::fs::metadata(path).ok()?;
    let modified = meta.modified().ok()?;
    let age = SystemTime::now().duration_since(modified).ok()?;
    Some(age.as_secs() / 86_400)
}

fn hash_file(path: &Path) -> Option<u64> {
    let meta = std::fs::metadata(path).ok()?;
    let mtime = meta
        .modified()
        .ok()
        .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let mut hasher = DefaultHasher::new();
    meta.len().hash(&mut hasher);
    mtime.hash(&mut hasher);
    path.file_name().hash(&mut hasher);
    Some(hasher.finish())
}

pub fn gtlf_cache_fingerprint(project_dir: &Path, gtlf: &GtlfSourceConfig) -> String {
    if !gtlf.enabled {
        return "disabled".to_string();
    }
    let dir = gtlf_cache_dir(project_dir);
    let mut parts: Vec<u64> = Vec::new();
    for url in gtlf.jeep_layers.iter().chain(gtlf.foot_layers.iter()) {
        let path = dir.join(layer_cache_name(url));
        if let Some(h) = hash_file(&path) {
            parts.push(h);
        }
    }
    parts.sort_unstable();
    let mut hasher = DefaultHasher::new();
    for p in parts {
        p.hash(&mut hasher);
    }
    format!("{:016x}", hasher.finish())
}

fn project_gtlf_bbox(project_dir: &Path, pad_deg: f64) -> Result<LonLatBBox> {
    let preset_path = project_dir.join("config.yaml");
    let geom = load_preset_aoi_union(&preset_path)?
        .context("GTLF fetch requires project AOI in land.yaml")?;
    LonLatBBox::from_geometry(&geom)
        .context("AOI geometry has no bounding box")
        .map(|b| b.padded(pad_deg))
}

fn ensure_layer_cached(
    project_dir: &Path,
    layer_url: &str,
    bbox: LonLatBBox,
    force: bool,
) -> Result<PathBuf> {
    let dir = gtlf_cache_dir(project_dir);
    std::fs::create_dir_all(&dir).context("create gtlf cache dir")?;
    let dest = dir.join(layer_cache_name(layer_url));
    let stale = file_age_days(&dest).unwrap_or(u64::MAX) >= GTLF_REFRESH_DAYS;
    if dest.is_file() && !force && !stale {
        info!(
            layer = layer_url,
            path = %dest.display(),
            "gtlf: cache hit"
        );
        return Ok(dest);
    }
    info!(
        layer = layer_url,
        path = %dest.display(),
        "gtlf: downloading bbox clip…"
    );
    download_featureserver_geojson_bbox(
        "gtlf",
        &dest.to_string_lossy(),
        layer_url,
        bbox.west,
        bbox.south,
        bbox.east,
        bbox.north,
        &dest,
        false,
    )?;
    Ok(dest)
}

pub fn ensure_gtlf_cache(project_dir: &Path, force: bool) -> Result<()> {
    let preset_path = project_dir.join("config.yaml");
    let preset = load_preset(&preset_path)?;
    if !preset.sources.gtlf.enabled {
        return Ok(());
    }
    let pad = OSM_BUILD_PAD_DEG + 0.5;
    let bbox = project_gtlf_bbox(project_dir, pad)?;
    for url in preset
        .sources
        .gtlf
        .jeep_layers
        .iter()
        .chain(preset.sources.gtlf.foot_layers.iter())
    {
        ensure_layer_cached(project_dir, url, bbox, force)?;
    }
    Ok(())
}

fn coords_in_bbox(coords: &[(f64, f64)], west: f64, south: f64, east: f64, north: f64) -> bool {
    coords
        .iter()
        .any(|&(lat, lon)| lon >= west && lon <= east && lat >= south && lat <= north)
}

fn extract_line_coords(geom: &Geometry) -> Vec<(f64, f64)> {
    match &geom.value {
        GeoValue::LineString(ls) => ls
            .iter()
            .filter_map(|pos| {
                if pos.len() >= 2 {
                    Some((pos[1], pos[0]))
                } else {
                    None
                }
            })
            .collect(),
        GeoValue::MultiLineString(mls) => mls
            .iter()
            .flat_map(|ls| {
                ls.iter().filter_map(|pos| {
                    if pos.len() >= 2 {
                        Some((pos[1], pos[0]))
                    } else {
                        None
                    }
                })
            })
            .collect(),
        _ => Vec::new(),
    }
}

fn parse_geojson_lines(path: &Path) -> Result<Vec<Vec<(f64, f64)>>> {
    let bytes = std::fs::read(path).with_context(|| format!("read {}", path.display()))?;
    let geo: GeoJson = serde_json::from_slice(&bytes).context("parse gtlf geojson")?;
    let features = match geo {
        GeoJson::FeatureCollection(fc) => fc.features,
        GeoJson::Feature(f) => vec![f],
        _ => Vec::new(),
    };
    Ok(features
        .into_iter()
        .filter_map(|f| f.geometry)
        .map(|g| extract_line_coords(&g))
        .filter(|c| c.len() >= 2)
        .collect())
}

fn gtlf_highway_from_props(
    props: &serde_json::Map<String, serde_json::Value>,
) -> (String, Option<String>) {
    let class = props
        .get("PLAN_ASSET_CLASS")
        .or_else(|| props.get("plan_asset_class"))
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let upper = class.to_ascii_uppercase();
    if upper.contains("PRIMITIVE") || upper.contains("TRAIL") {
        ("track".to_string(), Some("grade4".to_string()))
    } else {
        ("unclassified".to_string(), None)
    }
}

fn load_jeep_lines(
    project_dir: &Path,
    gtlf: &GtlfSourceConfig,
    roads: &RoadsBook,
    bbox: LonLatBBox,
) -> Result<Vec<SupplementalLine>> {
    let mut out = Vec::new();
    for url in &gtlf.jeep_layers {
        let path = gtlf_cache_dir(project_dir).join(layer_cache_name(url));
        if !path.is_file() {
            continue;
        }
        let layer_id = url.trim_end_matches('/').rsplit('/').next().unwrap_or("?");
        let t0 = Instant::now();
        info!(layer = layer_id, path = %path.display(), "gtlf: reading jeep layer");
        let bytes = std::fs::read(&path)?;
        let geo: GeoJson = serde_json::from_slice(&bytes)
            .with_context(|| format!("parse gtlf jeep layer {layer_id}"))?;
        let features = match geo {
            GeoJson::FeatureCollection(fc) => fc.features,
            GeoJson::Feature(f) => vec![f],
            _ => continue,
        };
        let before = out.len();
        for f in features {
            let Some(geom) = f.geometry else { continue };
            let coords = extract_line_coords(&geom);
            if coords.len() < 2 {
                continue;
            }
            if !coords_in_bbox(&coords, bbox.west, bbox.south, bbox.east, bbox.north) {
                continue;
            }
            let (highway, tracktype) = f
                .properties
                .as_ref()
                .map(|p| gtlf_highway_from_props(p))
                .unwrap_or(("track".to_string(), None));
            let role = roads.classify_gtlf_line(layer_id, &coords, false);
            out.push(SupplementalLine {
                coords,
                highway,
                tracktype,
                role,
                group_id: u32::MAX,
                gtlf_layer: Some(layer_id.to_string()),
            });
        }
        info!(
            layer = layer_id,
            lines = out.len() - before,
            total = out.len(),
            elapsed_secs = t0.elapsed().as_secs_f64(),
            "gtlf: jeep layer loaded"
        );
    }
    Ok(out)
}

fn load_foot_lines(
    project_dir: &Path,
    gtlf: &GtlfSourceConfig,
    bbox: LonLatBBox,
) -> Result<Vec<Vec<(f64, f64)>>> {
    let mut out = Vec::new();
    for url in &gtlf.foot_layers {
        let path = gtlf_cache_dir(project_dir).join(layer_cache_name(url));
        if !path.is_file() {
            continue;
        }
        let layer_id = url.trim_end_matches('/').rsplit('/').next().unwrap_or("?");
        let t0 = Instant::now();
        info!(layer = layer_id, path = %path.display(), "gtlf: reading foot layer");
        let before = out.len();
        for coords in parse_geojson_lines(&path)? {
            if coords_in_bbox(&coords, bbox.west, bbox.south, bbox.east, bbox.north) {
                out.push(coords);
            }
        }
        info!(
            layer = layer_id,
            lines = out.len() - before,
            total = out.len(),
            elapsed_secs = t0.elapsed().as_secs_f64(),
            "gtlf: foot layer loaded"
        );
    }
    Ok(out)
}

/// Load GTLF foot trails and merge jeep lines into ``routing`` for the given bbox.
pub fn apply_gtlf_to_routing(
    project_dir: &Path,
    routing: &mut OsmRouting,
    opts: &OsmRoutingOpts,
    west: f64,
    south: f64,
    east: f64,
    north: f64,
    force_fetch: bool,
) -> Result<GtlfBundle> {
    apply_gtlf_to_routing_progress(
        project_dir,
        routing,
        opts,
        west,
        south,
        east,
        north,
        force_fetch,
        false,
        &[],
        &mut |_| {},
    )
}

/// Same as [`apply_gtlf_to_routing`]. ``skip_jeep_merge`` loads foot trails only
/// (jeep graph already came from the merged-routing cache).
pub fn apply_gtlf_to_routing_progress<F>(
    project_dir: &Path,
    routing: &mut OsmRouting,
    opts: &OsmRoutingOpts,
    west: f64,
    south: f64,
    east: f64,
    north: f64,
    force_fetch: bool,
    skip_jeep_merge: bool,
    extra_foot_lines: &[Vec<(f64, f64)>],
    progress: &mut F,
) -> Result<GtlfBundle>
where
    F: FnMut(f64),
{
    let preset_path = project_dir.join("config.yaml");
    let preset = load_preset(&preset_path)?;
    let gtlf = preset.sources.gtlf.clone();
    if !gtlf.enabled {
        progress(1.0);
        return Ok(GtlfBundle::empty());
    }
    ensure_gtlf_cache(project_dir, force_fetch)?;
    let fingerprint = gtlf_cache_fingerprint(project_dir, &gtlf);
    if !skip_jeep_merge {
        info!(fingerprint = %fingerprint, "gtlf: loading jeep lines");
        progress(0.04);
        let roads = peaky_preset::ensure_roads_book(&preset_path)?;
        let jeep_lines = load_jeep_lines(
            project_dir,
            &gtlf,
            &roads,
            LonLatBBox::new(west, south, east, north),
        )?;
        info!(
            lines = jeep_lines.len(),
            jeep_ways = routing.jeep_roads.way_count,
            "gtlf: merging jeep lines into OSM graph"
        );
        let t_merge = Instant::now();
        merge_supplemental_jeep_lines_progress(
            routing,
            &jeep_lines,
            opts.road_sample_step_m,
            &mut |frac| progress(0.08 + 0.84 * frac.clamp(0.0, 1.0)),
        );
        info!(
            jeep_ways = routing.jeep_roads.way_count,
            elapsed_secs = t_merge.elapsed().as_secs_f64(),
            "gtlf: jeep merge done"
        );
    } else {
        info!(fingerprint = %fingerprint, "gtlf: jeep merge skipped (merged cache)");
        progress(0.92);
    }
    info!("gtlf: loading foot trails");
    let mut foot_lines = load_foot_lines(
        project_dir,
        &gtlf,
        LonLatBBox::new(west, south, east, north),
    )?;
    foot_lines.extend(extra_foot_lines.iter().cloned());
    let t_trails = Instant::now();
    let foot_trails = build_trail_graph(&foot_lines, opts.road_sample_step_m);
    info!(
        lines = foot_lines.len(),
        ways = foot_trails.samples.way_count,
        elapsed_secs = t_trails.elapsed().as_secs_f64(),
        "gtlf: foot trail graph ready"
    );
    let _ = TRAIL_MAX_M;
    progress(1.0);
    Ok(GtlfBundle {
        foot_trails,
        fingerprint,
    })
}

fn string_props(props: &serde_json::Map<String, serde_json::Value>) -> HashMap<String, String> {
    let mut out = HashMap::new();
    for (k, v) in props {
        if let Some(s) = v.as_str() {
            out.insert(k.clone(), s.to_string());
        } else if !v.is_null() {
            out.insert(k.clone(), v.to_string());
        }
    }
    out
}

/// Cached GTLF jeep + foot lines for the map "All" overlay (full properties).
pub fn load_gtlf_display_inventory(
    project_dir: &Path,
    west: f64,
    south: f64,
    east: f64,
    north: f64,
    roads: &RoadsBook,
) -> Result<GtlfRoadInventory> {
    let preset = load_preset(&project_dir.join("config.yaml"))?;
    let gtlf = preset.sources.gtlf.clone();
    if !gtlf.enabled {
        return Ok(GtlfRoadInventory::default());
    }
    let mut lines = Vec::new();
    for url in gtlf.jeep_layers.iter().chain(gtlf.foot_layers.iter()) {
        let path = gtlf_cache_dir(project_dir).join(layer_cache_name(url));
        if !path.is_file() {
            continue;
        }
        let layer_id = url
            .trim_end_matches('/')
            .rsplit('/')
            .next()
            .unwrap_or("?")
            .to_string();
        let foot_layer = gtlf.foot_layers.iter().any(|u| u == url);
        let bytes = std::fs::read(&path)?;
        let geo: GeoJson = serde_json::from_slice(&bytes)
            .with_context(|| format!("parse gtlf display layer {layer_id}"))?;
        let features = match geo {
            GeoJson::FeatureCollection(fc) => fc.features,
            GeoJson::Feature(f) => vec![f],
            _ => continue,
        };
        for f in features {
            let Some(geom) = f.geometry else { continue };
            let coords = extract_line_coords(&geom);
            if coords.len() < 2 {
                continue;
            }
            if !coords_in_bbox(&coords, west, south, east, north) {
                continue;
            }
            let road_role = roads.classify_gtlf_line(&layer_id, &coords, foot_layer);
            let role = crate::road_inventory::inventory_role_from_road(road_role);
            let props = f.properties.as_ref().map(string_props).unwrap_or_default();
            lines.push(GtlfInventoryLine {
                coords,
                role,
                layer_id: layer_id.clone(),
                props,
            });
        }
    }
    Ok(GtlfRoadInventory { lines })
}

pub fn read_gtlf_inventory_geojson(project_dir: &Path, foot: bool) -> Result<GeoJson> {
    let preset = load_preset(&project_dir.join("config.yaml"))?;
    if !preset.sources.gtlf.enabled {
        return Ok(GeoJson::FeatureCollection(geojson::FeatureCollection {
            bbox: None,
            features: Vec::new(),
            foreign_members: None,
        }));
    }
    let layers = if foot {
        &preset.sources.gtlf.foot_layers
    } else {
        &preset.sources.gtlf.jeep_layers
    };
    let mut features: Vec<Feature> = Vec::new();
    for url in layers {
        let path = gtlf_cache_dir(project_dir).join(layer_cache_name(url));
        if !path.is_file() {
            continue;
        }
        let bytes = std::fs::read(&path)?;
        if let GeoJson::FeatureCollection(fc) = serde_json::from_slice::<GeoJson>(&bytes)? {
            features.extend(fc.features);
        }
    }
    Ok(GeoJson::FeatureCollection(geojson::FeatureCollection {
        bbox: None,
        features,
        foreign_members: None,
    }))
}
