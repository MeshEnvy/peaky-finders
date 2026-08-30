//! Eligible land: include parcels minus exclude parcels (no statewide dissolve).

use std::path::{Path, PathBuf};
use std::time::Instant;

use anyhow::{bail, Context, Result};
use geo::{BooleanOps, Geometry, LineString, MultiPolygon, Polygon};
use rayon::prelude::*;
use splatter::LandFilterIndex;
use peaky_preset::{
    load_preset, resolved_preset_cache_dir, LandLayerEntry, LandLayerRole, Preset,
};

use crate::land_path::resolve_land_layer_geojson_path;
use crate::land_pipeline::{find_warmed_pipeline_geojson, land_cache_dir};
use crate::land_query::read_land_layer_polygons_in_bbox;
use crate::lon_lat_bbox::LonLatBBox;

fn geometry_to_multi(geom: &Geometry<f64>) -> MultiPolygon<f64> {
    match geom {
        Geometry::Polygon(p) => MultiPolygon(vec![p.clone()]),
        Geometry::MultiPolygon(mp) => mp.clone(),
        Geometry::GeometryCollection(gc) => {
            let mut polys = Vec::new();
            for g in &gc.0 {
                polys.extend(geometry_to_multi(g).0);
            }
            MultiPolygon(polys)
        }
        _ => MultiPolygon(vec![]),
    }
}

fn multi_to_geometry(mp: MultiPolygon<f64>) -> Geometry<f64> {
    if mp.0.is_empty() {
        empty_polygon()
    } else if mp.0.len() == 1 {
        Geometry::Polygon(mp.0[0].clone())
    } else {
        Geometry::MultiPolygon(mp)
    }
}

fn geom_union(a: Geometry<f64>, b: &Geometry<f64>) -> Geometry<f64> {
    let ma = geometry_to_multi(&a);
    let mb = geometry_to_multi(b);
    if ma.0.is_empty() {
        return b.clone();
    }
    if mb.0.is_empty() {
        return a;
    }
    multi_to_geometry(ma.union(&mb))
}

pub fn intersect_land_geometry(a: Geometry<f64>, b: &Geometry<f64>) -> Geometry<f64> {
    geom_intersection(a, b)
}

/// Include − exclude in Web Mercator, then back to lon/lat.
pub fn difference_land_geometry(a: &Geometry<f64>, b: &Geometry<f64>) -> Geometry<f64> {
    let a_ll = polygonal_area_only(a.clone());
    if is_empty_geom(&a_ll) {
        return empty_polygon();
    }
    let b_ll = polygonal_area_only(b.clone());
    if is_empty_geom(&b_ll) {
        return a_ll;
    }
    let diff = geom_difference(&to_web_mercator(&a_ll), &to_web_mercator(&b_ll));
    if is_empty_geom(&diff) {
        return empty_polygon();
    }
    let clean = polygonal_area_only(from_web_mercator(&diff));
    if is_empty_geom(&clean) {
        empty_polygon()
    } else {
        clean
    }
}

pub fn union_land_geometry(a: Geometry<f64>, b: &Geometry<f64>) -> Geometry<f64> {
    let a_ll = polygonal_area_only(a);
    let b_ll = polygonal_area_only(b.clone());
    if is_empty_geom(&a_ll) {
        return b_ll;
    }
    if is_empty_geom(&b_ll) {
        return a_ll;
    }
    let united = geom_union(to_web_mercator(&a_ll), &to_web_mercator(&b_ll));
    if is_empty_geom(&united) {
        return empty_polygon();
    }
    let clean = polygonal_area_only(from_web_mercator(&united));
    if is_empty_geom(&clean) {
        empty_polygon()
    } else {
        clean
    }
}

pub fn land_geometry_is_empty(geom: &Geometry<f64>) -> bool {
    is_empty_geom(geom)
}

pub fn empty_land_geometry() -> Geometry<f64> {
    empty_polygon()
}

pub fn collect_role_geometry(
    preset_path: &Path,
    role: LandLayerRole,
    clip: Option<LonLatBBox>,
) -> Result<Geometry<f64>> {
    Ok(multi_to_geometry(collect_role_polygons(
        preset_path,
        role,
        clip,
        None,
    )?))
}

pub fn collect_role_geometry_for_source(
    preset_path: &Path,
    role: LandLayerRole,
    clip: Option<LonLatBBox>,
    source_id: &str,
) -> Result<Geometry<f64>> {
    Ok(multi_to_geometry(collect_role_polygons(
        preset_path,
        role,
        clip,
        Some(source_id),
    )?))
}

