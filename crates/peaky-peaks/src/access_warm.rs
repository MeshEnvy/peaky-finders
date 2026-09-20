//! Compute paved→park→trail→bushwhack access for an arbitrary destination.
//!
//! Place/site access is **display routing**, not peak eligibility. Same hike
//! pathfinder as `peaky peaks`; catalog gates (0.5 mi road, slope/length, land)
//! only accept or reject a candidate after the path exists.
//!
//! Pathfinding knobs come from ``access/_meta.yaml``.

use std::path::Path;
use std::sync::Arc;

use anyhow::{Context, Result};
use peaky_preset::{ensure_access_meta, load_preset, place_access_compute_key, upsert_access, AccessMeta, PlaceAccess};
use splatter::Session;

use crate::access::{build_access_from_park_display, build_access_from_paved_approach};
use crate::gtlf::{apply_gtlf_to_routing, gtlf_cache_fingerprint};
use crate::hike::{default_max_slope_deg, HikeSampleElev};
use crate::osm::{
    build_osm_routing, build_osm_routing_with_progress, ensure_osm_pbf, OsmParsePhase, OsmRouting,
    OsmRoutingOpts, OSM_BUILD_PAD_DEG,
};
use crate::osm_cache::{save_osm_routing_cache, try_load_osm_routing_cache};
use crate::park::nearest_park_hike;
use crate::trail::TrailRouting;

struct SessionElev<'a>(&'a Session);

impl HikeSampleElev for SessionElev<'_> {
    fn sample_elev_m(&self, lat: f64, lon: f64) -> f64 {
        self.0.sample_elev_m(lat, lon)
    }
}

/// OSM jeep graph plus optional GTLF foot-trail graph (merged in memory after cache hit).
pub struct ProjectRouting {
    pub osm: OsmRouting,
    pub trails: TrailRouting,
    pub gtlf_fingerprint: String,
}

impl ProjectRouting {
    pub fn osm_only(osm: OsmRouting) -> Self {
        Self {
            osm,
            trails: TrailRouting::empty(),
            gtlf_fingerprint: "disabled".to_string(),
        }
    }

    pub fn osm(&self) -> &OsmRouting {
        &self.osm
    }

    pub fn trails_opt(&self) -> Option<&TrailRouting> {
        if self.trails.is_empty() {
            None
        } else {
            Some(&self.trails)
        }
    }
}

fn merge_gtlf(
    project_dir: &Path,
    mut osm: OsmRouting,
    west: f64,
    south: f64,
    east: f64,
    north: f64,
    opts: &OsmRoutingOpts,
    force_fetch: bool,
) -> Result<ProjectRouting> {
    let bundle = apply_gtlf_to_routing(
        project_dir, &mut osm, opts, west, south, east, north, force_fetch,
    )?;
    Ok(ProjectRouting {
        osm,
        trails: bundle.foot_trails,
        gtlf_fingerprint: bundle.fingerprint,
    })
}

fn place_from_built(
    meta: &AccessMeta,
    gtlf_fingerprint: &str,
    built: crate::access::BuiltAccess,
) -> PlaceAccess {
    PlaceAccess {
        compute_key: Some(place_access_compute_key(meta, gtlf_fingerprint)),
        paved_loc: Some([built.paved_lat, built.paved_lon]),
        road_loc: Some([built.road_lat, built.road_lon]),
        jeep_m: Some((built.jeep_m * 10.0).round() / 10.0),
        jeep: Some(built.jeep),
        hike_m: Some((built.hike_m * 10.0).round() / 10.0),
        hike: Some(built.hike),
        trail_loc: if built.trail_m > crate::access::LEG_MIN_M {
            Some([built.trail_lat, built.trail_lon])
        } else {
            None
        },
        trail_m: if built.trail_m > crate::access::LEG_MIN_M {
            Some((built.trail_m * 10.0).round() / 10.0)
        } else {
            None
        },
        trail: if built.trail_m > crate::access::LEG_MIN_M {
            Some(built.trail)
        } else {
            None
        },
        max_slope_deg: Some((built.max_slope_deg * 10.0).round() / 10.0),
    }
}

/// Compute access for ``(lat, lon)`` using OSM + GTLF + DEM and ``access`` meta.
///
/// Display routing: jeep from pavement, optional foot trail, then bushwhack.
/// Returns ``None`` only when the loaded graph has neither paved nor jeep roads.
pub fn compute_place_access(
    _project_dir: &Path,
    session: &Session,
    routing: &ProjectRouting,
    meta: &AccessMeta,
    lat: f64,
    lon: f64,
) -> Option<PlaceAccess> {
    let elev = SessionElev(session);
    let jeep_max = display_jeep_max_m();
    if let Some(built) = build_access_from_paved_approach(
        &elev,
        routing.osm(),
        routing.trails_opt(),
        lat,
        lon,
        jeep_max,
        jeep_max,
        meta.profile_sample_m,
    ) {
        return Some(place_from_built(meta, &routing.gtlf_fingerprint, built));
    }
    let picked = nearest_park_hike(
        &elev,
        &routing.osm.jeep_roads,
        Some(&routing.osm.paved),
        lat,
        lon,
        jeep_max,
        default_max_slope_deg(),
        jeep_max,
        meta.profile_sample_m,
        false,
    )?;
    let built = build_access_from_park_display(
        &elev,
        routing.osm(),
        &picked,
        jeep_max,
        meta.profile_sample_m,
    );
    Some(place_from_built(meta, &routing.gtlf_fingerprint, built))
}

