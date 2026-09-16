//! Long-lived splatter session: shared DEM mosaic across many coverage runs.

use std::path::{Path, PathBuf};
use std::sync::{Arc, RwLock};

use anyhow::{Context, Result};
use geo::MultiPolygon;
use rayon::prelude::*;

use crate::dem::DemMosaic;
use crate::dem_mirror::DemMirror;
use crate::engine::{run_coverage_with_dem, required_tile_names};
use crate::hash::{splat_input_sha256, Request as CovRequest};
use crate::propagate::{
    evaluate_link, evaluate_mutual_link_viable, evaluate_mutual_links_parallel,
    link_context_from_json, required_tile_names_for_points, LinkContext, LinkResult,
};
use crate::peak_links::{linkable_binned_peaks_with_dem, LinkablePeak};

pub struct Session {
    mirror_root: PathBuf,
    dem: RwLock<DemMosaic>,
    dem_mirror: Arc<DemMirror>,
    verbose: bool,
}

impl Session {
    pub fn new(mirror_root: PathBuf, verbose: bool, dem_fetch_workers: usize) -> Self {
        let dem_mirror = Arc::new(DemMirror::start(
            mirror_root.clone(),
            verbose,
            dem_fetch_workers,
        ));
        Self {
            mirror_root,
            dem: RwLock::new(DemMosaic::empty()),
            dem_mirror,
            verbose,
        }
    }

    pub fn dem_mirror(&self) -> &DemMirror {
        &self.dem_mirror
    }

    pub fn mirror_root(&self) -> &Path {
        &self.mirror_root
    }

    pub fn verbose(&self) -> bool {
        self.verbose
    }

    pub fn loaded_tile_count(&self) -> usize {
        self.dem.read().unwrap().tile_count()
    }

    /// Bilinear AMSL meters from the loaded mosaic (0 if the tile is missing or void).
    pub fn sample_elev_m(&self, lat: f64, lon: f64) -> f64 {
        self.dem.read().unwrap().sample_m(lat, lon)
    }

    pub fn preload_tiles(&self, tile_names: &[String]) -> Result<()> {
        self.dem.write().unwrap().ensure_tiles(
            &self.mirror_root,
            tile_names,
            self.verbose,
            Some(&self.dem_mirror),
        )
    }

    pub fn ensure_tiles_for_request(&self, req: &CovRequest) -> Result<()> {
        let tiles = required_tile_names(req.lat, req.lon, req.radius);
        self.preload_tiles(&tiles)
    }

    pub fn ensure_tiles_for_requests(&self, requests: &[CovRequest]) -> Result<()> {
        let mut tile_set: Vec<String> = Vec::new();
        for req in requests {
            tile_set.extend(required_tile_names(req.lat, req.lon, req.radius));
        }
        tile_set.sort();
        tile_set.dedup();
        self.preload_tiles(&tile_set)
    }

    pub fn ensure_tiles_for_points(&self, points: &[(f64, f64)], buffer_m: f64) -> Result<()> {
        let tiles = required_tile_names_for_points(points, buffer_m);
        self.preload_tiles(&tiles)
    }

    pub fn ensure_tiles_for_bounds(
        &self,
        minx: f64,
        miny: f64,
        maxx: f64,
        maxy: f64,
    ) -> Result<()> {
        let tiles = crate::engine::required_tile_names_for_bounds(minx, miny, maxx, maxy);
        self.preload_tiles(&tiles)
    }

    pub fn missing_tiles_for_bounds(
        &self,
        minx: f64,
        miny: f64,
        maxx: f64,
        maxy: f64,
    ) -> Result<Vec<String>> {
        let tiles = crate::engine::required_tile_names_for_bounds(minx, miny, maxx, maxy);
        crate::skadi_fetch::missing_mirror_tiles(&self.mirror_root, &tiles)
    }

