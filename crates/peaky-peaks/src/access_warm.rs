//! Compute paved→park→trail→bushwhack access for an arbitrary destination.
//!
//! Place/site access is **display routing**, not peak eligibility. Same hike
//! pathfinder as `peaky peaks`; catalog gates (0.5 mi road, slope/length, land)
//! only accept or reject a candidate after the path exists.
//!
//! Pathfinding knobs come from ``Preset::resolved_access_meta()`` (project config).

use std::path::{Path, PathBuf};
use std::sync::{Arc, OnceLock};

use anyhow::{Context, Result};
use peaky_preset::{
    load_preset, probe_key, probe_prefs_key, AccessMeta, AccessPreferenceConfig, AccessProbe,
    PeaksDifficultyConfig, PeaksScanConfig, PlaceAccess,
};
use splatter::Session;

use crate::access_probe::{probe, AccessContext};
use crate::gtlf::{
    apply_gtlf_to_routing_progress, ensure_gtlf_cache, gtlf_cache_fingerprint,
};
use crate::hike::HikeSampleElev;
use crate::jeep::{JeepLengthBudget, PavedReachTree};
use crate::osm::{
    build_osm_routing_with_progress, ensure_osm_pbf, osm_cache_path, OsmParsePhase, OsmRouting,
    OsmRoutingOpts, OSM_BUILD_PAD_DEG,
};
use crate::osm_cache::{
    save_merged_routing_cache, save_osm_routing_cache, try_load_merged_routing_cache,
    try_load_osm_routing_cache,
};
use crate::trail::TrailRouting;

struct SessionElev<'a>(&'a Session);

impl HikeSampleElev for SessionElev<'_> {
    fn sample_elev_m(&self, lat: f64, lon: f64) -> f64 {
        self.0.sample_elev_m(lat, lon)
    }
}

/// Paved-reach trees built once per routing load (unconstrained + budget-gated).
#[derive(Debug)]
pub struct RoutingTrees {
    pub unconstrained: PavedReachTree,
    pub budgeted: PavedReachTree,
}

/// OSM jeep graph plus optional GTLF foot-trail graph (merged in memory after cache hit).
pub struct ProjectRouting {
    pub osm: OsmRouting,
    pub trails: TrailRouting,
    pub gtlf_fingerprint: String,
    trees: OnceLock<RoutingTrees>,
}

impl ProjectRouting {
    pub fn osm_only(osm: OsmRouting) -> Self {
        Self {
            osm,
            trails: TrailRouting::empty(),
            gtlf_fingerprint: "disabled".to_string(),
            trees: OnceLock::new(),
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

    /// Build (once) and return paved-reach trees for the given jeep search + budget.
    pub fn trees(&self, max_jeep_m: f64, jeep_budget: &JeepLengthBudget) -> &RoutingTrees {
        self.trees.get_or_init(|| RoutingTrees {
            unconstrained: PavedReachTree::build(
                &self.osm.graph,
                &self.osm.paved,
                max_jeep_m,
                None,
            ),
            budgeted: PavedReachTree::build(
                &self.osm.graph,
                &self.osm.paved,
                max_jeep_m,
                Some(jeep_budget),
            ),
        })
    }
}

fn merge_gtlf<F>(
    project_dir: &Path,
    mut osm: OsmRouting,
    west: f64,
    south: f64,
    east: f64,
    north: f64,
    opts: &OsmRoutingOpts,
    force_fetch: bool,
    skip_jeep_merge: bool,
    progress: &mut F,
) -> Result<ProjectRouting>
where
    F: FnMut(f64),
{
    let bundle = apply_gtlf_to_routing_progress(
        project_dir,
        &mut osm,
        opts,
        west,
        south,
        east,
        north,
        force_fetch,
        skip_jeep_merge,
        progress,
    )?;
    Ok(ProjectRouting {
        osm,
        trails: bundle.foot_trails,
        gtlf_fingerprint: bundle.fingerprint,
        trees: OnceLock::new(),
    })
}

fn coord5(v: f64) -> String {
    format!("{:.5}", v)
}

fn probe_cache_dir(project_dir: &Path, prefs_key: &str) -> PathBuf {
    project_dir
        .join(".peaky/cache/probe")
        .join(prefs_key)
}

pub fn probe_cache_path(
    project_dir: &Path,
    prefs_key: &str,
    lat: f64,
    lon: f64,
) -> PathBuf {
    probe_cache_dir(project_dir, prefs_key)
        .join(format!("{}_{}.json", coord5(lat), coord5(lon)))
}

pub fn load_probe_cache(path: &Path) -> Option<AccessProbe> {
    let bytes = std::fs::read(path).ok()?;
    serde_json::from_slice(&bytes).ok()
}

pub fn save_probe_cache(path: &Path, probe: &AccessProbe) -> Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)
            .with_context(|| format!("create probe cache dir {}", parent.display()))?;
    }
    let bytes = serde_json::to_vec(probe).context("serialize probe cache")?;
    std::fs::write(path, bytes).with_context(|| format!("write probe cache {}", path.display()))?;
    Ok(())
}

