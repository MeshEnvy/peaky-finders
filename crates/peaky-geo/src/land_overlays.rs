//! Built-in map overlay: eligible `(include − exclude) ∩ AOI`.
//! Cached by land digest only. Parcel rings with exclude holes; no SMA dissolve.

use std::fs;
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::Instant;

use anyhow::{bail, Context, Result};
use geo::{BoundingRect, Centroid, Contains, Geometry, MultiPolygon, Point, Polygon};
use geojson::Geometry as GeoJsonGeometry;
use peaky_preset::LandLayerRole;
use rayon::prelude::*;
use serde_json::{json, Value};
use splatter::LandFilterIndex;

use crate::eligible_land::{
    collect_role_geometry, collect_role_geometry_for_source, empty_land_geometry,
    include_role_source_ids, intersect_land_geometry, overlay_land_digest,
};
use crate::land_pipeline::land_cache_dir;
use crate::lon_lat_bbox::LonLatBBox;

static OVERLAY_BUILD_LOCK: Mutex<()> = Mutex::new(());

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum LandOverlayKind {
    Eligible,
}

impl LandOverlayKind {
    pub fn as_str(self) -> &'static str {
        "eligible"
    }

    pub fn parse(raw: &str) -> Result<Self> {
        match raw.trim().to_ascii_lowercase().as_str() {
            "eligible" => Ok(Self::Eligible),
            _ => bail!("unknown land overlay: {raw}"),
        }
    }
}

fn flatten_polygons(geom: &Geometry<f64>) -> Vec<Polygon<f64>> {
    match geom {
        Geometry::Polygon(p) => {
            if p.exterior().0.is_empty() {
                Vec::new()
            } else {
                vec![p.clone()]
            }
        }
        Geometry::MultiPolygon(mp) => mp
            .0
            .iter()
            .filter(|p| !p.exterior().0.is_empty())
            .cloned()
            .collect(),
        Geometry::GeometryCollection(gc) => gc.0.iter().flat_map(flatten_polygons).collect(),
        _ => Vec::new(),
    }
}

fn geometry_from_polys(polys: Vec<Polygon<f64>>) -> Geometry<f64> {
    let polys: Vec<Polygon<f64>> = polys
        .into_iter()
        .filter(|p| !p.exterior().0.is_empty())
        .collect();
    match polys.len() {
        0 => empty_land_geometry(),
        1 => Geometry::Polygon(polys.into_iter().next().expect("len 1")),
        _ => Geometry::MultiPolygon(MultiPolygon(polys)),
    }
}

fn try_intersect(a: &Geometry<f64>, b: &Geometry<f64>) -> Option<Geometry<f64>> {
    catch_unwind(AssertUnwindSafe(|| intersect_land_geometry(a.clone(), b))).ok()
}

fn poly_bbox(poly: &Polygon<f64>) -> Option<LonLatBBox> {
    let rect = poly.bounding_rect()?;
    Some(LonLatBBox::new(
        rect.min().x,
        rect.min().y,
        rect.max().x,
        rect.max().y,
    ))
}

fn poly_centroid(poly: &Polygon<f64>) -> Option<Point<f64>> {
    poly.centroid()
}

fn clip_include_to_aoi(poly: &Polygon<f64>, aoi: &[Polygon<f64>]) -> Vec<Polygon<f64>> {
    if aoi.is_empty() {
        return vec![poly.clone()];
    }
    let Some(inc_bbox) = poly_bbox(poly) else {
        return Vec::new();
    };
    let mut out = Vec::new();
    for piece in aoi {
        let Some(aoi_bbox) = poly_bbox(piece) else {
            continue;
        };
        if !aoi_bbox.intersects(inc_bbox) {
            continue;
        }
        if aoi_bbox.contains_bbox(inc_bbox) {
            if let Some(c) = poly_centroid(poly) {
                if piece.contains(&c) {
                    out.push(poly.clone());
                    continue;
                }
            }
        }
        match try_intersect(
            &Geometry::Polygon(poly.clone()),
            &Geometry::Polygon(piece.clone()),
        ) {
            Some(geom) => out.extend(flatten_polygons(&geom)),
            None => {}
        }
    }
    out
}