    pub fn mirror_tile_gz_bytes(&self, tile_name: &str) -> Result<Vec<u8>> {
        self.dem_mirror
            .ensure(tile_name, crate::dem_mirror::PRIORITY_DEM_BLOCKING)?;
        let path = self.dem_mirror.tile_path(tile_name);
        std::fs::read(&path).with_context(|| format!("read mirror tile {}", path.display()))
    }

    pub fn install_dem_mosaic(&self, dem: DemMosaic) {
        *self.dem.write().unwrap() = dem;
    }

    /// Skadi HGT tile names covering a hop disc around ``(source_lat, source_lon)``.
    pub fn hop_disc_tile_names(source_lat: f64, source_lon: f64, hop_radius_m: f64) -> Vec<String> {
        let hop = crate::peaks::hop_disc_multipolygon(source_lat, source_lon, hop_radius_m);
        let Some((minx, miny, maxx, maxy)) = crate::peaks::eligible_bounds(&hop) else {
            return Vec::new();
        };
        let mut tile_names = crate::engine::required_tile_names_for_bounds(minx, miny, maxx, maxy);
        tile_names.extend(required_tile_names(
            source_lat,
            source_lon,
            hop_radius_m,
        ));
        tile_names.sort();
        tile_names.dedup();
        tile_names
    }

    /// Blocking fetch + in-memory load of hop-disc Skadi tiles.
    pub fn ensure_tiles_for_hop_disc(
        &self,
        source_lat: f64,
        source_lon: f64,
        hop_radius_m: f64,
    ) -> Result<()> {
        let tile_names = Self::hop_disc_tile_names(source_lat, source_lon, hop_radius_m);
        if tile_names.is_empty() {
            return Ok(());
        }
        self.preload_tiles(&tile_names)
    }

    /// Binned ridge peaks inside a search polygon (eligible ∩ hop ∩ viewport, etc.).
    pub fn binned_peaks_in_polygon(
        &self,
        search: &MultiPolygon<f64>,
        bin_size_m: f64,
    ) -> Result<Vec<crate::peaks::Peak>> {
        let dem = self.dem.read().unwrap();
        crate::peaks::binned_peaks_in_polygon(&dem, search, bin_size_m, self.verbose)
    }

    /// Binned peaks inside a corridor bbox, filtered to eligible land cells.
    pub fn binned_peaks_in_bounds(
        &self,
        west: f64,
        south: f64,
        east: f64,
        north: f64,
        land: std::sync::Arc<crate::peaks::LandFilterIndex>,
        mask_cache_dir: Option<&std::path::Path>,
        bin_size_m: f64,
        on_tile_scan: Option<crate::peaks::TileScanCallback>,
    ) -> Result<Vec<crate::peaks::Peak>> {
        let dem = self.dem.read().unwrap();
        crate::peaks::binned_peaks_in_bounds(
            &dem,
            west,
            south,
            east,
            north,
            land,
            mask_cache_dir,
            bin_size_m,
            self.verbose,
            on_tile_scan,
        )
    }

    /// Watch viz grid for one DEM tile: load `.elmk` or build it (peak-cache reconnect path).
    pub fn ensure_tile_mask_viz(
        &self,
        mask_dir: &Path,
        tile_name: &str,
        land: &crate::peaks::LandFilterIndex,
    ) -> Result<Option<Vec<u8>>> {
        let stem = tile_name
            .trim_end_matches(".gz")
            .trim_end_matches(".hgt");
        if let Some(samples) = crate::eligible_mask::load_tile_mask_viz(mask_dir, stem)? {
            return Ok(Some(samples));
        }
        let coord = crate::peaks::tile_stem_to_coord(stem)?;
        let dem = self.dem.read().unwrap();
        let Some(tile) = dem.get_tile(coord) else {
            return Ok(None);
        };
        let (mask, _) = crate::eligible_mask::load_or_build_tile_mask(
            mask_dir,
            stem,
            tile,
            land,
            self.verbose,
            None,
            None,
        )?;
        Ok(Some(mask.viz_samples()))
    }

