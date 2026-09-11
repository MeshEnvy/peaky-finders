//! Scan region: corridor strip or GeoJSON polygon.

use std::fs;
use std::path::Path;
use std::str::FromStr;

use anyhow::{bail, Context, Result};
use geo::{Contains, Coord, Geometry, LineString, Point, Polygon};
use geojson::GeoJson;
use peaky_preset::Preset;

use crate::hike::{destination_point, haversine_m};

pub const MI_TO_M: f64 = 1609.344;

#[derive(Debug, Clone)]
pub enum ScanRegion {
    /// Full project AOI (or explicit bbox with no clip polygon).
    BboxOnly,
    /// Keep candidates whose snapped summit lies inside this polygon.
    Clip(Polygon<f64>),
}

pub fn initial_bearing_deg(from_lat: f64, from_lon: f64, to_lat: f64, to_lon: f64) -> f64 {
    let lat1 = from_lat.to_radians();
    let lat2 = to_lat.to_radians();
    let dlon = (to_lon - from_lon).to_radians();
    let y = dlon.sin() * lat2.cos();
    let x = lat1.cos() * lat2.sin() - lat1.sin() * lat2.cos() * dlon.cos();
    y.atan2(x).to_degrees().rem_euclid(360.0)
}

/// Geodesic corridor: centerline from ``from`` to ``to``, ``half_width_m`` each side.
pub fn corridor_polygon(
    from_lat: f64,
    from_lon: f64,
    to_lat: f64,
    to_lon: f64,
    half_width_m: f64,
) -> Polygon<f64> {
    let bearing = initial_bearing_deg(from_lat, from_lon, to_lat, to_lon);
    let dist = haversine_m(from_lat, from_lon, to_lat, to_lon);
    let step_m = half_width_m.max(5_000.0);
    let n = ((dist / step_m).ceil() as usize).max(1);
    let left_b = bearing - 90.0;
    let right_b = bearing + 90.0;
    let mut left = Vec::with_capacity(n + 1);
    let mut right = Vec::with_capacity(n + 1);
    for i in 0..=n {
        let t = i as f64 / n as f64;
        let along_m = t * dist;
        let (mid_lat, mid_lon) = destination_point(from_lat, from_lon, bearing, along_m);
        let (l_lat, l_lon) = destination_point(mid_lat, mid_lon, left_b, half_width_m);
        let (r_lat, r_lon) = destination_point(mid_lat, mid_lon, right_b, half_width_m);
        left.push(Coord { x: l_lon, y: l_lat });
        right.push(Coord { x: r_lon, y: r_lat });
    }
    let mut ring = left;
    ring.extend(right.into_iter().rev());
    if let Some(first) = ring.first().copied() {
        ring.push(first);
    }
    Polygon::new(LineString::from(ring), vec![])
}

pub fn point_in_region(region: &ScanRegion, lat: f64, lon: f64) -> bool {
    match region {
        ScanRegion::BboxOnly => true,
        ScanRegion::Clip(poly) => poly.contains(&Point::new(lon, lat)),
    }
}

pub fn parse_corridor_coords(raw: &str) -> Result<(f64, f64, f64, f64)> {
    let parts: Vec<&str> = raw.split(',').map(str::trim).collect();
    if parts.len() != 4 {
        bail!("corridor expects from_lat,from_lon,to_lat,to_lon (4 values)");
    }
    let from_lat: f64 = parts[0].parse().context("corridor from_lat")?;
    let from_lon: f64 = parts[1].parse().context("corridor from_lon")?;
    let to_lat: f64 = parts[2].parse().context("corridor to_lat")?;
    let to_lon: f64 = parts[3].parse().context("corridor to_lon")?;
    Ok((from_lat, from_lon, to_lat, to_lon))
}

pub fn resolve_corridor_sites(preset: &Preset, raw: &str) -> Result<(f64, f64, f64, f64)> {
    let parts: Vec<&str> = raw.split(',').map(str::trim).collect();
    if parts.len() != 2 {
        bail!("corridor-sites expects from_slug,to_slug");
    }
    let from = site_loc(preset, parts[0])?;
    let to = site_loc(preset, parts[1])?;
    Ok((from.0, from.1, to.0, to.1))
}

fn site_loc(preset: &Preset, slug: &str) -> Result<(f64, f64)> {
    let site = preset
        .sites
        .get(slug)
        .with_context(|| format!("site not found: {slug}"))?;
    Ok((site.lat(), site.lon()))
}

pub fn load_polygon_file(path: &Path) -> Result<Polygon<f64>> {
    let text = fs::read_to_string(path).with_context(|| format!("read {}", path.display()))?;
    geometry_to_polygon(GeoJson::from_str(&text).context("parse GeoJSON")?)
}

fn geometry_to_polygon(gj: GeoJson) -> Result<Polygon<f64>> {
    let geom = match gj {
        GeoJson::Geometry(g) => Geometry::<f64>::try_from(g).context("GeoJSON geometry")?,
        GeoJson::Feature(f) => {
            let g = f.geometry.context("Feature missing geometry")?;
            Geometry::<f64>::try_from(g).context("Feature geometry")?
        }
        GeoJson::FeatureCollection(fc) => {
            for feat in fc.features {
                if let Some(g) = feat.geometry {
                    if let Ok(Geometry::Polygon(p)) = Geometry::<f64>::try_from(g) {
                        return Ok(p);
                    }
                }
            }
            bail!("FeatureCollection has no Polygon");
        }
    };
    match geom {
        Geometry::Polygon(p) => Ok(p),
        other => bail!("expected Polygon, got {:?}", other),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn corridor_contains_centerline_excludes_far_flank() {
        let poly = corridor_polygon(38.0, -117.0, 36.0, -115.0, 50_000.0);
        assert!(point_in_region(
            &ScanRegion::Clip(poly.clone()),
            37.0,
            -116.0
        ));
        assert!(!point_in_region(
            &ScanRegion::Clip(poly),
            37.0,
            -118.5
        ));
    }

    #[test]
    fn corridor_width_respects_half_width() {
        let from = (38.09469, -117.1873);
        let to = (36.016, -115.234);
        let half = 50.0 * MI_TO_M;
        let poly = corridor_polygon(from.0, from.1, to.0, to.1, half);
        let bearing = initial_bearing_deg(from.0, from.1, to.0, to.1);
        let (mid_lat, mid_lon) = destination_point(from.0, from.1, bearing, half);
        assert!(point_in_region(
            &ScanRegion::Clip(poly.clone()),
            mid_lat,
            mid_lon
        ));
        let (far_lat, far_lon) = destination_point(mid_lat, mid_lon, bearing + 90.0, half * 1.5);
        assert!(!point_in_region(&ScanRegion::Clip(poly), far_lat, far_lon));
    }
}