pub fn include_role_source_ids(preset_path: &Path) -> Result<Vec<String>> {
    let preset = load_preset(preset_path)?;
    let mut ids: Vec<String> = iter_land_layer_entries_by_role(&preset, LandLayerRole::Include)
        .into_iter()
        .map(|(source_id, _)| source_id)
        .collect();
    ids.sort();
    ids.dedup();
    Ok(ids)
}

fn geom_intersection(a: Geometry<f64>, b: &Geometry<f64>) -> Geometry<f64> {
    let ma = geometry_to_multi(&a);
    let mb = geometry_to_multi(b);
    if ma.0.is_empty() || mb.0.is_empty() {
        return empty_polygon();
    }
    multi_to_geometry(ma.intersection(&mb))
}

fn geom_difference(a: &Geometry<f64>, b: &Geometry<f64>) -> Geometry<f64> {
    let ma = geometry_to_multi(a);
    let mb = geometry_to_multi(b);
    if ma.0.is_empty() {
        return empty_polygon();
    }
    if mb.0.is_empty() {
        return a.clone();
    }
    multi_to_geometry(ma.difference(&mb))
}

#[derive(Debug, thiserror::Error)]
#[error("{0}")]
pub struct EligibleLandError(pub String);

pub fn iter_land_layer_entries_by_role(
    preset: &Preset,
    role: LandLayerRole,
) -> Vec<(String, LandLayerEntry)> {
    let mut rows = Vec::new();
    for (source_id, source) in &preset.land.sources {
        if !source.is_enabled() {
            continue;
        }
        for layer in &source.layers {
            if layer.role == Some(role) {
                rows.push((source_id.clone(), layer.clone()));
            }
        }
    }
    rows.sort_by(|a, b| a.0.cmp(&b.0).then_with(|| a.1.name.cmp(&b.1.name)));
    rows
}

fn empty_polygon() -> Geometry<f64> {
    Geometry::Polygon(Polygon::new(LineString::empty(), vec![]))
}

fn is_empty_geom(geom: &Geometry<f64>) -> bool {
    use geo::HasDimensions;
    geom.is_empty()
}

fn polygonal_area_only(geom: Geometry<f64>) -> Geometry<f64> {
    match geom {
        Geometry::Polygon(p) => {
            if p.exterior().0.is_empty() {
                empty_polygon()
            } else {
                Geometry::Polygon(p)
            }
        }
        Geometry::MultiPolygon(mp) => {
            let parts: MultiPolygon<f64> = mp
                .0
                .into_iter()
                .filter(|p| !p.exterior().0.is_empty())
                .collect();
            if parts.0.is_empty() {
                empty_polygon()
            } else {
                Geometry::MultiPolygon(parts)
            }
        }
        Geometry::GeometryCollection(gc) => {
            let mut polys = Vec::new();
            for g in gc.0 {
                let cleaned = polygonal_area_only(g);
                if !is_empty_geom(&cleaned) {
                    polys.push(cleaned);
                }
            }
            unary_union_geometries(polys)
        }
        _ => empty_polygon(),
    }
}

fn unary_union_geometries(geoms: Vec<Geometry<f64>>) -> Geometry<f64> {
    let mut acc: Option<Geometry<f64>> = None;
    for geom in geoms {
        if is_empty_geom(&geom) {
            continue;
        }
        acc = Some(match acc {
            None => geom,
            Some(prev) => geom_union(prev, &geom),
        });
    }
    acc.unwrap_or_else(empty_polygon)
}

