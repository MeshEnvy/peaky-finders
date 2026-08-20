//! Route waypoint loading, dedupe, and simplification.

use std::fs;
use std::path::Path;

use anyhow::{bail, Context, Result};
use peaky_geo::{parse_kml_linestring_routes, parse_kmz_linestring_routes};
use splatter::propagate::haversine_m;

use crate::ROUTE_DEDUPE_M;

#[derive(Debug, Clone, PartialEq)]
pub struct Waypoint {
    pub lat: f64,
    pub lon: f64,
}

#[derive(Debug, Clone)]
pub struct Route {
    pub name: String,
    pub waypoints: Vec<Waypoint>,
}

pub fn load_route(path: &Path, simplify_m: f64) -> Result<Route> {
    let data = fs::read(path).with_context(|| format!("read route {}", path.display()))?;
    let lower = path
        .extension()
        .and_then(|s| s.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    let (routes, skipped) = if lower == "kmz" {
        parse_kmz_linestring_routes(&data)?
    } else {
        parse_kml_linestring_routes(&data)?
    };
    if routes.is_empty() {
        bail!("no LineString routes in {} (skipped {skipped} placemarks)", path.display());
    }
    if routes.len() > 1 {
        eprintln!(
            "finder: {} routes in KML; using first ({})",
            routes.len(),
            routes[0].name
        );
    }
    let mut waypoints: Vec<Waypoint> = routes[0]
        .waypoints
        .iter()
        .map(|w| Waypoint {
            lat: w.lat,
            lon: w.lon,
        })
        .collect();
    waypoints = dedupe_consecutive(&waypoints, ROUTE_DEDUPE_M);
    if simplify_m > 0.0 {
        waypoints = simplify_min_distance(&waypoints, simplify_m);
    }
    if waypoints.len() < 2 {
        bail!("route needs at least 2 waypoints after dedupe/simplify");
    }
    Ok(Route {
        name: routes[0].name.clone(),
        waypoints,
    })
}

pub fn dedupe_consecutive(waypoints: &[Waypoint], min_sep_m: f64) -> Vec<Waypoint> {
    let mut out: Vec<Waypoint> = Vec::new();
    for wp in waypoints {
        if let Some(last) = out.last() {
            if haversine_m(last.lat, last.lon, wp.lat, wp.lon) < min_sep_m {
                continue;
            }
        }
        out.push(wp.clone());
    }
    if waypoints.len() >= 2 {
        if let (Some(first), Some(last_in)) = (out.first(), waypoints.last()) {
            if out.len() >= 2
                && haversine_m(first.lat, first.lon, last_in.lat, last_in.lon) < min_sep_m
            {
                if haversine_m(
                    out[out.len() - 1].lat,
                    out[out.len() - 1].lon,
                    last_in.lat,
                    last_in.lon,
                ) >= min_sep_m
                {
                    // loop closure duplicate at end
                }
            }
        }
    }
    out
}

/// Drop vertices closer than `min_dist_m` to the previous kept vertex.
pub fn simplify_min_distance(waypoints: &[Waypoint], min_dist_m: f64) -> Vec<Waypoint> {
    if waypoints.is_empty() {
        return Vec::new();
    }
    let mut out = vec![waypoints[0].clone()];
    for wp in waypoints.iter().skip(1) {
        let last = out.last().unwrap();
        if haversine_m(last.lat, last.lon, wp.lat, wp.lon) >= min_dist_m {
            out.push(wp.clone());
        }
    }
    let last_orig = waypoints.last().unwrap();
    let last_out = out.last().unwrap();
    if haversine_m(last_out.lat, last_out.lon, last_orig.lat, last_orig.lon) >= ROUTE_DEDUPE_M {
        out.push(last_orig.clone());
    }
    out
}

pub fn route_kml_digest(path: &Path) -> Result<String> {
    let data = fs::read(path)?;
    use sha2::{Digest, Sha256};
    let mut hasher = Sha256::new();
    hasher.update(&data);
    Ok(format!("{:x}", hasher.finalize()))
}

pub fn waypoint_key(wp: &Waypoint) -> String {
    format!("{:.6},{:.6}", wp.lat, wp.lon)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn wp(lat: f64, lon: f64) -> Waypoint {
        Waypoint { lat, lon }
    }

    #[test]
    fn dedupe_drops_near_duplicates() {
        let wps = vec![
            wp(39.0, -119.0),
            wp(39.00001, -119.00001),
            wp(39.1, -119.1),
        ];
        let out = dedupe_consecutive(&wps, 50.0);
        assert_eq!(out.len(), 2);
    }

    #[test]
    fn simplify_min_distance_collapses_dense() {
        let wps = vec![
            wp(39.0, -119.0),
            wp(39.001, -119.0),
            wp(39.05, -119.0),
            wp(39.10, -119.0),
        ];
        let out = simplify_min_distance(&wps, 5000.0);
        assert!(out.len() <= 3);
        assert_eq!(out.first().unwrap().lat, 39.0);
    }
}
