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
    ensure_roads_book, load_preset, probe_key, probe_prefs_key, roads_global_fingerprint,
    roads_local_fingerprint, AccessMeta, AccessPreferenceConfig, AccessProbe,
    PeaksDifficultyConfig, PeaksScanConfig, PlaceAccess, RoadCustom, RoadRole, RoadsBook,
};
use splatter::Session;

use crate::access_probe::{
    append_draft_site_access_options, build_site_access_options, probe, AccessContext,
};
use crate::gtlf::{
    apply_gtlf_to_routing_progress, ensure_gtlf_cache, gtlf_cache_fingerprint,
    load_gtlf_display_inventory,
};
use crate::road_inventory::{GtlfRoadInventory, InventoryRole, OsmRoadInventory};
use crate::hike::HikeSampleElev;
use crate::jeep::{JeepLengthBudget, PavedReachTree};
use crate::osm::{
    build_osm_routing_with_progress, ensure_osm_pbf, merge_road_customs_into_routing,
    osm_cache_path, osm_routing_opts_with_roads, OsmParsePhase, OsmRouting, OsmRoutingOpts,
    OSM_BUILD_PAD_DEG,
};
use crate::osm_cache::{
    save_merged_routing_cache, save_osm_routing_cache, try_load_merged_routing_cache,
    try_load_osm_routing_cache, try_load_road_inventory_cache,
};
use crate::network_index::GraphEdgeIndex;
use crate::trail::TrailRouting;

pub struct SessionElev<'a>(pub &'a Session);

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
    pub osm_inventory: OsmRoadInventory,
    pub gtlf_inventory: GtlfRoadInventory,
    pub roads_customs: Vec<RoadCustom>,
    trees: OnceLock<RoutingTrees>,
    draft_trees: OnceLock<RoutingTrees>,
    drive_edge_index: OnceLock<GraphEdgeIndex>,
    trail_edge_index: OnceLock<GraphEdgeIndex>,
}

impl ProjectRouting {
    pub fn osm_only(osm: OsmRouting) -> Self {
        Self {
            osm,
            trails: TrailRouting::empty(),
            gtlf_fingerprint: "disabled".to_string(),
            osm_inventory: OsmRoadInventory::default(),
            gtlf_inventory: GtlfRoadInventory::default(),
            roads_customs: Vec::new(),
            trees: OnceLock::new(),
            draft_trees: OnceLock::new(),
            drive_edge_index: OnceLock::new(),
            trail_edge_index: OnceLock::new(),
        }
    }

    pub(crate) fn drive_edge_index(&self) -> &GraphEdgeIndex {
        self.drive_edge_index
            .get_or_init(|| GraphEdgeIndex::build(&self.osm.graph))
    }

    pub(crate) fn trail_edge_index(&self) -> &GraphEdgeIndex {
        self.trail_edge_index
            .get_or_init(|| GraphEdgeIndex::build(&self.trails.graph))
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
                true,
            ),
            budgeted: PavedReachTree::build(
                &self.osm.graph,
                &self.osm.paved,
                max_jeep_m,
                Some(jeep_budget),
                true,
            ),
        })
    }

    /// Reach trees that include jeep/paved custom edges (draft site options only).
    pub fn draft_trees(&self, max_jeep_m: f64, jeep_budget: &JeepLengthBudget) -> &RoutingTrees {
        self.draft_trees.get_or_init(|| RoutingTrees {
            unconstrained: PavedReachTree::build(
                &self.osm.graph,
                &self.osm.paved,
                max_jeep_m,
                None,
                false,
            ),
            budgeted: PavedReachTree::build(
                &self.osm.graph,
                &self.osm.paved,
                max_jeep_m,
                Some(jeep_budget),
                false,
            ),
        })
    }

    pub fn has_draft_customs(&self) -> bool {
        self.osm
            .segment_groups
            .iter()
            .any(|g| g.is_custom_drive())
    }
}

fn hiking_lines_from_inventory(inv: &OsmRoadInventory) -> Vec<Vec<(f64, f64)>> {
    inv.ways
        .iter()
        .filter(|w| w.role == InventoryRole::Hiking)
        .map(|w| {
            w.coords
                .iter()
                .map(|(lat, lon)| (*lat as f64, *lon as f64))
                .collect()
        })
        .collect()
}

fn custom_hiking_lines(book: &RoadsBook) -> Vec<Vec<(f64, f64)>> {
    book.customs
        .iter()
        .filter(|c| c.role == RoadRole::Hiking && c.coords.len() >= 2)
        .map(|c| c.coords.iter().map(|[lat, lon]| (*lat, *lon)).collect())
        .collect()
}