fn collect_role_polygons(
    preset_path: &Path,
    role: LandLayerRole,
    clip: Option<LonLatBBox>,
    only_source: Option<&str>,
) -> Result<MultiPolygon<f64>> {
    let preset = load_preset(preset_path)?;
    let project_dir = preset_path
        .parent()
        .context("preset path must have a parent directory")?;
    let cache_root = land_cache_dir(preset_path);
    let mut jobs = iter_land_layer_entries_by_role(&preset, role);
    if let Some(source_id) = only_source {
        jobs.retain(|(id, _)| id == source_id);
    }
    let role_tag = land_role_tag(role);
    tracing::info!(
        role = role_tag,
        layers = jobs.len(),
        clip = clip.map(|b| format!(
            "{:.3},{:.3},{:.3},{:.3}",
            b.west, b.south, b.east, b.north
        )),
        "eligible land: collect start"
    );

    let batches: Result<Vec<Vec<Polygon<f64>>>> = jobs
        .par_iter()
        .map(|(source_id, layer)| {
            let t0 = Instant::now();
            let source = preset
                .land
                .sources
                .get(source_id)
                .with_context(|| format!("unknown land source: {source_id}"))?;
            let data_path = match find_warmed_pipeline_geojson(
                &cache_root,
                source_id,
                &layer.layer_key(),
            ) {
                Some(path) => path,
                None => resolve_land_layer_geojson_path(
                    project_dir,
                    source_id,
                    &layer.name,
                    &source.path,
                )?,
            };
            let polys = read_land_layer_polygons_in_bbox(&data_path, layer, clip)?;
            tracing::info!(
                role = role_tag,
                source_id = %source_id,
                layer = %layer.layer_key(),
                kept = polys.len(),
                elapsed_secs = t0.elapsed().as_secs_f64(),
                path = %data_path.display(),
                "eligible land: layer clipped"
            );
            Ok(polys)
        })
        .collect();

    let polys: Vec<Polygon<f64>> = batches?.into_iter().flatten().collect();
    tracing::info!(
        role = role_tag,
        polygons = polys.len(),
        "eligible land: collect done"
    );
    Ok(MultiPolygon(polys))
}

fn to_web_mercator(geom: &Geometry<f64>) -> Geometry<f64> {
    use geo::MapCoords;
    geom.map_coords(|coord| {
        let (x, y) = lon_lat_to_mercator(coord.x, coord.y);
        geo::Coord { x, y }
    })
}

fn from_web_mercator(geom: &Geometry<f64>) -> Geometry<f64> {
    use geo::MapCoords;
    geom.map_coords(|coord| {
        let (lon, lat) = mercator_to_lon_lat(coord.x, coord.y);
        geo::Coord { x: lon, y: lat }
    })
}

fn lon_lat_to_mercator(lon: f64, lat: f64) -> (f64, f64) {
    let x = lon.to_radians() * 6_378_137.0;
    let y = ((std::f64::consts::FRAC_PI_4 + lat.to_radians() / 2.0).tan()).ln() * 6_378_137.0;
    (x, y)
}

fn mercator_to_lon_lat(x: f64, y: f64) -> (f64, f64) {
    let lon = x / 6_378_137.0 * 180.0 / std::f64::consts::PI;
    let lat = (2.0 * (y / 6_378_137.0).exp().atan() - std::f64::consts::FRAC_PI_2).to_degrees();
    (lon, lat)
}

pub fn build_eligible_geometry(
    include_union: &Geometry<f64>,
    exclude_union: &Geometry<f64>,
) -> Geometry<f64> {
    if is_empty_geom(include_union) {
        return empty_polygon();
    }
    difference_land_geometry(include_union, exclude_union)
}

#[derive(Clone, Debug)]
pub struct EligibleLandParts {
    pub include: MultiPolygon<f64>,
    pub exclude: MultiPolygon<f64>,
    pub digest: String,
}

impl EligibleLandParts {
    pub fn index(&self) -> LandFilterIndex {
        LandFilterIndex::from_include_exclude(&self.include, &self.exclude)
    }

    pub fn is_empty(&self) -> bool {
        self.include.0.is_empty()
    }
}

pub fn build_eligible_land_union(preset_path: &Path) -> Result<Geometry<f64>> {
    let parts = load_or_build_eligible_land_parts(preset_path, None)?;
    if parts.is_empty() {
        bail!(EligibleLandError(
            "Eligible land geometry is empty after include − exclude".into()
        ));
    }
    Ok(multi_to_geometry(parts.include))
}

fn resolved_eligible_cache_dir(preset_path: &Path) -> PathBuf {
    resolved_preset_cache_dir(preset_path).join("land/eligible")
}

/// Disk cache for per-Skadi-tile eligible-land bitmasks (`dem_masks/*.elmk`).
pub fn eligible_land_dem_mask_dir(preset_path: &Path, digest: &str) -> PathBuf {
    resolved_eligible_cache_dir(preset_path)
        .join(digest)
        .join("dem_masks")
}