fn access_context<'a>(
    routing: &'a ProjectRouting,
    trees: &'a RoutingTrees,
    meta: &AccessMeta,
    _scan: &PeaksScanConfig,
    max_jeep_m: f64,
    max_slope_deg: f64,
    jeep_budget: JeepLengthBudget,
    preference: AccessPreferenceConfig,
    difficulty: PeaksDifficultyConfig,
) -> AccessContext<'a> {
    AccessContext {
        routing: routing.osm(),
        trails: routing.trails_opt(),
        tree: &trees.unconstrained,
        tree_budgeted: &trees.budgeted,
        max_jeep_m,
        hike_path_max_m: meta.hike_path_max_m,
        profile_sample_m: meta.profile_sample_m,
        max_slope_deg,
        jeep_budget,
        preference,
        difficulty,
    }
}

/// Run the unified probe, using disk cache when ``prefs_key`` matches.
pub fn resolve_probe<E: HikeSampleElev>(
    project_dir: &Path,
    elev: &E,
    routing: &ProjectRouting,
    meta: &AccessMeta,
    scan: &PeaksScanConfig,
    lat: f64,
    lon: f64,
    max_jeep_m: f64,
    max_slope_deg: f64,
    jeep_budget: JeepLengthBudget,
) -> Option<AccessProbe> {
    let pbf_fp = project_pbf_fingerprint(project_dir);
    let prefs_key = probe_prefs_key(meta, scan, &routing.gtlf_fingerprint, &pbf_fp);
    let cache_path = probe_cache_path(project_dir, &prefs_key, lat, lon);
    if let Some(mut cached) = load_probe_cache(&cache_path) {
        if cached.probe_key.is_none() {
            cached.probe_key = Some(probe_key(
                lat,
                lon,
                meta,
                scan,
                &routing.gtlf_fingerprint,
                &pbf_fp,
            ));
        }
        return Some(cached);
    }

    let trees = routing.trees(max_jeep_m, &jeep_budget);
    let ctx = access_context(
        routing,
        trees,
        meta,
        scan,
        max_jeep_m,
        max_slope_deg,
        jeep_budget,
        scan.preference.clone(),
        scan.difficulty.clone(),
    );
    let mut result = probe(elev, lat, lon, &ctx)?;
    result.probe_key = Some(probe_key(
        lat,
        lon,
        meta,
        scan,
        &routing.gtlf_fingerprint,
        &pbf_fp,
    ));
    if let Err(e) = save_probe_cache(&cache_path, &result) {
        tracing::warn!("probe cache write: {e:#}");
    }
    Some(result)
}

/// Compute access for ``(lat, lon)`` using OSM + GTLF + DEM and ``access`` meta.
///
/// Display routing: jeep from pavement, optional foot trail, then bushwhack.
/// Returns ``None`` only when the loaded graph has neither paved nor jeep roads.
pub fn compute_place_access(
    project_dir: &Path,
    session: &Session,
    routing: &ProjectRouting,
    meta: &AccessMeta,
    lat: f64,
    lon: f64,
) -> Option<PlaceAccess> {
    let preset_path = project_dir.join("config.yaml");
    let preset = load_preset(&preset_path).unwrap_or_default();
    let scan = preset.scan.peaks.clone();
    let max_slope_deg = preset.peak_access_rules().max_slope_deg;
    let jeep_budget = JeepLengthBudget::from_extreme(&scan.difficulty.jeep.extreme);
    let elev = SessionElev(session);
    resolve_probe(
        project_dir,
        &elev,
        routing,
        meta,
        &scan,
        lat,
        lon,
        display_jeep_max_m(),
        max_slope_deg,
        jeep_budget,
    )
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
    let west = lon - pad_deg;
    let east = lon + pad_deg;
    let south = lat - pad_deg;
    let north = lat + pad_deg;
    load_routing_for_bbox_progress(
        project_dir,
        west,
        south,
        east,
        north,
        force_osm,
        opts,
        &mut |_, _| {},
    )
}

/// Warm (compute + write) access for a place slug at ``loc``.
pub fn warm_place_access(
    config_path: &Path,
    session: &Arc<Session>,
    _slug: &str,
    lat: f64,
    lon: f64,
) -> Result<Option<PlaceAccess>> {
    let project_dir = config_path.parent().context("config path has no parent")?;
    let preset = load_preset(config_path)?;
    let meta = preset.resolved_access_meta();
    let opts = OsmRoutingOpts::from(&meta);
    let routing = load_routing_near(project_dir, lat, lon, 0.5, false, &opts)?;
    warm_place_access_with_routing(config_path, session, &routing, &meta, lat, lon)
}

/// Warm access using a shared routing graph (avoids re-parsing the PBF).
pub fn warm_place_access_with_routing(
    config_path: &Path,
    session: &Arc<Session>,
    routing: &ProjectRouting,
    meta: &AccessMeta,
    lat: f64,
    lon: f64,
) -> Result<Option<PlaceAccess>> {
    let project_dir = config_path.parent().context("config path has no parent")?;
    session
        .ensure_tiles_for_points(&[(lat, lon)], meta.place_road_search_m)
        .context("preload DEM for access warm")?;
    Ok(compute_place_access(
        project_dir,
        session.as_ref(),
        routing,
        meta,
        lat,
        lon,
    ))
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
    load_routing_for_bbox_progress(
        project_dir, west, south, east, north, force_osm, opts, progress,
    )
}