/// Pad around a place for jeep routing (~55 km). Used by serve cache + warm.
pub const OSM_ROUTE_PAD_DEG: f64 = 0.5;

/// Jeep budget for site/pin display: the loaded OSM pad, not the catalog 20 km search.
pub fn display_jeep_max_m() -> f64 {
    OSM_ROUTE_PAD_DEG * 111_320.0
}

/// Axis-aligned bbox covering ``points`` plus ``pad_deg`` (before the build pad).
pub fn routing_bbox_for_points(points: &[(f64, f64)], pad_deg: f64) -> Option<(f64, f64, f64, f64)> {
    if points.is_empty() {
        return None;
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
    Some((west - pad_deg, south - pad_deg, east + pad_deg, north + pad_deg))
}

/// Effective coverage after ``build_osm_routing`` adds its inner pad.
pub fn routing_coverage_bbox(west: f64, south: f64, east: f64, north: f64) -> (f64, f64, f64, f64) {
    (
        west - OSM_BUILD_PAD_DEG,
        south - OSM_BUILD_PAD_DEG,
        east + OSM_BUILD_PAD_DEG,
        north + OSM_BUILD_PAD_DEG,
    )
}

/// Ensure OSM + GTLF routing for a small pad around ``(lat, lon)``.
pub fn load_routing_near(
    project_dir: &Path,
    lat: f64,
    lon: f64,
    pad_deg: f64,
    force_osm: bool,
    opts: &OsmRoutingOpts,
) -> Result<ProjectRouting> {
    let pbf = ensure_osm_pbf(project_dir, force_osm)?;
    let west = lon - pad_deg;
    let east = lon + pad_deg;
    let south = lat - pad_deg;
    let north = lat + pad_deg;
    let osm = build_osm_routing(&pbf, west, south, east, north, opts)?;
    merge_gtlf(project_dir, osm, west, south, east, north, opts, force_osm)
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
    let routing = load_routing_near(project_dir, lat, lon, 0.5, false, &opts)?;
    warm_place_access_with_routing(config_path, session, &routing, &meta, slug, lat, lon)
}

/// Warm access using a shared routing graph (avoids re-parsing the PBF).
pub fn warm_place_access_with_routing(
    config_path: &Path,
    session: &Arc<Session>,
    routing: &ProjectRouting,
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
    if !slug.starts_with('_') {
        upsert_access(config_path, slug, &access)?;
    }
    Ok(Some(access))
}

/// Load OSM + GTLF routing covering all ``points`` plus ``pad_deg``.
pub fn load_routing_for_points(
    project_dir: &Path,
    points: &[(f64, f64)],
    pad_deg: f64,
    force_osm: bool,
    opts: &OsmRoutingOpts,
) -> Result<ProjectRouting> {
    load_routing_for_points_progress(project_dir, points, pad_deg, force_osm, opts, &mut |_, _| {})
}

pub fn load_routing_for_points_progress<F>(
    project_dir: &Path,
    points: &[(f64, f64)],
    pad_deg: f64,
    force_osm: bool,
    opts: &OsmRoutingOpts,
    progress: &mut F,
) -> Result<ProjectRouting>
where
    F: FnMut(OsmParsePhase, f64),
{
    let (west, south, east, north) = routing_bbox_for_points(points, pad_deg)
        .ok_or_else(|| anyhow::anyhow!("no points for OSM routing"))?;
    let pbf = ensure_osm_pbf(project_dir, force_osm)?;
    progress(OsmParsePhase::Cache, 0.08);
    let osm = if let Some(routing) =
        try_load_osm_routing_cache(&pbf, west, south, east, north, opts)
    {
        progress(OsmParsePhase::Ready, 1.0);
        routing
    } else {
        let routing =
            build_osm_routing_with_progress(&pbf, west, south, east, north, opts, progress)?;
        let (cwest, csouth, ceast, cnorth) = routing_coverage_bbox(west, south, east, north);
        if let Err(e) = save_osm_routing_cache(&pbf, cwest, csouth, ceast, cnorth, opts, &routing)
        {
            tracing::warn!("osm routing cache write: {e:#}");
        }
        routing
    };
    merge_gtlf(project_dir, osm, west, south, east, north, opts, force_osm)
}

/// GTLF cache fingerprint for compute keys (``disabled`` when source off).
pub fn project_gtlf_fingerprint(project_dir: &Path) -> String {
    let preset_path = project_dir.join("config.yaml");
    let preset = load_preset(&preset_path).unwrap_or_default();
    gtlf_cache_fingerprint(project_dir, &preset.sources.gtlf)
}