/// Load eligible land and build the R-tree point filter (once per process).
pub fn load_or_build_eligible_land_filter(
    preset_path: &Path,
    clip: Option<LonLatBBox>,
) -> Result<(String, LandFilterIndex, MultiPolygon<f64>)> {
    let parts = load_or_build_eligible_land_parts(preset_path, clip)?;
    let filter = parts.index();
    Ok((parts.digest, filter, parts.include))
}

/// Collect include/exclude parcels, clipped to `clip` when given.
///
/// Does **not** dissolve statewide SMA. Seek and finder must pass a hop/route bbox.
pub fn load_or_build_eligible_land_parts(
    preset_path: &Path,
    clip: Option<LonLatBBox>,
) -> Result<EligibleLandParts> {
    let preset = load_preset(preset_path)?;
    if iter_land_layer_entries_by_role(&preset, LandLayerRole::Include).is_empty() {
        return Err(EligibleLandError(
            "Seek requires at least one land layer with role: include".into(),
        )
        .into());
    }

    let digest = eligible_land_digest(preset_path)?;
    let include = collect_role_polygons(preset_path, LandLayerRole::Include, clip, None)?;
    let exclude = collect_role_polygons(preset_path, LandLayerRole::Exclude, clip, None)?;
    if include.0.is_empty() && clip.is_none() {
        bail!(EligibleLandError(
            "Eligible land geometry is empty after include − exclude".into()
        ));
    }
    Ok(EligibleLandParts {
        include,
        exclude,
        digest,
    })
}

/// Load cached eligible land (v4 `union.wkb`) or collect include parcels (no dissolve).
pub fn load_or_build_eligible_land_union(
    preset_path: &Path,
) -> Result<(Geometry<f64>, String)> {
    let parts = load_or_build_eligible_land_parts(preset_path, None)?;
    Ok((multi_to_geometry(parts.include), parts.digest))
}

pub fn load_preset_aoi_union(preset_path: &Path) -> Result<Option<Geometry<f64>>> {
    let geom = multi_to_geometry(collect_role_polygons(
        preset_path,
        LandLayerRole::Aoi,
        None,
        None,
    )?);
    if is_empty_geom(&geom) {
        Ok(None)
    } else {
        Ok(Some(geom))
    }
}

pub fn aoi_land_digest(preset_path: &Path) -> Result<String> {
    land_role_layer_digest(preset_path, LandLayerRole::Aoi)
}

pub fn eligible_land_digest(preset_path: &Path) -> Result<String> {
    let preset = load_preset(preset_path)?;
    let project_dir = preset_path
        .parent()
        .context("preset path must have a parent directory")?;
    let mut parts = Vec::new();
    for role in [LandLayerRole::Include, LandLayerRole::Exclude] {
        for (source_id, layer) in iter_land_layer_entries_by_role(&preset, role) {
            let source = &preset.land.sources[&source_id];
            let data_path = resolve_land_layer_geojson_path(
                project_dir,
                &source_id,
                &layer.name,
                &source.path,
            )?;
            let meta = std::fs::metadata(&data_path)?;
            let modified = meta
                .modified()
                .ok()
                .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
                .map(|d| d.as_nanos())
                .unwrap_or(0);
            let role_tag = land_role_tag(role);
            parts.push(format!(
                "{role_tag}|{source_id}|{}|{modified}|{}",
                layer.layer_key(),
                meta.len()
            ));
        }
    }
    if parts.is_empty() {
        return Ok("none".to_string());
    }
    parts.sort();
    Ok(format!("{:016x}", fnv1a_hash(parts.join("\n").as_bytes())))
}

pub fn overlay_land_digest(preset_path: &Path) -> Result<String> {
    let mut parts = Vec::new();
    for role in [
        LandLayerRole::Aoi,
        LandLayerRole::Include,
        LandLayerRole::Exclude,
    ] {
        parts.push(format!(
            "{}:{}",
            land_role_tag(role),
            land_role_layer_digest(preset_path, role)?
        ));
    }
    Ok(format!("{:016x}", fnv1a_hash(parts.join("\n").as_bytes())))
}

fn land_role_tag(role: LandLayerRole) -> &'static str {
    match role {
        LandLayerRole::Include => "include",
        LandLayerRole::Exclude => "exclude",
        LandLayerRole::Aoi => "aoi",
    }
}