fn ring_bbox_inside(outer: LonLatBBox, ring: &geo::LineString<f64>) -> bool {
    if ring.0.len() < 4 {
        return false;
    }
    let hole = Polygon::new(ring.clone(), vec![]);
    let Some(inner) = poly_bbox(&hole) else {
        return false;
    };
    outer.contains_bbox(inner)
}

fn ring_vertices_inside(shell: &Polygon<f64>, ring: &geo::LineString<f64>) -> bool {
    ring.0.iter().all(|coord| shell.contains(&Point::new(coord.x, coord.y)))
}

fn sanitize_polygon(poly: &Polygon<f64>) -> Option<Polygon<f64>> {
    if poly.exterior().0.len() < 4 {
        return None;
    }
    let ext_bbox = poly_bbox(poly)?;
    let shell = Polygon::new(poly.exterior().clone(), vec![]);
    let holes = poly
        .interiors()
        .iter()
        .filter(|ring| ring_bbox_inside(ext_bbox, ring) && ring_vertices_inside(&shell, ring))
        .cloned()
        .collect();
    Some(Polygon::new(poly.exterior().clone(), holes))
}

fn exclude_covers_include(include: &Polygon<f64>, exclude: &Polygon<f64>) -> bool {
    let Some(inc_bbox) = poly_bbox(include) else {
        return false;
    };
    let Some(ex_bbox) = poly_bbox(exclude) else {
        return false;
    };
    if !ex_bbox.contains_bbox(inc_bbox) {
        return false;
    }
    let Some(c) = poly_centroid(include) else {
        return false;
    };
    exclude.contains(&c)
}

fn punch_excludes_as_holes(
    poly: &Polygon<f64>,
    exclude: &LandFilterIndex,
) -> Option<Polygon<f64>> {
    let Some(rect) = poly.bounding_rect() else {
        return None;
    };
    let hits = exclude.intersecting_exclude_polys(
        rect.min().x,
        rect.min().y,
        rect.max().x,
        rect.max().y,
    );
    if hits.is_empty() {
        return sanitize_polygon(poly);
    }
    let poly = sanitize_polygon(poly)?;
    let Some(inc_bbox) = poly_bbox(&poly) else {
        return None;
    };
    if hits.iter().any(|ex| exclude_covers_include(&poly, ex)) {
        return None;
    }
    let mut holes = poly.interiors().to_vec();
    for ex in hits {
        let Some(ex_bbox) = poly_bbox(ex) else {
            continue;
        };
        if !inc_bbox.contains_bbox(ex_bbox) {
            continue;
        }
        let Some(c) = poly_centroid(ex) else {
            continue;
        };
        if poly.contains(&c) {
            holes.push(ex.exterior().clone());
        }
    }
    sanitize_polygon(&Polygon::new(poly.exterior().clone(), holes))
}

/// Include minus exclude, clipped to the AOI. Parcel-wise; no SMA dissolve.
pub fn build_eligible_overlay(
    include: &Geometry<f64>,
    exclude: &Geometry<f64>,
    aoi: &Geometry<f64>,
) -> Geometry<f64> {
    let include_mp = MultiPolygon(flatten_polygons(include));
    let exclude_mp = MultiPolygon(flatten_polygons(exclude));
    let aoi_polys = flatten_polygons(aoi);
    let exclude_idx = LandFilterIndex::from_include_exclude(&MultiPolygon(vec![]), &exclude_mp);
    let parts: Vec<Polygon<f64>> = include_mp
        .0
        .par_iter()
        .flat_map(|poly| clip_include_to_aoi(poly, &aoi_polys))
        .filter_map(|poly| punch_excludes_as_holes(&poly, &exclude_idx))
        .collect();
    geometry_from_polys(parts)
}

fn overlay_cache_dir(preset_path: &Path, digest: &str) -> PathBuf {
    land_cache_dir(preset_path)
        .join("overlays")
        .join("v7")
        .join(digest)
}

fn overlay_part_cache_path(preset_path: &Path, digest: &str, source_id: &str) -> PathBuf {
    overlay_cache_dir(preset_path, digest).join(format!("{source_id}.geojson"))
}

