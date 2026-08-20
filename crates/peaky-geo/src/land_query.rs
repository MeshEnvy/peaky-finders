//! Point-in-polygon queries against preset land layers (GeoJSON).

use std::path::Path;

use anyhow::{bail, Context, Result};
use geo::{Area, Contains, Geometry, HasDimensions, Point};
use geo::algorithm::bounding_rect::BoundingRect;
use geojson::{Feature, GeoJson, Value as GeoJsonValue};
use peaky_preset::{load_preset, LandLayerEntry, Preset};
use rstar::{RTree, RTreeObject, AABB};

use crate::land_filter::{json_safe_properties, matches_land_attribute_filters};
use crate::land_path::{is_land_geojson_path, resolve_land_layer_geojson_path};

#[derive(Debug, Clone)]
pub struct LandPointHit {
    pub properties: serde_json::Map<String, serde_json::Value>,
    pub area: f64,
}

struct IndexedPolygon {
    envelope: AABB<[f64; 2]>,
    index: usize,
}

impl RTreeObject for IndexedPolygon {
    type Envelope = AABB<[f64; 2]>;

    fn envelope(&self) -> Self::Envelope {
        self.envelope
    }
}

pub struct LandLayerSpatialIndex {
    tree: RTree<IndexedPolygon>,
    geoms: Vec<Geometry<f64>>,
    hits: Vec<LandPointHit>,
}

pub fn resolve_land_layer_entry(
    preset: &Preset,
    source_id: &str,
    layer_name: Option<&str>,
) -> Result<LandLayerEntry> {
    let source = preset
        .land
        .sources
        .get(source_id)
        .with_context(|| format!("unknown land source: {source_id}"))?;
    if let Some(name) = layer_name {
        for layer in &source.layers {
            if layer.name == name {
                return Ok(layer.clone());
            }
        }
        let names: Vec<_> = source.layers.iter().map(|l| l.name.as_str()).collect();
        bail!("layer {name:?} not found on {source_id:?}; have {names:?}");
    }
    if source.layers.len() != 1 {
        bail!(
            "land source {source_id:?} has {} layers; pass layer_name",
            source.layers.len()
        );
    }
    Ok(source.layers[0].clone())
}

fn geometry_from_geojson(value: &GeoJsonValue) -> Result<Geometry<f64>> {
    let geom: geo::Geometry<f64> = value
        .clone()
        .try_into()
        .context("convert GeoJSON geometry")?;
    Ok(geom)
}

fn polygon_envelope(geom: &Geometry<f64>) -> Option<AABB<[f64; 2]>> {
    let rect = geom.bounding_rect()?;
    Some(AABB::from_corners(
        [rect.min().x, rect.min().y],
        [rect.max().x, rect.max().y],
    ))
}

pub fn read_land_layer_features(
    data_path: &Path,
    entry: &LandLayerEntry,
) -> Result<Vec<(Geometry<f64>, LandPointHit)>> {
    if !is_land_geojson_path(data_path) {
        bail!("GeoJSON-only land import: {}", data_path.display());
    }
    let text = std::fs::read_to_string(data_path)
        .with_context(|| format!("read {}", data_path.display()))?;
    let geojson: GeoJson = text.parse().context("parse GeoJSON")?;
    let features = match geojson {
        GeoJson::FeatureCollection(fc) => fc.features,
        GeoJson::Feature(f) => vec![f],
        _ => bail!("expected GeoJSON FeatureCollection"),
    };

    let mut rows = Vec::new();
    for feature in features {
        let Feature {
            geometry,
            properties,
            ..
        } = feature;
        let Some(geometry) = geometry else {
            continue;
        };
        let props_map = properties.unwrap_or_default();
        if !matches_land_attribute_filters(&props_map, &entry.include, &entry.exclude) {
            continue;
        }
        let geom = geometry_from_geojson(&geometry.value)?;
        if geom.is_empty() {
            continue;
        }
        let area = geom.unsigned_area();
        let safe = json_safe_properties(&props_map);
        rows.push((
            geom,
            LandPointHit {
                properties: safe,
                area,
            },
        ));
    }
    Ok(rows)
}

pub fn load_land_layer_index(
    preset_path: &Path,
    source_id: &str,
    layer_name: Option<&str>,
    entry: Option<&LandLayerEntry>,
) -> Result<LandLayerSpatialIndex> {
    let preset = load_preset(preset_path)?;
    let project_dir = preset_path
        .parent()
        .context("preset path must have a parent directory")?;
    let source = preset
        .land
        .sources
        .get(source_id)
        .with_context(|| format!("unknown land source: {source_id}"))?;
    let layer_entry = match entry {
        Some(e) => e.clone(),
        None => resolve_land_layer_entry(&preset, source_id, layer_name)?,
    };
    let data_path = resolve_land_layer_geojson_path(
        project_dir,
        source_id,
        &layer_entry.name,
        &source.path,
    )?;
    let rows = read_land_layer_features(&data_path, &layer_entry)?;

    let mut geoms = Vec::with_capacity(rows.len());
    let mut hits = Vec::with_capacity(rows.len());
    let mut indexed = Vec::with_capacity(rows.len());
    for (geom, hit) in rows {
        if let Some(envelope) = polygon_envelope(&geom) {
            let index = geoms.len();
            indexed.push(IndexedPolygon { envelope, index });
            geoms.push(geom);
            hits.push(hit);
        }
    }

    let tree = RTree::bulk_load(indexed);
    Ok(LandLayerSpatialIndex { tree, geoms, hits })
}

pub fn query_land_layer_index(
    index: &LandLayerSpatialIndex,
    lat: f64,
    lon: f64,
    smallest_only: bool,
) -> Vec<LandPointHit> {
    if index.geoms.is_empty() {
        return Vec::new();
    }

    let pt = Point::new(lon, lat);
    let mut matched = Vec::new();
    for item in index.tree.locate_in_envelope_intersecting(&AABB::from_point([lon, lat])) {
        let geom = &index.geoms[item.index];
        if geom.contains(&pt) {
            matched.push(index.hits[item.index].clone());
        }
    }

    if matched.is_empty() {
        return matched;
    }
    if smallest_only && matched.len() > 1 {
        matched.sort_by(|a, b| {
            a.area
                .partial_cmp(&b.area)
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        vec![matched.remove(0)]
    } else {
        matched
    }
}

pub fn point_hits(
    preset_path: &Path,
    lat: f64,
    lon: f64,
    source_id: &str,
    layer_name: Option<&str>,
    entry: Option<&LandLayerEntry>,
    smallest_only: bool,
) -> Result<Vec<serde_json::Map<String, serde_json::Value>>> {
    let index = load_land_layer_index(preset_path, source_id, layer_name, entry)?;
    Ok(query_land_layer_index(&index, lat, lon, smallest_only)
        .into_iter()
        .map(|hit| hit.properties)
        .collect())
}