fn land_role_layer_digest(preset_path: &Path, role: LandLayerRole) -> Result<String> {
    let preset = load_preset(preset_path)?;
    let project_dir = preset_path
        .parent()
        .context("preset path must have a parent directory")?;
    let mut parts = Vec::new();
    for (source_id, layer) in iter_land_layer_entries_by_role(&preset, role) {
        let source = &preset.land.sources[&source_id];
        let data_path = resolve_land_layer_geojson_path(
            project_dir,
            &source_id,
            &layer.name,
            &source.path,
        )?;
        let meta = std::fs::metadata(&data_path)?;
        let modified = meta
            .modified()
            .ok()
            .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
            .map(|d| d.as_nanos())
            .unwrap_or(0);
        let role_tag = land_role_tag(role);
        parts.push(format!(
            "{role_tag}|{source_id}|{}|{modified}|{}",
            layer.layer_key(),
            meta.len()
        ));
    }
    if parts.is_empty() {
        return Ok("none".to_string());
    }
    parts.sort();
    Ok(format!("{:016x}", fnv1a_hash(parts.join("\n").as_bytes())))
}

fn fnv1a_hash(bytes: &[u8]) -> u64 {
    let mut hash = 0xcbf29ce484222325u64;
    for b in bytes {
        hash ^= u64::from(*b);
        hash = hash.wrapping_mul(0x100000001b3);
    }
    hash
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn build_eligible_geometry_under_100ms() {
        use std::time::{Duration, Instant};
        let include = Geometry::Polygon(Polygon::new(
            LineString::from(vec![
                geo::Coord { x: -120.0, y: 39.0 },
                geo::Coord { x: -119.0, y: 39.0 },
                geo::Coord { x: -119.0, y: 40.0 },
                geo::Coord { x: -120.0, y: 40.0 },
                geo::Coord { x: -120.0, y: 39.0 },
            ]),
            vec![],
        ));
        let exclude = Geometry::Polygon(Polygon::new(
            LineString::from(vec![
                geo::Coord { x: -119.5, y: 39.5 },
                geo::Coord { x: -119.25, y: 39.5 },
                geo::Coord { x: -119.25, y: 39.75 },
                geo::Coord { x: -119.5, y: 39.75 },
                geo::Coord { x: -119.5, y: 39.5 },
            ]),
            vec![],
        ));
        let t0 = Instant::now();
        let geom = build_eligible_geometry(&include, &exclude);
        assert!(t0.elapsed() < Duration::from_millis(100));
        assert!(!is_empty_geom(&geom));
    }

    #[test]
    fn clipped_collect_skips_distant_parcels_and_exclude() {
        use std::time::Duration;
        let tmp = tempfile::tempdir().expect("tempdir");
        let project = tmp.path();
        std::fs::create_dir_all(project.join("data")).expect("data");
        let geojson = r#"{
          "type":"FeatureCollection",
          "features":[
            {"type":"Feature","properties":{"kind":"near"},"geometry":{"type":"Polygon","coordinates":[[[-114.2,35.1],[-114.1,35.1],[-114.1,35.2],[-114.2,35.2],[-114.2,35.1]]]}},
            {"type":"Feature","properties":{"kind":"far"},"geometry":{"type":"Polygon","coordinates":[[[-120.2,42.1],[-120.1,42.1],[-120.1,42.2],[-120.2,42.2],[-120.2,42.1]]]}}
          ]
        }"#;
        std::fs::write(project.join("data/include.geojson"), geojson).expect("include");
        let exclude = r#"{
          "type":"FeatureCollection",
          "features":[
            {"type":"Feature","properties":{},"geometry":{"type":"Polygon","coordinates":[[[-114.16,35.14],[-114.14,35.14],[-114.14,35.16],[-114.16,35.16],[-114.16,35.14]]]}}
          ]
        }"#;
        std::fs::write(project.join("data/exclude.geojson"), exclude).expect("exclude");
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
"#,
        )
        .expect("yaml");

        let clip = LonLatBBox::new(-114.3, 35.0, -114.0, 35.3);
        let t0 = Instant::now();
        let parts = load_or_build_eligible_land_parts(&project.join("config.yaml"), Some(clip))
            .expect("parts");
        assert!(t0.elapsed() < Duration::from_millis(200), "clip collect {:?}", t0.elapsed());
        assert_eq!(parts.include.0.len(), 1);
        assert_eq!(parts.exclude.0.len(), 1);
        let idx = parts.index();
        assert!(idx.contains(-114.18, 35.18));
        assert!(!idx.contains(-114.15, 35.15));
        assert!(!idx.contains(-120.15, 42.15));
    }
}