fn geometry_to_feature_collection(geom: &Geometry<f64>, source_id: &str) -> Value {
    let features: Vec<Value> = flatten_polygons(geom)
        .into_iter()
        .filter_map(|poly| sanitize_polygon(&poly))
        .map(|poly| {
            json!({
                "type": "Feature",
                "properties": { "kind": "eligible", "sourceId": source_id },
                "geometry": GeoJsonGeometry::from(&Geometry::Polygon(poly))
            })
        })
        .collect();
    json!({ "type": "FeatureCollection", "features": features })
}

fn merge_overlay_part_bytes(parts: &[Vec<u8>]) -> Result<Vec<u8>> {
    let mut features = Vec::new();
    for bytes in parts {
        let value: Value = serde_json::from_slice(bytes).context("parse overlay part")?;
        if let Some(rows) = value.get("features").and_then(|v| v.as_array()) {
            features.extend(rows.iter().cloned());
        }
    }
    Ok(serde_json::to_vec(&json!({
        "type": "FeatureCollection",
        "features": features
    }))?)
}

fn overlay_part_ready(path: &Path) -> bool {
    fs::metadata(path).is_ok_and(|meta| meta.len() > 0)
}

fn load_overlay_shared_inputs(
    preset_path: &Path,
) -> Result<(Geometry<f64>, Geometry<f64>, Option<LonLatBBox>)> {
    let aoi = collect_role_geometry(preset_path, LandLayerRole::Aoi, None)?;
    let clip = LonLatBBox::from_geometry(&aoi);
    let exclude = collect_role_geometry(preset_path, LandLayerRole::Exclude, clip)?;
    Ok((exclude, aoi, clip))
}

fn write_overlay_cache(path: &Path, bytes: &[u8]) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let tmp = path.with_extension("geojson.tmp");
    fs::write(&tmp, bytes)?;
    fs::rename(&tmp, path).with_context(|| format!("rename {}", path.display()))?;
    Ok(())
}

fn ensure_overlay_parts(preset_path: &Path, digest: &str) -> Result<Vec<String>> {
    let source_ids = include_role_source_ids(preset_path)?;
    if source_ids
        .iter()
        .all(|id| overlay_part_ready(&overlay_part_cache_path(preset_path, digest, id)))
    {
        return Ok(source_ids);
    }

    let t0 = Instant::now();
    tracing::info!(digest = %digest, sources = source_ids.len(), "land overlay: build start");
    let (exclude, aoi, clip) = load_overlay_shared_inputs(preset_path)?;
    let mut polygons = 0usize;
    let mut bytes_out = 0usize;
    for source_id in &source_ids {
        let path = overlay_part_cache_path(preset_path, digest, source_id);
        if overlay_part_ready(&path) {
            continue;
        }
        let include = collect_role_geometry_for_source(
            preset_path,
            LandLayerRole::Include,
            clip,
            source_id,
        )?;
        let geom = build_eligible_overlay(&include, &exclude, &aoi);
        polygons += flatten_polygons(&geom).len();
        let bytes = serde_json::to_vec(&geometry_to_feature_collection(&geom, source_id))?;
        bytes_out += bytes.len();
        write_overlay_cache(&path, &bytes)?;
        tracing::info!(
            source_id = %source_id,
            polygons = flatten_polygons(&geom).len(),
            bytes = bytes.len(),
            "land overlay: part done"
        );
    }
    tracing::info!(
        polygons,
        bytes = bytes_out,
        elapsed_secs = t0.elapsed().as_secs_f64(),
        "land overlay: build done"
    );
    Ok(source_ids)
}

fn lock_overlay_parts(preset_path: &Path, digest: &str) -> Result<Vec<String>> {
    let _guard = OVERLAY_BUILD_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    ensure_overlay_parts(preset_path, digest)
}

/// Overlay GeoJSON for the whole project. One feature per include source.
pub fn read_or_build_overlay_geojson(
    preset_path: &Path,
    kind: LandOverlayKind,
) -> Result<(Vec<u8>, String)> {
    let _ = kind;
    let digest = overlay_land_digest(preset_path).unwrap_or_else(|_| "none".to_string());
    let source_ids = lock_overlay_parts(preset_path, &digest)?;
    let mut parts = Vec::with_capacity(source_ids.len());
    for source_id in &source_ids {
        let path = overlay_part_cache_path(preset_path, &digest, source_id);
        parts.push(fs::read(&path).with_context(|| format!("read {}", path.display()))?);
    }
    Ok((merge_overlay_part_bytes(&parts)?, digest))
}