    /// Highest point per bin inside hop disc (eligible land only). No RF filter.
    pub fn disc_binned_peaks(
        &self,
        source_lat: f64,
        source_lon: f64,
        hop_radius_m: f64,
        land_filter: Option<&MultiPolygon<f64>>,
        scan_bbox: Option<(f64, f64, f64, f64)>,
        progress_lens: Option<crate::peaks::GoalProgressFilter>,
        ring_sector: Option<crate::peaks::RingSectorFilter>,
        bin_size_m: f64,
        land_index: Option<&crate::peaks::LandFilterIndex>,
        mask_cache_dir: Option<&Path>,
        on_progress: Option<&(dyn Fn(crate::peaks::HopDiscScanProgress) + Send + Sync)>,
    ) -> Result<Vec<crate::peaks::Peak>> {
        let dem = self.dem.read().unwrap();
        crate::peak_links::disc_binned_peaks_with_dem(
            &dem,
            source_lat,
            source_lon,
            hop_radius_m,
            land_filter,
            scan_bbox,
            progress_lens,
            ring_sector,
            bin_size_m,
            land_index,
            mask_cache_dir,
            on_progress,
        )
    }

    /// Highest eligible cell per bin (ridges, not only local maxima). No RF filter.
    pub fn disc_binned_high_points(
        &self,
        source_lat: f64,
        source_lon: f64,
        hop_radius_m: f64,
        land_filter: Option<&MultiPolygon<f64>>,
        scan_bbox: Option<(f64, f64, f64, f64)>,
        progress_lens: Option<crate::peaks::GoalProgressFilter>,
        ring_sector: Option<crate::peaks::RingSectorFilter>,
        bin_size_m: f64,
        land_index: Option<&crate::peaks::LandFilterIndex>,
        mask_cache_dir: Option<&Path>,
        on_progress: Option<&(dyn Fn(crate::peaks::HopDiscScanProgress) + Send + Sync)>,
    ) -> Result<Vec<crate::peaks::Peak>> {
        let dem = self.dem.read().unwrap();
        let binned = crate::peaks::binned_high_points_in_hop_disc(
            &dem,
            source_lat,
            source_lon,
            hop_radius_m,
            bin_size_m,
            scan_bbox,
            progress_lens,
            ring_sector,
            land_filter,
            land_index,
            mask_cache_dir,
            on_progress,
        )?;
        Ok(binned
            .into_iter()
            .filter(|peak| {
                crate::peaks::peak_passes_land_filter(
                    *peak,
                    land_filter,
                    scan_bbox,
                    land_index,
                ) && crate::propagate::haversine_m(source_lat, source_lon, peak.lat, peak.lon)
                    <= hop_radius_m
            })
            .collect())
    }

    /// Binned local maxima with mutual RF viability. Call [`Self::ensure_tiles_for_hop_disc`] first.
    /// Pass ``limit = 0`` to return all viable peaks (goal seek sorts toward the target).
    pub fn linkable_binned_peaks(
        &self,
        source_lat: f64,
        source_lon: f64,
        tx_height_agl: f64,
        hop_radius_m: f64,
        land_filter: Option<&MultiPolygon<f64>>,
        viewport_bbox: Option<(f64, f64, f64, f64)>,
        rf_json: &str,
        limit: usize,
        bin_size_m: f64,
        rx_height_agl: Option<f64>,
    ) -> Result<Vec<LinkablePeak>> {
        let dem = self.dem.read().unwrap();
        linkable_binned_peaks_with_dem(
            &dem,
            source_lat,
            source_lon,
            tx_height_agl,
            hop_radius_m,
            land_filter,
            viewport_bbox,
            rf_json,
            limit,
            bin_size_m,
            rx_height_agl,
        )
    }

    pub fn link_context(&self, rf_json: &str) -> Result<LinkContext> {
        link_context_from_json(rf_json)
    }