fn merge_gtlf<F>(
    project_dir: &Path,
    mut osm: OsmRouting,
    west: f64,
    south: f64,
    east: f64,
    north: f64,
    opts: &OsmRoutingOpts,
    roads: &RoadsBook,
    extra_foot_lines: Vec<Vec<(f64, f64)>>,
    force_fetch: bool,
    skip_jeep_merge: bool,
    progress: &mut F,
) -> Result<ProjectRouting>
where
    F: FnMut(f64),
{
    let mut all_foot = extra_foot_lines;
    all_foot.extend(custom_hiking_lines(roads));
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
        &all_foot,
        progress,
    )?;
    merge_road_customs_into_routing(&mut osm, roads, opts.road_sample_step_m);
    let trails = bundle.foot_trails;
    let gtlf_inventory = load_gtlf_display_inventory(project_dir, west, south, east, north, roads)
        .unwrap_or_default();
    Ok(ProjectRouting {
        osm,
        trails,
        gtlf_fingerprint: bundle.fingerprint,
        osm_inventory: OsmRoadInventory::default(),
        gtlf_inventory,
        roads_customs: roads.customs.clone(),
        trees: OnceLock::new(),
        draft_trees: OnceLock::new(),
        drive_edge_index: OnceLock::new(),
        trail_edge_index: OnceLock::new(),
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
    access_context_on_osm(
        routing.osm(),
        routing.trails_opt(),
        trees,
        meta,
        max_jeep_m,
        max_slope_deg,
        jeep_budget,
        preference,
        difficulty,
    )
}

fn access_context_on_osm<'a>(
    osm: &'a OsmRouting,
    trails: Option<&'a TrailRouting>,
    trees: &'a RoutingTrees,
    meta: &AccessMeta,
    max_jeep_m: f64,
    max_slope_deg: f64,
    jeep_budget: JeepLengthBudget,
    preference: AccessPreferenceConfig,
    difficulty: PeaksDifficultyConfig,
) -> AccessContext<'a> {
    AccessContext {
        routing: osm,
        trails,
        tree: &trees.unconstrained,
        tree_budgeted: &trees.budgeted,
        max_jeep_m,
        hike_path_max_m: meta.hike_path_max_m,
        profile_sample_m: meta.profile_sample_m,
        max_slope_deg,
        jeep_budget,
        preference,
        difficulty,
        paved_highways: meta.paved_highways.clone(),
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
    let config_path = project_dir.join("config.yaml");
    let roads = ensure_roads_book(&config_path).ok()?;
    let global_fp = roads_global_fingerprint(&roads);
    let local_fp = roads_local_fingerprint(&roads, lat, lon, max_jeep_m);

    let pbf_fp = project_pbf_fingerprint(project_dir);
    let prefs_key = probe_prefs_key(meta, scan, &routing.gtlf_fingerprint, &pbf_fp);
    let cache_path = probe_cache_path(project_dir, &prefs_key, lat, lon);
    if let Some(mut cached) = load_probe_cache(&cache_path) {
        let roads_ok = cached.roads_global_fp.as_deref() == Some(global_fp.as_str())
            && cached.roads_local_fp.as_deref() == Some(local_fp.as_str());
        if roads_ok {
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
    result.roads_global_fp = Some(global_fp);
    result.roads_local_fp = Some(local_fp);
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

/// Attach site access options to a probe and refresh the disk cache when present.
pub fn ensure_site_access_options_on_probe<E: HikeSampleElev>(
    project_dir: &Path,
    elev: &E,
    routing: &ProjectRouting,
    meta: &AccessMeta,
    lat: f64,
    lon: f64,
    probe: &mut AccessProbe,
) -> Result<()> {
    if probe.site_options_rev >= crate::access_probe::SITE_ACCESS_OPTIONS_REV
        && probe
            .site_options
            .as_ref()
            .is_some_and(|options| !options.is_empty())
    {
        return Ok(());
    }
    let preset_path = project_dir.join("config.yaml");
    let preset = load_preset(&preset_path).unwrap_or_default();
    let scan = preset.scan.peaks.clone();
    let max_slope_deg = preset.peak_access_rules().max_slope_deg;
    let jeep_budget = JeepLengthBudget::from_extreme(&scan.difficulty.jeep.extreme);
    let max_jeep_m = display_jeep_max_m();
    let trees = routing.trees(max_jeep_m, &jeep_budget);
    let ctx = access_context(
        routing,
        trees,
        meta,
        &scan,
        max_jeep_m,
        max_slope_deg,
        jeep_budget.clone(),
        scan.preference.clone(),
        scan.difficulty.clone(),
    );
    let mut options = build_site_access_options(elev, lat, lon, &ctx, probe);
    if routing.has_draft_customs() {
        let draft_trees = routing.draft_trees(max_jeep_m, &jeep_budget);
        let draft_ctx = access_context_on_osm(
            routing.osm(),
            routing.trails_opt(),
            draft_trees,
            meta,
            max_jeep_m,
            max_slope_deg,
            jeep_budget,
            scan.preference.clone(),
            scan.difficulty.clone(),
        );
        append_draft_site_access_options(elev, lat, lon, &draft_ctx, probe, &mut options);
    }
    probe.site_options = Some(options);
    probe.site_options_rev = crate::access_probe::SITE_ACCESS_OPTIONS_REV;
    if probe.probe_key.is_some() {
        let pbf_fp = project_pbf_fingerprint(project_dir);
        let prefs_key = probe_prefs_key(meta, &scan, &routing.gtlf_fingerprint, &pbf_fp);
        let cache_path = probe_cache_path(project_dir, &prefs_key, lat, lon);
        if let Err(e) = save_probe_cache(&cache_path, probe) {
            tracing::warn!("probe cache write (site options): {e:#}");
        }
    }
    Ok(())
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
    _opts: &OsmRoutingOpts,
    progress: &mut F,
) -> Result<ProjectRouting>
where
    F: FnMut(OsmParsePhase, f64),
{
    let config_path = project_dir.join("config.yaml");
    let roads = ensure_roads_book(&config_path)?;
    let preset = load_preset(&config_path)?;
    let opts = osm_routing_opts_with_roads(&preset.resolved_access_meta(), &roads);
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
            try_load_merged_routing_cache(&pbf, west, south, east, north, &opts, &gtlf_fp)
        {
            progress(OsmParsePhase::Gtlf, 0.92);
            let inv = try_load_road_inventory_cache(&pbf, cwest, csouth, ceast, cnorth, &opts)
                .unwrap_or_default();
            let extra_foot = hiking_lines_from_inventory(&inv);
            let mut loaded = merge_gtlf(
                project_dir,
                osm,
                west,
                south,
                east,
                north,
                &opts,
                &roads,
                extra_foot,
                false,
                true,
                &mut |_| {},
            )?;
            loaded.osm_inventory = inv;
            progress(OsmParsePhase::Ready, 1.0);
            return Ok(loaded);
        }
    }
    progress(OsmParsePhase::Cache, 0.12);
    let (osm, osm_inventory, osm_hiking) = if !force_osm {
        if let Some(routing) = try_load_osm_routing_cache(&pbf, west, south, east, north, &opts) {
            progress(OsmParsePhase::Cache, 0.48);
            let inv = try_load_road_inventory_cache(&pbf, cwest, csouth, ceast, cnorth, &opts)
                .unwrap_or_default();
            let hiking = hiking_lines_from_inventory(&inv);
            (routing, inv, hiking)
        } else {
            let build = build_osm_routing_with_progress(
                &pbf,
                west,
                south,
                east,
                north,
                &opts,
                &roads,
                progress,
            )?;
            if let Err(e) = save_osm_routing_cache(
                &pbf,
                cwest,
                csouth,
                ceast,
                cnorth,
                &opts,
                &build.routing,
                &build.inventory,
            ) {
                tracing::warn!("osm routing cache write: {e:#}");
            }
            (build.routing, build.inventory, build.hiking_trail_lines)
        }
    } else {
        let build = build_osm_routing_with_progress(
            &pbf,
            west,
            south,
            east,
            north,
            &opts,
            &roads,
            progress,
        )?;
        if let Err(e) = save_osm_routing_cache(
            &pbf,
            cwest,
            csouth,
            ceast,
            cnorth,
            &opts,
            &build.routing,
            &build.inventory,
        ) {
            tracing::warn!("osm routing cache write: {e:#}");
        }
        (build.routing, build.inventory, build.hiking_trail_lines)
    };
    progress(OsmParsePhase::Gtlf, 0.52);
    let mut loaded = merge_gtlf(
        project_dir,
        osm,
        west,
        south,
        east,
        north,
        &opts,
        &roads,
        osm_hiking,
        force_osm,
        false,
        &mut |frac| {
            progress(OsmParsePhase::Gtlf, 0.52 + 0.45 * frac.clamp(0.0, 1.0));
        },
    )?;
    loaded.osm_inventory = osm_inventory;
    if loaded.gtlf_fingerprint != "disabled" {
        if let Err(e) = save_merged_routing_cache(
            &pbf,
            cwest,
            csouth,
            ceast,
            cnorth,
            &opts,
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