/// Overlay GeoJSON for one include source. Same shape as that source's map layer.
pub fn read_or_build_overlay_part_geojson(
    preset_path: &Path,
    kind: LandOverlayKind,
    source_id: &str,
) -> Result<(Vec<u8>, String)> {
    let _ = kind;
    let digest = overlay_land_digest(preset_path).unwrap_or_else(|_| "none".to_string());
    let source_ids = lock_overlay_parts(preset_path, &digest)?;
    if !source_ids.iter().any(|id| id == source_id) {
        bail!("unknown include source for overlay: {source_id}");
    }
    let path = overlay_part_cache_path(preset_path, &digest, source_id);
    let bytes = fs::read(&path).with_context(|| format!("read {}", path.display()))?;
    Ok((bytes, digest))
}

pub fn overlay_contains_lon_lat(geom: &Geometry<f64>, lon: f64, lat: f64) -> bool {
    let pt = Point::new(lon, lat);
    match geom {
        Geometry::Polygon(p) => p.contains(&pt),
        Geometry::MultiPolygon(mp) => mp.contains(&pt),
        _ => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use geo::LineString;
    use std::time::Duration;

    fn square(min_x: f64, min_y: f64, max_x: f64, max_y: f64) -> Geometry<f64> {
        Geometry::Polygon(Polygon::new(
            LineString::from(vec![
                geo::Coord {
                    x: min_x,
                    y: min_y,
                },
                geo::Coord {
                    x: max_x,
                    y: min_y,
                },
                geo::Coord {
                    x: max_x,
                    y: max_y,
                },
                geo::Coord {
                    x: min_x,
                    y: max_y,
                },
                geo::Coord {
                    x: min_x,
                    y: min_y,
                },
            ]),
            vec![],
        ))
    }

    #[test]
    fn eligible_is_include_minus_exclude_inside_aoi() {
        let aoi = square(-120.0, 39.0, -119.0, 40.0);
        let include = square(-119.8, 39.2, -119.2, 39.8);
        let exclude = square(-119.6, 39.4, -119.5, 39.5);
        let t0 = Instant::now();
        let eligible = build_eligible_overlay(&include, &exclude, &aoi);
        assert!(t0.elapsed() < Duration::from_millis(100));
        assert!(overlay_contains_lon_lat(&eligible, -119.7, 39.3));
        assert!(!overlay_contains_lon_lat(&eligible, -119.55, 39.45));
        assert!(!overlay_contains_lon_lat(&eligible, -119.9, 39.1));
    }

    #[test]
    fn eligible_clips_include_to_aoi() {
        let aoi = square(-120.0, 39.0, -119.0, 40.0);
        let include = square(-120.2, 39.2, -119.8, 39.5);
        let exclude = empty_land_geometry();
        let eligible = build_eligible_overlay(&include, &exclude, &aoi);
        assert!(overlay_contains_lon_lat(&eligible, -119.9, 39.35));
        assert!(!overlay_contains_lon_lat(&eligible, -120.1, 39.35));
    }

    #[test]
    fn eligible_keeps_include_when_exclude_sits_inside() {
        let aoi = square(-120.0, 39.0, -119.0, 40.0);
        let include = square(-119.8, 39.2, -119.2, 39.8);
        let exclude = square(-119.52, 39.48, -119.48, 39.52);
        let eligible = build_eligible_overlay(&include, &exclude, &aoi);
        assert!(overlay_contains_lon_lat(&eligible, -119.7, 39.3));
        assert!(!overlay_contains_lon_lat(&eligible, -119.50, 39.50));
    }

    #[test]
    fn eligible_drops_include_outside_aoi() {
        let aoi = square(-120.0, 39.0, -119.0, 40.0);
        let include = square(-120.5, 39.2, -120.2, 39.5);
        let exclude = empty_land_geometry();
        let eligible = build_eligible_overlay(&include, &exclude, &aoi);
        assert!(!overlay_contains_lon_lat(&eligible, -120.3, 39.35));
    }

    #[test]
    fn many_include_parcels_stay_parcelwise() {
        let aoi = square(-120.0, 39.0, -119.0, 40.0);
        let mut includes = Vec::new();
        for i in 0..8 {
            for j in 0..8 {
                let x = -119.9 + f64::from(i) * 0.1;
                let y = 39.1 + f64::from(j) * 0.1;
                includes.extend(flatten_polygons(&square(x, y, x + 0.08, y + 0.08)));
            }
        }
        let include = Geometry::MultiPolygon(MultiPolygon(includes));
        let exclude = square(-119.55, 39.45, -119.45, 39.55);
        let t0 = Instant::now();
        let eligible = build_eligible_overlay(&include, &exclude, &aoi);
        assert!(t0.elapsed() < Duration::from_millis(400), "{:?}", t0.elapsed());
        assert!(overlay_contains_lon_lat(&eligible, -119.86, 39.14));
        assert!(!overlay_contains_lon_lat(&eligible, -119.50, 39.50));
    }

    #[test]
    fn overlay_geojson_roundtrip_from_preset() {
        let tmp = tempfile::tempdir().expect("tempdir");
        let project = tmp.path();
        std::fs::create_dir_all(project.join("data")).expect("data");
        let include = r#"{
          "type":"FeatureCollection",
          "features":[
            {"type":"Feature","properties":{},"geometry":{"type":"Polygon","coordinates":[[[-119.8,39.2],[-119.2,39.2],[-119.2,39.8],[-119.8,39.8],[-119.8,39.2]]]}}
          ]
        }"#;
        let exclude = r#"{
          "type":"FeatureCollection",
          "features":[
            {"type":"Feature","properties":{},"geometry":{"type":"Polygon","coordinates":[[[-119.6,39.4],[-119.5,39.4],[-119.5,39.5],[-119.6,39.5],[-119.6,39.4]]]}}
          ]
        }"#;
        let aoi = r#"{
          "type":"FeatureCollection",
          "features":[
            {"type":"Feature","properties":{},"geometry":{"type":"Polygon","coordinates":[[[-120.0,39.0],[-119.0,39.0],[-119.0,40.0],[-120.0,40.0],[-120.0,39.0]]]}}
          ]
        }"#;
        std::fs::write(project.join("data/include.geojson"), include).expect("include");
        std::fs::write(project.join("data/exclude.geojson"), exclude).expect("exclude");
        std::fs::write(project.join("data/aoi.geojson"), aoi).expect("aoi");
        std::fs::write(
            project.join("config.yaml"),
            r#"
simulation:
  radius_km: 50
display:
  colormap: plasma
  transparency: 50
  min_dbm: -130
  max_dbm: -80
sites: {}
land:
  sources:
    pub:
      path: data/include.geojson
      enabled: true
      layers:
        - name: include
          role: include
    blocked:
      path: data/exclude.geojson
      enabled: true
      layers:
        - name: exclude
          role: exclude
    boundary:
      path: data/aoi.geojson
      enabled: true
      layers:
        - name: aoi
          role: aoi
"#,
        )
        .expect("yaml");

        let t0 = Instant::now();
        let (bytes, digest) = read_or_build_overlay_geojson(
            &project.join("config.yaml"),
            LandOverlayKind::Eligible,
        )
        .expect("eligible overlay");
        assert!(
            t0.elapsed() < Duration::from_millis(400),
            "overlay {:?}",
            t0.elapsed()
        );
        assert_ne!(digest, "none");
        let geojson: Value = serde_json::from_slice(&bytes).expect("json");
        assert_eq!(geojson["type"], "FeatureCollection");
        let features = geojson["features"].as_array().expect("features");
        assert_eq!(features.len(), 1);
        assert_eq!(features[0]["properties"]["sourceId"], "pub");

        let (again, _) = read_or_build_overlay_geojson(
            &project.join("config.yaml"),
            LandOverlayKind::Eligible,
        )
        .expect("cache hit");
        assert_eq!(bytes, again);
    }
}