    pub fn link_eval(
        &self,
        tx_lat: f64,
        tx_lon: f64,
        rx_lat: f64,
        rx_lon: f64,
        rf_json: &str,
    ) -> Result<LinkResult> {
        let ctx = link_context_from_json(rf_json)?;
        let dem = self.dem.read().unwrap();
        Ok(evaluate_link(
            &dem, tx_lat, tx_lon, rx_lat, rx_lon, &ctx,
        ))
    }

    pub fn link_eval_with_heights(
        &self,
        tx_lat: f64,
        tx_lon: f64,
        tx_h: f64,
        rx_lat: f64,
        rx_lon: f64,
        rx_h: f64,
        rf_json: &str,
    ) -> Result<LinkResult> {
        let mut ctx = link_context_from_json(rf_json)?;
        ctx.tx_height = tx_h.max(1.0);
        ctx.rx_height = rx_h.max(1.0);
        let dem = self.dem.read().unwrap();
        Ok(evaluate_link(
            &dem, tx_lat, tx_lon, rx_lat, rx_lon, &ctx,
        ))
    }

    pub fn link_viable(
        &self,
        tx_lat: f64,
        tx_lon: f64,
        rx_lat: f64,
        rx_lon: f64,
        rf_json: &str,
    ) -> Result<bool> {
        Ok(self
            .link_eval(tx_lat, tx_lon, rx_lat, rx_lon, rf_json)?
            .viable)
    }

    pub fn link_mutual_viable(
        &self,
        lat_a: f64,
        lon_a: f64,
        lat_b: f64,
        lon_b: f64,
        rf_json: &str,
    ) -> Result<bool> {
        let ctx = link_context_from_json(rf_json)?;
        let dem = self.dem.read().unwrap();
        Ok(evaluate_mutual_link_viable(
            &dem, lat_a, lon_a, lat_b, lon_b, &ctx,
        ))
    }

    pub fn link_mutual_batch(
        &self,
        pairs: &[(f64, f64, f64, f64)],
        rf_json: &str,
    ) -> Result<Vec<bool>> {
        let ctx = link_context_from_json(rf_json)?;
        let dem = self.dem.read().unwrap();
        Ok(evaluate_mutual_links_parallel(&dem, pairs, &ctx))
    }

    pub fn link_mutual_batch_with_tx_height(
        &self,
        pairs: &[(f64, f64, f64, f64)],
        rf_json: &str,
        tx_height_agl: f64,
    ) -> Result<Vec<bool>> {
        let mut ctx = link_context_from_json(rf_json)?;
        ctx.tx_height = tx_height_agl.max(1.0);
        let dem = self.dem.read().unwrap();
        Ok(evaluate_mutual_links_parallel(&dem, pairs, &ctx))
    }

    /// Goal-seek / peak links: weaker-leg dB over threshold. `None` if neither direction decodes.
    pub fn seek_repeater_link_margins(
        &self,
        from_lat: f64,
        from_lon: f64,
        from_tx_h: f64,
        endpoints: &[(f64, f64, f64)],
        rf_json: &str,
    ) -> Result<Vec<Option<f64>>> {
        let base = link_context_from_json(rf_json)?;
        let from_h = from_tx_h.max(1.0);
        let dem = self.dem.read().unwrap();
        Ok(endpoints
            .par_iter()
            .map(|(lat, lon, tx_h)| {
                crate::propagate::evaluate_mutual_site_link_margin(
                    &dem,
                    from_lat,
                    from_lon,
                    from_h,
                    *lat,
                    *lon,
                    tx_h.max(1.0),
                    &base,
                )
            })
            .collect())
    }

    /// Goal-seek / peak links: same per-endpoint antenna model as site mesh (mutual decode required).
    pub fn seek_repeater_link_batch(
        &self,
        from_lat: f64,
        from_lon: f64,
        from_tx_h: f64,
        endpoints: &[(f64, f64, f64)],
        rf_json: &str,
    ) -> Result<Vec<bool>> {
        Ok(self
            .seek_repeater_link_margins(from_lat, from_lon, from_tx_h, endpoints, rf_json)?
            .into_iter()
            .map(crate::propagate::mutual_site_link_viable)
            .collect())
    }

