//! Compute paved→park→pad access for an arbitrary destination.
//!
//! Place/site access is **display routing**, not peak eligibility. Existing sites
//! always get jeep + hike paths when OSM/DEM can build them. Peak catalog gates
//! (0.5 mi hike, slope cap, land) live only in `peaky peaks` filtering.
//!
//! Pathfinding knobs come from ``access/_meta.yaml``.

use std::path::Path;
use std::sync::Arc;

use anyhow::{Context, Result};
use peaky_preset::{
    ensure_access_meta, place_access_compute_key, upsert_access, AccessMeta, PlaceAccess,
};
use splatter::Session;

use crate::hike::{default_max_slope_deg, stored_peak_hike, HikeSampleElev};
use crate::jeep::{profile_along_polyline, route_jeep_detailed, stored_peak_jeep};
use crate::osm::{build_osm_routing, ensure_osm_pbf, OsmRouting, OsmRoutingOpts};
use crate::park::{nearest_park_hike, select_park_and_hike};

struct SessionElev<'a>(&'a Session);

impl HikeSampleElev for SessionElev<'_> {
    fn sample_elev_m(&self, lat: f64, lon: f64) -> f64 {
        self.0.sample_elev_m(lat, lon)
    }
}

/// Compute access for ``(lat, lon)`` using OSM + DEM and ``access`` meta.
///
/// No peak eligibility gates: long/steep hikes are still stored and drawn.
/// Returns ``None`` only when no jeep road / hike profile / jeep route can be built.
pub fn compute_place_access(
    _project_dir: &Path,
    session: &Session,
    routing: &OsmRouting,
    meta: &AccessMeta,
    lat: f64,
    lon: f64,
) -> Option<PlaceAccess> {
    let elev = SessionElev(session);
    let park_search = meta.hike_path_max_m.min(meta.place_road_search_m);
    let picked = select_park_and_hike(
        &elev,
        &routing.jeep_roads,
        Some(&routing.paved),
        lat,
        lon,
        park_search,
        default_max_slope_deg(),
        meta.hike_path_max_m,
        meta.profile_sample_m,
        false,
    )
    .or_else(|| {
        nearest_park_hike(
            &elev,
            &routing.jeep_roads,
            Some(&routing.paved),
            lat,
            lon,
            meta.place_road_search_m,
            default_max_slope_deg(),
            meta.hike_path_max_m,
            meta.profile_sample_m,
            false,
        )
    })?;
    let (road_lat, road_lon, hike_detail) = (picked.road_lat, picked.road_lon, picked.hike);
    let jeep_route = route_jeep_detailed(
        &routing.graph,
        &routing.paved,
        road_lat,
        road_lon,
        meta.max_jeep_m,
    )?;
    let jeep_detail = profile_along_polyline(
        &elev,
        &jeep_route.coords,
        meta.profile_sample_m,
        &jeep_route.road_segments,
    )?;
    Some(PlaceAccess {
        compute_key: Some(place_access_compute_key(meta)),
        paved_loc: Some([jeep_route.paved_lat, jeep_route.paved_lon]),
        road_loc: Some([road_lat, road_lon]),
        jeep_m: Some((jeep_detail.horiz_m * 10.0).round() / 10.0),
        jeep: Some(stored_peak_jeep(&jeep_detail)),
        hike_m: Some((hike_detail.hike_m_3d * 10.0).round() / 10.0),
        hike: Some(stored_peak_hike(&hike_detail)),
        max_slope_deg: Some((hike_detail.max_slope_deg * 10.0).round() / 10.0),
    })
}

/// Ensure OSM routing for a small pad around ``(lat, lon)``.
pub fn load_routing_near(
    project_dir: &Path,
    lat: f64,
    lon: f64,
    pad_deg: f64,
    force_osm: bool,
    opts: &OsmRoutingOpts,
) -> Result<OsmRouting> {
    let pbf = ensure_osm_pbf(project_dir, force_osm)?;
    let west = lon - pad_deg;
    let east = lon + pad_deg;
    let south = lat - pad_deg;
    let north = lat + pad_deg;
    build_osm_routing(&pbf, west, south, east, north, opts)
}

/// Warm (compute + write) access for a place slug at ``loc``.
pub fn warm_place_access(
    config_path: &Path,
    session: &Arc<Session>,
    slug: &str,
    lat: f64,
    lon: f64,
) -> Result<Option<PlaceAccess>> {
    let project_dir = config_path.parent().context("config path has no parent")?;
    let meta = ensure_access_meta(config_path)?;
    let opts = OsmRoutingOpts::from(&meta);
    // ~0.5° pad (~55 km) for jeep routing around the site.
    let routing = load_routing_near(project_dir, lat, lon, 0.5, false, &opts)?;
    warm_place_access_with_routing(config_path, session, &routing, &meta, slug, lat, lon)
}

/// Warm access using a shared OSM routing graph (avoids re-parsing the PBF).
pub fn warm_place_access_with_routing(
    config_path: &Path,
    session: &Arc<Session>,
    routing: &OsmRouting,
    meta: &AccessMeta,
    slug: &str,
    lat: f64,
    lon: f64,
) -> Result<Option<PlaceAccess>> {
    let project_dir = config_path.parent().context("config path has no parent")?;
    session
        .ensure_tiles_for_points(&[(lat, lon)], meta.place_road_search_m)
        .context("preload DEM for access warm")?;
    let Some(access) = compute_place_access(project_dir, session.as_ref(), routing, meta, lat, lon)
    else {
        return Ok(None);
    };
    upsert_access(config_path, slug, &access)?;
    Ok(Some(access))
}

/// Load OSM routing covering all ``points`` plus ``pad_deg``.
pub fn load_routing_for_points(
    project_dir: &Path,
    points: &[(f64, f64)],
    pad_deg: f64,
    force_osm: bool,
    opts: &OsmRoutingOpts,
) -> Result<OsmRouting> {
    if points.is_empty() {
        anyhow::bail!("no points for OSM routing");
    }
    let mut west = f64::INFINITY;
    let mut east = f64::NEG_INFINITY;
    let mut south = f64::INFINITY;
    let mut north = f64::NEG_INFINITY;
    for &(lat, lon) in points {
        west = west.min(lon);
        east = east.max(lon);
        south = south.min(lat);
        north = north.max(lat);
    }
    let pbf = ensure_osm_pbf(project_dir, force_osm)?;
    build_osm_routing(
        &pbf,
        west - pad_deg,
        south - pad_deg,
        east + pad_deg,
        north + pad_deg,
        opts,
    )
}
