//! Eligible land geometry: include layers union minus exclude layers (GeoJSON).

use std::path::{Path, PathBuf};
use std::panic::{catch_unwind, AssertUnwindSafe};

use anyhow::{bail, Context, Result};
use geo::{BooleanOps, Geometry, LineString, MultiPolygon, Polygon};
use geozero::wkb::Ewkb;
use geozero::{CoordDimensions, ToGeo, ToWkb};
use peaky_preset::{
    load_preset, resolved_preset_cache_dir, LandLayerEntry, LandLayerRole, Preset,
};

use crate::land_path::resolve_land_layer_geojson_path;
use crate::land_query::read_land_layer_features;

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

pub fn land_geometry_is_empty(geom: &Geometry<f64>) -> bool {
    is_empty_geom(geom)
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

fn union_role_layers(
    preset_path: &Path,
    role: LandLayerRole,
    aoi: Option<&Geometry<f64>>,
) -> Result<Geometry<f64>> {
    let preset = load_preset(preset_path)?;
    let project_dir = preset_path
        .parent()
        .context("preset path must have a parent directory")?;
    let mut pieces = Vec::new();
    for (source_id, layer) in iter_land_layer_entries_by_role(&preset, role) {
        let source = &preset.land.sources[&source_id];
        let data_path = resolve_land_layer_geojson_path(
            project_dir,
            &source_id,
            &layer.name,
            &source.path,
        )?;
        let rows = read_land_layer_features(&data_path, &layer)?;
        for (mut geom, _) in rows {
            if let Some(aoi_geom) = aoi {
                if !is_empty_geom(aoi_geom) {
                    geom = geom_intersection(geom, aoi_geom);
                }
            }
            if !is_empty_geom(&geom) {
                pieces.push(geom);
            }
        }
    }
    Ok(unary_union_geometries(pieces))
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
    let empty = empty_polygon();
    if is_empty_geom(include_union) {
        return empty;
    }

    let inc_ll = polygonal_area_only(include_union.clone());
    if is_empty_geom(&inc_ll) {
        return empty;
    }

    if is_empty_geom(exclude_union) {
        return inc_ll;
    }

    let exc_ll = polygonal_area_only(exclude_union.clone());
    if is_empty_geom(&exc_ll) {
        return inc_ll;
    }

    let ig = to_web_mercator(&inc_ll);
    let eg = to_web_mercator(&exc_ll);
    let diff = geom_difference(&ig, &eg);
    if is_empty_geom(&diff) {
        return empty;
    }
    let back = from_web_mercator(&diff);
    let clean = polygonal_area_only(back);
    if is_empty_geom(&clean) {
        empty
    } else {
        clean
    }
}

pub fn build_eligible_land_union(preset_path: &Path) -> Result<Geometry<f64>> {
    let preset = load_preset(preset_path)?;
    if iter_land_layer_entries_by_role(&preset, LandLayerRole::Include).is_empty() {
        return Err(EligibleLandError(
            "Seek requires at least one land layer with role: include".into(),
        )
        .into());
    }

    let aoi_geom = union_role_layers(preset_path, LandLayerRole::Aoi, None)?;
    let aoi_ref = if is_empty_geom(&aoi_geom) {
        None
    } else {
        Some(aoi_geom)
    };
    let include_union = union_role_layers(
        preset_path,
        LandLayerRole::Include,
        aoi_ref.as_ref(),
    )?;
    let exclude_union = union_role_layers(
        preset_path,
        LandLayerRole::Exclude,
        aoi_ref.as_ref(),
    )?;
    let eligible = build_eligible_geometry(&include_union, &exclude_union);
    if is_empty_geom(&eligible) {
        bail!(EligibleLandError(
            "Eligible land geometry is empty after include − exclude".into()
        ));
    }
    Ok(eligible)
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

fn geometry_to_multi_public(geom: &Geometry<f64>) -> MultiPolygon<f64> {
    geometry_to_multi(geom)
}

/// Load eligible land and build the R-tree point filter (once per process).
pub fn load_or_build_eligible_land_filter(
    preset_path: &Path,
) -> Result<(String, splatter::LandFilterIndex, MultiPolygon<f64>)> {
    let (geom, digest) = load_or_build_eligible_land_union(preset_path)?;
    let mp = geometry_to_multi_public(&geom);
    let filter = splatter::LandFilterIndex::from_multipolygon(&mp);
    Ok((digest, filter, mp))
}

fn eligible_memo_path(cache_root: &Path, digest: &str) -> PathBuf {
    cache_root.join(digest).join("union.wkb")
}

fn geometry_from_wkb(bytes: &[u8]) -> Result<Geometry<f64>> {
    Ok(Ewkb(bytes).to_geo().context("parse eligible land WKB")?)
}

fn geometry_to_wkb(geom: &Geometry<f64>) -> Result<Vec<u8>> {
    geom.to_wkb(CoordDimensions::xy())
        .context("encode eligible land WKB")
}

fn try_load_eligible_wkb(path: &Path) -> Option<Geometry<f64>> {
    let bytes = std::fs::read(path).ok()?;
    let geom = geometry_from_wkb(&bytes).ok()?;
    if is_empty_geom(&geom) {
        None
    } else {
        Some(geom)
    }
}

fn find_fallback_eligible_wkb(preset_path: &Path) -> Option<(Geometry<f64>, String)> {
    let cache_root = resolved_eligible_cache_dir(preset_path);
    let entries = std::fs::read_dir(&cache_root).ok()?;
    let mut best: Option<(PathBuf, String, std::time::SystemTime)> = None;
    for entry in entries.flatten() {
        let path = entry.path().join("union.wkb");
        if !path.is_file() {
            continue;
        }
        let digest = entry.file_name().to_string_lossy().into_owned();
        let modified = entry
            .metadata()
            .and_then(|m| m.modified())
            .unwrap_or(std::time::SystemTime::UNIX_EPOCH);
        match &best {
            None => best = Some((path, digest, modified)),
            Some((_, _, prev)) if modified > *prev => {
                best = Some((path, digest, modified));
            }
            _ => {}
        }
    }
    let (path, digest, _) = best?;
    try_load_eligible_wkb(&path).map(|geom| (geom, digest))
}

fn write_eligible_wkb_cache(preset_path: &Path, digest: &str, geom: &Geometry<f64>) {
    let memo_path = eligible_memo_path(&resolved_eligible_cache_dir(preset_path), digest);
    if let Some(parent) = memo_path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    if let Ok(bytes) = geometry_to_wkb(geom) {
        let tmp = memo_path.with_extension("wkb.tmp");
        if std::fs::write(&tmp, bytes).is_ok() {
            let _ = std::fs::rename(&tmp, &memo_path);
        }
    }
}

/// Load cached eligible land (v4 `union.wkb`) or build include − exclude.
pub fn load_or_build_eligible_land_union(
    preset_path: &Path,
) -> Result<(Geometry<f64>, String)> {
    let preset = load_preset(preset_path)?;
    if iter_land_layer_entries_by_role(&preset, LandLayerRole::Include).is_empty() {
        return Err(EligibleLandError(
            "Seek requires at least one land layer with role: include".into(),
        )
        .into());
    }

    let digest = eligible_land_digest(preset_path)?;
    let memo_path = eligible_memo_path(&resolved_eligible_cache_dir(preset_path), &digest);
    if let Some(geom) = try_load_eligible_wkb(&memo_path) {
        return Ok((geom, digest));
    }

    if let Some((geom, cached_digest)) = find_fallback_eligible_wkb(preset_path) {
        return Ok((geom, cached_digest));
    }

    let built = catch_unwind(AssertUnwindSafe(|| build_eligible_land_union(preset_path)));
    match built {
        Ok(Ok(geom)) => {
            write_eligible_wkb_cache(preset_path, &digest, &geom);
            Ok((geom, digest))
        }
        Ok(Err(e)) => Err(e),
        Err(_) => Err(EligibleLandError(
            "Eligible land boolean ops failed (geometry engine panic)".into(),
        )
        .into()),
    }
}

pub fn load_preset_aoi_union(preset_path: &Path) -> Result<Option<Geometry<f64>>> {
    let geom = union_role_layers(preset_path, LandLayerRole::Aoi, None)?;
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
            let role_tag = match role {
                LandLayerRole::Include => "include",
                LandLayerRole::Exclude => "exclude",
                LandLayerRole::Aoi => "aoi",
            };
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
        let role_tag = match role {
            LandLayerRole::Include => "include",
            LandLayerRole::Exclude => "exclude",
            LandLayerRole::Aoi => "aoi",
        };
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
    use std::path::Path;

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
    fn load_nevada_cached_eligible_wkb() {
        let preset_path = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../../ops/peaky_home/projects/nevada/config.yaml");
        if !preset_path.is_file() {
            return;
        }
        let (geom, digest) = load_or_build_eligible_land_union(&preset_path).expect("eligible land");
        assert!(!is_empty_geom(&geom));
        assert!(!digest.is_empty());
    }
}