    pub fn link_strength_batch(
        &self,
        pairs: &[(f64, f64, f64, f64)],
        rf_json: &str,
    ) -> Result<Vec<Option<crate::propagate::LinkStrength>>> {
        let ctx = link_context_from_json(rf_json)?;
        let dem = self.dem.read().unwrap();
        Ok(crate::propagate::evaluate_link_strengths_parallel(
            &dem, pairs, &ctx,
        ))
    }

    pub fn site_mesh_pair_strengths(
        &self,
        pairs: &[(f64, f64, f64, f64, f64, f64)],
        rf_json: &str,
    ) -> Result<Vec<Option<crate::propagate::LinkStrength>>> {
        let ctx = link_context_from_json(rf_json)?;
        let dem = self.dem.read().unwrap();
        Ok(crate::propagate::evaluate_mutual_site_link_strengths_parallel(
            &dem, pairs, &ctx,
        ))
    }

    /// MapLibre Terrarium PNG from the Skadi mirror (same elevations as peak/RF).
    pub fn skadi_terrarium_tile_png(&self, z: u32, x: u32, y: u32) -> Result<Vec<u8>> {
        self.render_map_tile(crate::dem_tile_cache::TileKind::Terrarium, z, x, y, |dem| {
            crate::dem_tiles::render_terrarium_png(dem, z, x, y)
        })
    }

    /// Grayscale hillshade PNG from the Skadi mirror.
    pub fn skadi_hillshade_tile_png(&self, z: u32, x: u32, y: u32) -> Result<Vec<u8>> {
        self.render_map_tile(crate::dem_tile_cache::TileKind::Hillshade, z, x, y, |dem| {
            crate::dem_tiles::render_hillshade_png(dem, z, x, y)
        })
    }

    fn render_map_tile<F>(
        &self,
        kind: crate::dem_tile_cache::TileKind,
        z: u32,
        x: u32,
        y: u32,
        render: F,
    ) -> Result<Vec<u8>>
    where
        F: FnOnce(&DemMosaic) -> Result<Vec<u8>>,
    {
        if let Some(cached) = crate::dem_tile_cache::read(&self.mirror_root, kind, z, x, y)? {
            return Ok(cached);
        }

        let pad = match kind {
            crate::dem_tile_cache::TileKind::Hillshade => crate::dem_tiles::HILLSHADE_HGT_PAD_DEG,
            crate::dem_tile_cache::TileKind::Terrarium => 0.0,
        };
        let hgt_names = crate::dem_tiles::required_hgt_names_for_xyz(z, x, y, pad);
        let missing: Vec<String> = hgt_names
            .iter()
            .filter(|name| !self.dem_mirror.is_on_disk(name))
            .cloned()
            .collect();
        if !missing.is_empty() {
            self.dem_mirror
                .prefetch(&missing, crate::dem_mirror::PRIORITY_DEM_MAP);
            anyhow::bail!(
                "map tile {z}/{x}/{y} waiting on {} HGT file(s)",
                missing.len()
            );
        }

        let dem = DemMosaic::from_mirror_on_disk(&self.mirror_root, &hgt_names)?;
        let png = render(&dem)?;
        crate::dem_tile_cache::write(&self.mirror_root, kind, z, x, y, &png)?;
        Ok(png)
    }

    pub fn input_sha256(&self, req: &CovRequest) -> Result<String> {
        splat_input_sha256(req)
    }

    pub fn run(&self, work_dir: &Path) -> Result<()> {
        let req_path = work_dir.join("request.json");
        let raw = std::fs::read_to_string(&req_path)
            .with_context(|| format!("read {}", req_path.display()))?;
        let parsed: CovRequest =
            serde_json::from_str(&raw).context("parse request.json as SplatCoverageRequest")?;
        self.ensure_tiles_for_request(&parsed)?;
        let dem = self.dem.read().unwrap();
        run_coverage_with_dem(work_dir, &dem, self.verbose)
    }
}
