//! BLM GTLF fetch, cache, and merge with OSM jeep + foot-trail graphs.

use std::collections::hash_map::DefaultHasher;
use std::hash::{Hash, Hasher};
use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use anyhow::{Context, Result};
use geojson::{Feature, GeoJson, Geometry, Value as GeoValue};
use peaky_geo::{download_featureserver_geojson_bbox, load_preset_aoi_union, LonLatBBox};
use peaky_preset::{load_preset, GtlfSourceConfig};

use crate::osm::OsmRoutingOpts;
use tracing::info;

use crate::osm::{merge_supplemental_jeep_lines, OsmRouting, SupplementalLine};
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
    let id = layer_url.trim_end_matches('/').rsplit('/').next().unwrap_or("0");
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
    coords.iter().any(|&(lat, lon)| {
        lon >= west && lon <= east && lat >= south && lat <= north
    })
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

fn gtlf_highway_from_props(props: &serde_json::Map<String, serde_json::Value>) -> (String, Option<String>) {
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

fn load_jeep_lines(project_dir: &Path, gtlf: &GtlfSourceConfig, bbox: LonLatBBox) -> Result<Vec<SupplementalLine>> {
    let mut out = Vec::new();
    for url in &gtlf.jeep_layers {
        let path = gtlf_cache_dir(project_dir).join(layer_cache_name(url));
        if !path.is_file() {
            continue;
        }
        let bytes = std::fs::read(&path)?;
        let geo: GeoJson = serde_json::from_slice(&bytes)?;
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
            if !coords_in_bbox(&coords, bbox.west, bbox.south, bbox.east, bbox.north) {
                continue;
            }
            let (highway, tracktype) = f
                .properties
                .as_ref()
                .map(|p| gtlf_highway_from_props(p))
                .unwrap_or(("track".to_string(), None));
            out.push(SupplementalLine {
                coords,
                highway,
                tracktype,
            });
        }
    }
    Ok(out)
}

fn load_foot_lines(project_dir: &Path, gtlf: &GtlfSourceConfig, bbox: LonLatBBox) -> Result<Vec<Vec<(f64, f64)>>> {
    let mut out = Vec::new();
    for url in &gtlf.foot_layers {
        let path = gtlf_cache_dir(project_dir).join(layer_cache_name(url));
        if !path.is_file() {
            continue;
        }
        for coords in parse_geojson_lines(&path)? {
            if coords_in_bbox(&coords, bbox.west, bbox.south, bbox.east, bbox.north) {
                out.push(coords);
            }
        }
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
    let preset_path = project_dir.join("config.yaml");
    let preset = load_preset(&preset_path)?;
    let gtlf = preset.sources.gtlf.clone();
    if !gtlf.enabled {
        return Ok(GtlfBundle::empty());
    }
    ensure_gtlf_cache(project_dir, force_fetch)?;
    let fingerprint = gtlf_cache_fingerprint(project_dir, &gtlf);
    let jeep_lines = load_jeep_lines(project_dir, &gtlf, LonLatBBox::new(west, south, east, north))?;
    merge_supplemental_jeep_lines(routing, &jeep_lines, opts.road_sample_step_m);
    let foot_lines = load_foot_lines(project_dir, &gtlf, LonLatBBox::new(west, south, east, north))?;
    let foot_trails = build_trail_graph(&foot_lines, opts.road_sample_step_m);
    let _ = TRAIL_MAX_M;
    Ok(GtlfBundle {
        foot_trails,
        fingerprint,
    })
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