/// Load OSM + GTLF routing covering ``west,south,east,north``.
pub fn load_routing_for_bbox(
    project_dir: &Path,
    west: f64,
    south: f64,
    east: f64,
    north: f64,
    force_osm: bool,
    opts: &OsmRoutingOpts,
) -> Result<ProjectRouting> {
    load_routing_for_bbox_progress(
        project_dir,
        west,
        south,
        east,
        north,
        force_osm,
        opts,
        &mut |_, _| {},
    )
}

pub fn load_routing_for_bbox_progress<F>(
    project_dir: &Path,
    west: f64,
    south: f64,
    east: f64,
    north: f64,
    force_osm: bool,
    opts: &OsmRoutingOpts,
    progress: &mut F,
) -> Result<ProjectRouting>
where
    F: FnMut(OsmParsePhase, f64),
{
    let pbf = ensure_osm_pbf(project_dir, force_osm)?;
    progress(OsmParsePhase::Cache, 0.06);
    if !force_osm {
        if let Err(e) = ensure_gtlf_cache(project_dir, false) {
            tracing::warn!("gtlf cache ensure: {e:#}");
        }
    } else if let Err(e) = ensure_gtlf_cache(project_dir, true) {
        tracing::warn!("gtlf cache refresh: {e:#}");
    }
    let gtlf_fp = project_gtlf_fingerprint(project_dir);
    let (cwest, csouth, ceast, cnorth) = routing_coverage_bbox(west, south, east, north);
    if !force_osm {
        if let Some(osm) =
            try_load_merged_routing_cache(&pbf, west, south, east, north, opts, &gtlf_fp)
        {
            progress(OsmParsePhase::Gtlf, 0.92);
            let loaded = merge_gtlf(
                project_dir,
                osm,
                west,
                south,
                east,
                north,
                opts,
                false,
                true,
                &mut |_| {},
            )?;
            progress(OsmParsePhase::Ready, 1.0);
            return Ok(loaded);
        }
    }
    progress(OsmParsePhase::Cache, 0.12);
    let osm = if !force_osm {
        if let Some(routing) = try_load_osm_routing_cache(&pbf, west, south, east, north, opts) {
            progress(OsmParsePhase::Cache, 0.48);
            routing
        } else {
            let routing =
                build_osm_routing_with_progress(&pbf, west, south, east, north, opts, progress)?;
            if let Err(e) = save_osm_routing_cache(&pbf, cwest, csouth, ceast, cnorth, opts, &routing)
            {
                tracing::warn!("osm routing cache write: {e:#}");
            }
            routing
        }
    } else {
        let routing =
            build_osm_routing_with_progress(&pbf, west, south, east, north, opts, progress)?;
        if let Err(e) = save_osm_routing_cache(&pbf, cwest, csouth, ceast, cnorth, opts, &routing) {
            tracing::warn!("osm routing cache write: {e:#}");
        }
        routing
    };
    progress(OsmParsePhase::Gtlf, 0.52);
    let loaded = merge_gtlf(
        project_dir,
        osm,
        west,
        south,
        east,
        north,
        opts,
        force_osm,
        false,
        &mut |frac| {
            progress(OsmParsePhase::Gtlf, 0.52 + 0.45 * frac.clamp(0.0, 1.0));
        },
    )?;
    if loaded.gtlf_fingerprint != "disabled" {
        if let Err(e) = save_merged_routing_cache(
            &pbf,
            cwest,
            csouth,
            ceast,
            cnorth,
            opts,
            &loaded.osm,
            &loaded.gtlf_fingerprint,
        ) {
            tracing::warn!("merged routing cache write: {e:#}");
        }
    }
    progress(OsmParsePhase::Ready, 1.0);
    Ok(loaded)
}

/// GTLF cache fingerprint for compute keys (``disabled`` when source off).
pub fn project_gtlf_fingerprint(project_dir: &Path) -> String {
    let preset_path = project_dir.join("config.yaml");
    let preset = load_preset(&preset_path).unwrap_or_default();
    gtlf_cache_fingerprint(project_dir, &preset.sources.gtlf)
}

/// OSM PBF cache fingerprint for probe prefs keys (no download).
pub fn project_pbf_fingerprint(project_dir: &Path) -> String {
    let preset_path = project_dir.join("config.yaml");
    let preset = load_preset(&preset_path).unwrap_or_default();
    let path = osm_cache_path(project_dir, &preset.sources.osm.cache_name);
    if !path.is_file() {
        return "missing".to_string();
    }
    let meta = match std::fs::metadata(&path) {
        Ok(m) => m,
        Err(_) => return "missing".to_string(),
    };
    let len = meta.len();
    let mtime = meta
        .modified()
        .ok()
        .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|d| d.as_secs())
        .unwrap_or(0);
    format!("{len}:{mtime}")
}
