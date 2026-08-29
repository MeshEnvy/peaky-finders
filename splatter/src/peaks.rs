//! Binned local-maxima peak scan inside eligible WGS-84 polygons.

use std::collections::HashMap;
use std::io::{self, Write};
use std::path::Path;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::Instant;

use anyhow::{bail, Context, Result};
use geo::algorithm::bounding_rect::BoundingRect;
use geo::{Contains, Geometry, LineString, MultiPolygon, Point, Polygon};
use geojson::GeoJson;
use rayon::prelude::*;
use rstar::{RTree, RTreeObject, AABB};

use crate::dem::{DemMosaic, DemTile, VOID_SRTM};
use crate::engine::required_tile_names_for_bounds;
use crate::progress_log::{col_log_every, loop_log, phase};

pub const MIN_BIN_M: f64 = 500.0;
/// Minimum drop from a cell to a lower 8-neighbor to count as a peak.
/// Filters flat basins / DEM noise that highest-per-bin used to emit as "peaks".
pub const MIN_PEAK_PROMINENCE_M: f64 = 20.0;
const WEB_MERCATOR_ORIGIN: f64 = 20037508.342_789_244;

/// Goal-direction wedge filter for hop-disc peak scans (matches peaky-serve seek wedge).
#[derive(Clone, Copy, Debug)]
pub struct GoalWedgeFilter {
    pub goal_lat: f64,
    pub goal_lon: f64,
    pub far_angle_scale: f64,
}

/// Annulus sector for on-demand finder scans (ring band + bearing wedge, optional flank-only widen).
#[derive(Clone, Copy, Debug)]
pub struct RingSectorFilter {
    pub goal_lat: f64,
    pub goal_lon: f64,
    pub min_m: f64,
    pub max_m: f64,
    /// Skip cells already scanned (bearing delta <= this from goal bearing).
    pub half_angle_exclude_deg: f64,
    /// Include cells with bearing delta <= this from goal bearing.
    pub half_angle_include_deg: f64,
}

#[derive(Debug, Clone, Copy)]
pub struct Peak {
    pub lon: f64,
    pub lat: f64,
    pub elev_m: f64,
}

/// Called when a DEM tile starts or finishes peak detection (finder watch UI).
pub enum TileScanUpdate {
    Started {
        tile: String,
        started: usize,
        total: usize,
    },
    /// Eligible-land mask burn progress inside a tile (polygon scanline rasterize).
    MaskProgress {
        tile: String,
        row: usize,
        col: usize,
        n: usize,
        eligible_cells: usize,
    },
    /// One downsampled row of the BLM eligibility bitmap (watch UI).
    MaskRow {
        tile: String,
        row: usize,
        n: usize,
        samples: Vec<u8>,
    },
    /// Full downsampled land mask for a tile (cache hit / replay). codes: 1=ineligible 2=eligible.
    MaskTile {
        tile: String,
        w: usize,
        h: usize,
        samples: Vec<u8>,
    },
    Done {
        tile: String,
        peaks: Vec<Peak>,
        done: usize,
        total: usize,
    },
}

pub type TileScanCallback = Arc<dyn Fn(TileScanUpdate) + Send + Sync>;

/// Row-level progress while scanning binned peaks inside a hop disc.
#[derive(Clone, Copy, Debug)]
pub struct HopDiscScanProgress {
    pub rows_done: usize,
    pub rows_total: usize,
    pub tiles: usize,
}

pub fn parse_eligible_geojson(raw: &str) -> Result<MultiPolygon<f64>> {
    let gj: GeoJson = raw.parse().context("parse eligible GeoJSON")?;
    let geom = match gj {
        GeoJson::Geometry(g) => Geometry::<f64>::try_from(&g)
            .map_err(|e| anyhow::anyhow!("eligible GeoJSON geometry: {e}"))?,
        _ => bail!("eligible GeoJSON must be a Geometry object"),
    };
    geometry_to_multipolygon(geom)
}

fn geometry_to_multipolygon(geom: Geometry<f64>) -> Result<MultiPolygon<f64>> {
    match geom {
        Geometry::Polygon(p) => Ok(MultiPolygon(vec![p])),
        Geometry::MultiPolygon(mp) => Ok(mp),
        _ => bail!("eligible geometry must be Polygon or MultiPolygon"),
    }
}

pub fn eligible_bounds(mp: &MultiPolygon<f64>) -> Option<(f64, f64, f64, f64)> {
    let rect = mp.bounding_rect()?;
    Some((rect.min().x, rect.min().y, rect.max().x, rect.max().y))
}

pub fn hop_disc_multipolygon(lat: f64, lon: f64, radius_m: f64) -> MultiPolygon<f64> {
    let n = 64usize;
    let mut coords = Vec::with_capacity(n + 1);
    for i in 0..=n {
        let bearing = 360.0 * i as f64 / n as f64;
        let (dest_lat, dest_lon) = destination_point_wgs84(lat, lon, bearing, radius_m);
        coords.push(geo::Coord {
            x: dest_lon,
            y: dest_lat,
        });
    }
    MultiPolygon(vec![Polygon::new(LineString::from(coords), vec![])])
}

pub fn peak_passes_land_filter(
    peak: Peak,
    land_filter: Option<&MultiPolygon<f64>>,
    viewport_bbox: Option<(f64, f64, f64, f64)>,
    land_index: Option<&LandFilterIndex>,
) -> bool {
    if let Some((west, south, east, north)) = viewport_bbox {
        if peak.lon < west || peak.lon > east || peak.lat < south || peak.lat > north {
            return false;
        }
    }
    if let Some(idx) = land_index {
        return idx.contains(peak.lon, peak.lat);
    }
    if let Some(mp) = land_filter {
        if !point_in_eligible(mp, peak.lon, peak.lat) {
            return false;
        }
    }
    true
}

fn destination_point_wgs84(lat: f64, lon: f64, bearing_deg: f64, distance_m: f64) -> (f64, f64) {
    let r = 6_371_000.0;
    let bearing = bearing_deg.to_radians();
    let lat1 = lat.to_radians();
    let lon1 = lon.to_radians();
    let ang = distance_m / r;
    let lat2 = (lat1.sin() * ang.cos() + lat1.cos() * ang.sin() * bearing.cos()).asin();
    let lon2 = lon1
        + (bearing.sin() * ang.sin() * lat1.cos())
            .atan2(ang.cos() - lat1.sin() * lat2.sin());
    (lat2.to_degrees(), lon2.to_degrees())
}

fn wgs84_to_web_mercator(lon: f64, lat: f64) -> (f64, f64) {
    let x = lon * WEB_MERCATOR_ORIGIN / 180.0;
    let lat_rad = lat.to_radians();
    let y = (std::f64::consts::FRAC_PI_4 + lat_rad / 2.0)
        .tan()
        .ln()
        * WEB_MERCATOR_ORIGIN
        / std::f64::consts::PI;
    (x, y)
}

fn point_in_eligible(mp: &MultiPolygon<f64>, lon: f64, lat: f64) -> bool {
    mp.contains(&Point::new(lon, lat))
}

struct IndexedPoly {
    envelope: AABB<[f64; 2]>,
    index: usize,
}

impl RTreeObject for IndexedPoly {
    type Envelope = AABB<[f64; 2]>;

    fn envelope(&self) -> Self::Envelope {
        self.envelope
    }
}

struct PolyIndex {
    tree: RTree<IndexedPoly>,
    polys: Vec<Polygon<f64>>,
}

impl PolyIndex {
    fn from_multipolygon(mp: &MultiPolygon<f64>) -> Self {
        let mut polys = Vec::with_capacity(mp.0.len());
        let mut indexed = Vec::with_capacity(mp.0.len());
        for poly in &mp.0 {
            let Some(rect) = poly.bounding_rect() else {
                continue;
            };
            let index = polys.len();
            indexed.push(IndexedPoly {
                envelope: AABB::from_corners(
                    [rect.min().x, rect.min().y],
                    [rect.max().x, rect.max().y],
                ),
                index,
            });
            polys.push(poly.clone());
        }
        Self {
            tree: RTree::bulk_load(indexed),
            polys,
        }
    }

    fn contains(&self, lon: f64, lat: f64) -> bool {
        if self.polys.is_empty() {
            return false;
        }
        let pt = Point::new(lon, lat);
        for item in self
            .tree
            .locate_in_envelope_intersecting(&AABB::from_point([lon, lat]))
        {
            if self.polys[item.index].contains(&pt) {
                return true;
            }
        }
        false
    }

    fn intersecting(
        &self,
        min_lon: f64,
        min_lat: f64,
        max_lon: f64,
        max_lat: f64,
    ) -> Vec<&Polygon<f64>> {
        if self.polys.is_empty() {
            return Vec::new();
        }
        let env = AABB::from_corners([min_lon, min_lat], [max_lon, max_lat]);
        self.tree
            .locate_in_envelope_intersecting(&env)
            .map(|item| &self.polys[item.index])
            .collect()
    }
}

/// R-tree over include parcels, minus exclude parcels (no dissolve required).
pub struct LandFilterIndex {
    include: PolyIndex,
    exclude: PolyIndex,
}

impl LandFilterIndex {
    pub fn from_multipolygon(mp: &MultiPolygon<f64>) -> Self {
        Self::from_include_exclude(mp, &MultiPolygon(vec![]))
    }

    pub fn from_include_exclude(include: &MultiPolygon<f64>, exclude: &MultiPolygon<f64>) -> Self {
        Self {
            include: PolyIndex::from_multipolygon(include),
            exclude: PolyIndex::from_multipolygon(exclude),
        }
    }

    pub fn polygon_count(&self) -> usize {
        self.include.polys.len()
    }

    pub fn contains(&self, lon: f64, lat: f64) -> bool {
        self.include.contains(lon, lat) && !self.exclude.contains(lon, lat)
    }

    /// Include polygons whose bbox intersects `[min_lon,min_lat]–[max_lon,max_lat]`.
    pub fn intersecting_polys(
        &self,
        min_lon: f64,
        min_lat: f64,
        max_lon: f64,
        max_lat: f64,
    ) -> Vec<&Polygon<f64>> {
        self.include.intersecting(min_lon, min_lat, max_lon, max_lat)
    }

    /// Exclude polygons whose bbox intersects the tile (punched after include burn).
    pub fn intersecting_exclude_polys(
        &self,
        min_lon: f64,
        min_lat: f64,
        max_lon: f64,
        max_lat: f64,
    ) -> Vec<&Polygon<f64>> {
        self.exclude.intersecting(min_lon, min_lat, max_lon, max_lat)
    }
}

/// Index-space radius for hop-disc scans (avoids haversine on every DEM cell).
fn hop_disc_index_radius_cells(tile: &DemTile, center_lat: f64, radius_m: f64) -> i32 {
    let spacing_m = tile.spacing_deg() * 111_000.0 * center_lat.to_radians().cos().max(0.01);
    ((radius_m / spacing_m).ceil() as i32).saturating_add(2)
}

fn cell_center_wgs84(tile: &DemTile, iy: usize, ix: usize) -> (f64, f64) {
    let spacing = tile.spacing_deg();
    let north = tile.sw_lat + 1.0;
    let lat = north - (iy as f64 + 0.5) * spacing;
    let lon = tile.sw_lon + (ix as f64 + 0.5) * spacing;
    (lat, lon)
}

struct HopDiscTileCtx<'a> {
    tile: &'a DemTile,
    center_lat: f64,
    center_lon: f64,
    hop_radius_m: f64,
    center_iy: i32,
    center_ix: i32,
    iy0: usize,
    iy1: usize,
    ix0: usize,
    ix1: usize,
    spacing_m_lat: f64,
    spacing_m_lon: f64,
    radius_sq_m: f64,
    scan_bbox: Option<(f64, f64, f64, f64)>,
    wedge: Option<GoalWedgeFilter>,
    ring_sector: Option<RingSectorFilter>,
    /// Per-cell eligible land (`.elmk`); skips ineligible cells during local-max scan.
    usable: Option<Arc<Vec<Vec<bool>>>>,
}

fn eligible_usable_grid_for_tile(
    tile: &DemTile,
    tile_name: &str,
    land: &LandFilterIndex,
    mask_cache_dir: &Path,
) -> Result<Option<Arc<Vec<Vec<bool>>>>> {
    let stem = tile_name
        .trim_end_matches(".gz")
        .trim_end_matches(".hgt");
    let (mask, built_usable) = crate::eligible_mask::load_or_build_tile_mask(
        mask_cache_dir,
        stem,
        tile,
        land,
        false,
        None,
        None,
    )?;
    if !mask.has_eligible_cells() {
        return Ok(None);
    }
    let grid = if let Some(grid) = built_usable {
        grid
    } else {
        crate::eligible_mask::usable_grid_from_mask(tile, &mask, stem, false)
    };
    Ok(Some(Arc::new(grid)))
}

pub fn cell_in_ring_sector(
    from_lat: f64,
    from_lon: f64,
    goal_lat: f64,
    goal_lon: f64,
    peak_lat: f64,
    peak_lon: f64,
    min_m: f64,
    max_m: f64,
    half_angle_exclude_deg: f64,
    half_angle_include_deg: f64,
) -> bool {
    let hop_m = crate::propagate::haversine_m(from_lat, from_lon, peak_lat, peak_lon);
    if hop_m <= min_m || hop_m > max_m {
        return false;
    }
    let goal_bearing = bearing_deg(from_lat, from_lon, goal_lat, goal_lon);
    let peak_bearing = bearing_deg(from_lat, from_lon, peak_lat, peak_lon);
    let delta = angle_diff_deg(peak_bearing, goal_bearing);
    if delta <= half_angle_exclude_deg {
        return false;
    }
    delta <= half_angle_include_deg
}

/// Bounding box for a ring-sector peak scan (tight enough to skip most of the hop disc).
pub fn ring_sector_scan_bbox(
    center_lat: f64,
    center_lon: f64,
    goal_lat: f64,
    goal_lon: f64,
    min_m: f64,
    max_m: f64,
    half_include_deg: f64,
) -> (f64, f64, f64, f64) {
    let goal_bearing = bearing_deg(center_lat, center_lon, goal_lat, goal_lon);
    let start_b = goal_bearing - half_include_deg;
    let end_b = goal_bearing + half_include_deg;
    let steps = 24usize;
    let mut lngs = Vec::new();
    let mut lats = Vec::new();
    for i in 0..=steps {
        let t = i as f64 / steps as f64;
        let b = start_b + t * (end_b - start_b);
        for &r in &[min_m.max(1.0), max_m] {
            let (la, lo) = destination_point_wgs84(center_lat, center_lon, b, r);
            lngs.push(lo);
            lats.push(la);
        }
    }
    let (la, lo) = destination_point_wgs84(center_lat, center_lon, 0.0, 0.0);
    lngs.push(lo);
    lats.push(la);
    (
        lngs.iter().copied().fold(f64::INFINITY, f64::min),
        lats.iter().copied().fold(f64::INFINITY, f64::min),
        lngs.iter().copied().fold(f64::NEG_INFINITY, f64::max),
        lats.iter().copied().fold(f64::NEG_INFINITY, f64::max),
    )
}

fn bearing_deg(lat1: f64, lon1: f64, lat2: f64, lon2: f64) -> f64 {
    let phi1 = lat1.to_radians();
    let phi2 = lat2.to_radians();
    let dlambda = (lon2 - lon1).to_radians();
    let y = dlambda.sin() * phi2.cos();
    let x = phi1.cos() * phi2.sin() - phi1.sin() * phi2.cos() * dlambda.cos();
    (y.atan2(x).to_degrees() + 360.0) % 360.0
}

fn angle_diff_deg(a: f64, b: f64) -> f64 {
    let mut d = (a - b).abs() % 360.0;
    if d > 180.0 {
        d = 360.0 - d;
    }
    d
}

fn seek_wedge_half_angle_deg(hop_m: f64, hop_radius_m: f64) -> f64 {
    const NEAR_DEG: f64 = 10.0;
    const FAR_DEG: f64 = 50.0;
    if hop_radius_m <= 0.0 {
        return FAR_DEG;
    }
    let t = (hop_m / hop_radius_m).clamp(0.0, 1.0);
    NEAR_DEG + t * (FAR_DEG - NEAR_DEG)
}

pub fn cell_in_goal_wedge(
    from_lat: f64,
    from_lon: f64,
    goal_lat: f64,
    goal_lon: f64,
    peak_lat: f64,
    peak_lon: f64,
    hop_radius_m: f64,
    far_angle_scale: f64,
) -> bool {
    let hop_m = crate::propagate::haversine_m(from_lat, from_lon, peak_lat, peak_lon);
    if hop_m <= 1.0 {
        return false;
    }
    let goal_bearing = bearing_deg(from_lat, from_lon, goal_lat, goal_lon);
    let peak_bearing = bearing_deg(from_lat, from_lon, peak_lat, peak_lon);
    let delta = angle_diff_deg(peak_bearing, goal_bearing);
    if delta >= 90.0 {
        return false;
    }
    let mut half = seek_wedge_half_angle_deg(hop_m, hop_radius_m);
    if far_angle_scale > 1.0 {
        let near = seek_wedge_half_angle_deg(0.0, hop_radius_m);
        half = near + (half - near) * far_angle_scale;
        half = half.min(89.0);
    }
    delta <= half
}

fn hop_disc_tile_ctx<'a>(
    tile: &'a DemTile,
    center_lat: f64,
    center_lon: f64,
    radius_m: f64,
    scan_bbox: Option<(f64, f64, f64, f64)>,
    wedge: Option<GoalWedgeFilter>,
    ring_sector: Option<RingSectorFilter>,
    usable: Option<Arc<Vec<Vec<bool>>>>,
) -> Option<HopDiscTileCtx<'a>> {
    let n = tile.n;
    if n < 3 {
        return None;
    }
    let spacing = tile.spacing_deg();
    let north = tile.sw_lat + 1.0;
    let center_iy = ((north - center_lat) / spacing - 0.5).round() as i32;
    let center_ix = ((center_lon - tile.sw_lon) / spacing - 0.5).round() as i32;
    let effective_radius = ring_sector.map(|r| r.max_m).unwrap_or(radius_m);
    let r = hop_disc_index_radius_cells(tile, center_lat, effective_radius);
    let iy0 = (center_iy - r).max(1) as usize;
    let iy1 = (center_iy + r).min(n as i32 - 2) as usize;
    let ix0 = (center_ix - r).max(1) as usize;
    let ix1 = (center_ix + r).min(n as i32 - 2) as usize;
    if iy0 > iy1 || ix0 > ix1 {
        return None;
    }
    let cos_lat = center_lat.to_radians().cos().max(0.01);
    Some(HopDiscTileCtx {
        tile,
        center_lat,
        center_lon,
        hop_radius_m: radius_m,
        center_iy,
        center_ix,
        iy0,
        iy1,
        ix0,
        ix1,
        spacing_m_lat: spacing * 111_000.0,
        spacing_m_lon: spacing * 111_000.0 * cos_lat,
        radius_sq_m: effective_radius * effective_radius,
        scan_bbox,
        wedge,
        ring_sector,
        usable,
    })
}

fn cell_in_scan_bbox(lat: f64, lon: f64, scan_bbox: Option<(f64, f64, f64, f64)>) -> bool {
    let Some((west, south, east, north)) = scan_bbox else {
        return true;
    };
    lon >= west && lon <= east && lat >= south && lat <= north
}

fn cell_in_hop_disc(ctx: &HopDiscTileCtx, iy: usize, ix: usize) -> bool {
    let n = ctx.tile.n;
    if iy >= n || ix >= n {
        return false;
    }
    let elev = ctx.tile.elevations[iy * n + ix];
    if elev == VOID_SRTM || elev < -12000 {
        return false;
    }
    if let Some(usable) = &ctx.usable {
        if iy >= usable.len() || ix >= usable[iy].len() || !usable[iy][ix] {
            return false;
        }
    }
    let (lat, lon) = cell_center_wgs84(ctx.tile, iy, ix);
    if !cell_in_scan_bbox(lat, lon, ctx.scan_bbox) {
        return false;
    }
    if let Some(rs) = ctx.ring_sector {
        return cell_in_ring_sector(
            ctx.center_lat,
            ctx.center_lon,
            rs.goal_lat,
            rs.goal_lon,
            lat,
            lon,
            rs.min_m,
            rs.max_m,
            rs.half_angle_exclude_deg,
            rs.half_angle_include_deg,
        );
    }
    if let Some(wedge) = ctx.wedge {
        if !cell_in_goal_wedge(
            ctx.center_lat,
            ctx.center_lon,
            wedge.goal_lat,
            wedge.goal_lon,
            lat,
            lon,
            ctx.hop_radius_m,
            wedge.far_angle_scale,
        ) {
            return false;
        }
    }
    let dy = iy as i32 - ctx.center_iy;
    let dx = ix as i32 - ctx.center_ix;
    let dist_sq_m =
        (dy as f64 * ctx.spacing_m_lat).powi(2) + (dx as f64 * ctx.spacing_m_lon).powi(2);
    dist_sq_m <= ctx.radius_sq_m
}

/// 8-neighbor local maxima with min prominence (ties allowed; flats rejected).
fn scan_hop_disc_row(ctx: &HopDiscTileCtx, iy: usize) -> Vec<Peak> {
    let n = ctx.tile.n;
    let mut row_peaks = Vec::new();
    for ix in ctx.ix0..=ctx.ix1 {
        if !cell_in_hop_disc(ctx, iy, ix) {
            continue;
        }
        let z = ctx.tile.elevations[iy * n + ix];
        let mut is_peak = true;
        let mut max_drop = 0.0_f64;
        for dy in -1i32..=1 {
            for dx in -1i32..=1 {
                if dy == 0 && dx == 0 {
                    continue;
                }
                let ny = iy as i32 + dy;
                let nx = ix as i32 + dx;
                if ny < 0 || nx < 0 {
                    continue;
                }
                let ny = ny as usize;
                let nx = nx as usize;
                if ny >= n || nx >= n {
                    continue;
                }
                if !cell_in_hop_disc(ctx, ny, nx) {
                    continue;
                }
                let nz = ctx.tile.elevations[ny * n + nx];
                if nz == VOID_SRTM || nz < -12000 {
                    continue;
                }
                // Strictly higher neighbor → not a local max. Equals OK (ridge plateaus).
                if nz > z {
                    is_peak = false;
                    break;
                }
                max_drop = max_drop.max(f64::from(z - nz));
            }
            if !is_peak {
                break;
            }
        }
        if !is_peak || max_drop < MIN_PEAK_PROMINENCE_M {
            continue;
        }
        let (lat, lon) = cell_center_wgs84(ctx.tile, iy, ix);
        row_peaks.push(Peak {
            lon,
            lat,
            elev_m: z as f64,
        });
    }
    row_peaks
}

/// Accumulate the highest DEM cell per Web Mercator bin (captures ridge crests, not just 3×3 maxima).
fn upsert_peak_bin(
    bins: &mut HashMap<(i64, i64), Peak>,
    lon: f64,
    lat: f64,
    elev_m: f64,
    bin_size_m: f64,
) {
    let bin_m = bin_size_m.max(MIN_BIN_M);
    let (x, y) = wgs84_to_web_mercator(lon, lat);
    let bx = (x / bin_m).floor() as i64;
    let by = (y / bin_m).floor() as i64;
    bins.entry((bx, by))
        .and_modify(|best| {
            if elev_m > best.elev_m {
                *best = Peak { lon, lat, elev_m };
            }
        })
        .or_insert(Peak { lon, lat, elev_m });
}

fn merge_peak_bin_maps(
    mut acc: HashMap<(i64, i64), Peak>,
    row: HashMap<(i64, i64), Peak>,
) -> HashMap<(i64, i64), Peak> {
    for (key, peak) in row {
        acc.entry(key)
            .and_modify(|best| {
                if peak.elev_m > best.elev_m {
                    *best = peak;
                }
            })
            .or_insert(peak);
    }
    acc
}

fn peaks_from_bin_map(bins: HashMap<(i64, i64), Peak>) -> Vec<Peak> {
    let mut out: Vec<Peak> = bins.into_values().collect();
    out.sort_by(|a, b| {
        b.elev_m
            .partial_cmp(&a.elev_m)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| a.lon.partial_cmp(&b.lon).unwrap_or(std::cmp::Ordering::Equal))
            .then_with(|| a.lat.partial_cmp(&b.lat).unwrap_or(std::cmp::Ordering::Equal))
    });
    out
}

/// Full-resolution 8-neighbor local maxima inside a hop disc (Skadi cell grid).
fn tile_local_maxima_in_disc(
    tile: &DemTile,
    center_lat: f64,
    center_lon: f64,
    radius_m: f64,
) -> Vec<Peak> {
    let Some(ctx) = hop_disc_tile_ctx(tile, center_lat, center_lon, radius_m, None, None, None, None) else {
        return Vec::new();
    };
    (ctx.iy0..=ctx.iy1)
        .flat_map(|iy| scan_hop_disc_row(&ctx, iy))
        .collect()
}

pub fn binned_peaks_in_hop_disc(
    dem: &DemMosaic,
    center_lat: f64,
    center_lon: f64,
    radius_m: f64,
    bin_size_m: f64,
    scan_bbox: Option<(f64, f64, f64, f64)>,
    wedge: Option<GoalWedgeFilter>,
    ring_sector: Option<RingSectorFilter>,
    land_filter: Option<&MultiPolygon<f64>>,
    land_index: Option<&LandFilterIndex>,
    mask_cache_dir: Option<&Path>,
    on_progress: Option<&(dyn Fn(HopDiscScanProgress) + Send + Sync)>,
) -> Result<Vec<Peak>> {
    let hop = hop_disc_multipolygon(center_lat, center_lon, radius_m);
    let Some((minx, miny, maxx, maxy)) = eligible_bounds(&hop) else {
        return Ok(Vec::new());
    };
    let tile_names = required_tile_names_for_bounds(minx, miny, maxx, maxy);
    let tile_ctxs: Vec<HopDiscTileCtx> = tile_names
        .iter()
        .filter_map(|name| {
            let stem = name.trim_end_matches(".gz").trim_end_matches(".hgt");
            let coord = tile_stem_to_coord(stem).ok()?;
            dem.get_tile(coord).and_then(|tile| {
                let usable = match (land_index, mask_cache_dir) {
                    (Some(land), Some(dir)) => {
                        eligible_usable_grid_for_tile(tile, name, land, dir).ok().flatten()
                    }
                    _ => None,
                };
                hop_disc_tile_ctx(
                    tile,
                    center_lat,
                    center_lon,
                    radius_m,
                    scan_bbox,
                    wedge,
                    ring_sector,
                    usable,
                )
            })
        })
        .collect();

    if tile_names.len() != tile_ctxs.len() {
        eprintln!(
            "[splatter] peak scan: {} HGT tile(s) in hop disc bbox, {} loaded in mosaic",
            tile_names.len(),
            tile_ctxs.len()
        );
    }

    let row_jobs: Vec<(usize, usize)> = (0..tile_ctxs.len())
        .flat_map(|ti| {
            let ctx = &tile_ctxs[ti];
            (ctx.iy0..=ctx.iy1).map(move |iy| (ti, iy))
        })
        .collect();

    let rows_total = row_jobs.len();
    let tiles_n = tile_ctxs.len();
    if let Some(cb) = on_progress {
        cb(HopDiscScanProgress {
            rows_done: 0,
            rows_total,
            tiles: tiles_n,
        });
    }

    if std::env::var("PEAKY_PEAK_SCAN_LOG").ok().as_deref() == Some("1") {
        eprintln!(
            "[splatter] peak scan: {} tile(s), {} row job(s), workers={}",
            tiles_n,
            rows_total,
            rayon::current_num_threads()
        );
    }

    let rows_done = AtomicUsize::new(0);
    let log_every = (rows_total / 40).max(1).min(800);

    // Local maxima with prominence, then densest-high per bin (drops flat-basin noise).
    let bins = row_jobs
        .par_iter()
        .map(|(ti, iy)| {
            let mut bins = HashMap::new();
            for peak in scan_hop_disc_row(&tile_ctxs[*ti], *iy) {
                if !peak_passes_land_filter(peak, land_filter, scan_bbox, land_index) {
                    continue;
                }
                upsert_peak_bin(&mut bins, peak.lon, peak.lat, peak.elev_m, bin_size_m);
            }
            let n = rows_done.fetch_add(1, Ordering::Relaxed) + 1;
            if let Some(cb) = on_progress {
                if n == rows_total || n % log_every == 0 {
                    cb(HopDiscScanProgress {
                        rows_done: n,
                        rows_total,
                        tiles: tiles_n,
                    });
                }
            }
            bins
        })
        .reduce(HashMap::new, merge_peak_bin_maps);
    Ok(peaks_from_bin_map(bins))
}

fn tile_local_maxima_from_usable(
    tile: &DemTile,
    usable: &[Vec<bool>],
    log: bool,
    tile_name: &str,
) -> Vec<Peak> {
    let n = tile.n;
    if n < 3 {
        return Vec::new();
    }
    phase("peak detect start", tile_name, log);

    let spacing = tile.spacing_deg();
    let north = tile.sw_lat + 1.0;
    let mut out = Vec::new();
    let col_every = col_log_every(n);
    for iy in 1..n - 1 {
        loop_log(log, &format!("{tile_name} peak detect row {iy}/{n} begin"));
        for ix in 1..n - 1 {
            if log && ix > 1 && (ix - 1) % col_every == 0 {
                loop_log(
                    log,
                    &format!("{tile_name} peak detect row {iy} col {ix}/{n} peaks={}", out.len()),
                );
            }
            if !usable[iy][ix] {
                continue;
            }
            let z = tile.elevations[iy * n + ix];
            let mut is_peak = true;
            for dy in -1i32..=1 {
                for dx in -1i32..=1 {
                    if dy == 0 && dx == 0 {
                        continue;
                    }
                    let ny = (iy as i32 + dy) as usize;
                    let nx = (ix as i32 + dx) as usize;
                    if !usable[ny][nx] {
                        continue;
                    }
                    if tile.elevations[ny * n + nx] >= z {
                        is_peak = false;
                        break;
                    }
                }
                if !is_peak {
                    break;
                }
            }
            if !is_peak {
                continue;
            }
            let lat = north - (iy as f64 + 0.5) * spacing;
            let lon = tile.sw_lon + (ix as f64 + 0.5) * spacing;
            out.push(Peak {
                lon,
                lat,
                elev_m: z as f64,
            });
        }
        loop_log(
            log,
            &format!("{tile_name} peak detect row {iy}/{n} done peaks={}", out.len()),
        );
    }
    if log {
        crate::progress_log::phase(
            "peak detect done",
            &format!("{tile_name} peaks={}", out.len()),
            true,
        );
    }
    out
}

fn tile_local_maxima(
    tile: &DemTile,
    land: &LandFilterIndex,
    mask_cache_dir: Option<&Path>,
    verbose: bool,
    tile_name: &str,
    on_tile_scan: Option<&TileScanCallback>,
) -> Result<Vec<Peak>> {
    let n = tile.n;
    if n < 3 {
        return Ok(Vec::new());
    }
    let log = crate::progress_log::progress_enabled(verbose);
    let stem = tile_name
        .trim_end_matches(".gz")
        .trim_end_matches(".hgt");
    let usable = if let Some(cache_dir) = mask_cache_dir {
        let tile_for_row = tile_name.to_string();
        let tile_for_prog = tile_name.to_string();
        let row_cb: Option<Box<dyn Fn(usize, usize, &[u8])>> = on_tile_scan.map(|cb| {
            let tile_label = tile_for_row.clone();
            let cb = Arc::clone(cb);
            Box::new(move |row, n, samples: &[u8]| {
                cb(TileScanUpdate::MaskRow {
                    tile: tile_label.clone(),
                    row,
                    n,
                    samples: samples.to_vec(),
                });
            }) as Box<dyn Fn(usize, usize, &[u8])>
        });
        let prog_cb: Option<Box<dyn Fn(usize, usize, usize, usize)>> = on_tile_scan.map(|cb| {
            let tile_label = tile_for_prog.clone();
            let cb = Arc::clone(cb);
            Box::new(move |row, col, n, eligible_cells| {
                cb(TileScanUpdate::MaskProgress {
                    tile: tile_label.clone(),
                    row,
                    col,
                    n,
                    eligible_cells,
                });
            }) as Box<dyn Fn(usize, usize, usize, usize)>
        });
        let (mask, built_usable) = crate::eligible_mask::load_or_build_tile_mask(
            cache_dir,
            stem,
            tile,
            land,
            verbose,
            row_cb.as_deref(),
            prog_cb.as_deref(),
        )?;
        if let Some(cb) = on_tile_scan {
            let w = crate::eligible_mask::MASK_ROW_SAMPLES;
            cb(TileScanUpdate::MaskTile {
                tile: tile_name.to_string(),
                w,
                h: w,
                samples: mask.viz_samples(),
            });
        }
        if !mask.has_eligible_cells() {
            if log {
                phase(
                    "peak detect skip",
                    &format!("{tile_name} no eligible cells"),
                    true,
                );
            }
            return Ok(Vec::new());
        }
        if let Some(grid) = built_usable {
            grid
        } else {
            crate::eligible_mask::usable_grid_from_mask(tile, &mask, stem, log)
        }
    } else {
        let (_, grid) = crate::eligible_mask::build_usable_grid(
            tile,
            land,
            stem,
            log,
            None,
            None,
        );
        grid
    };
    Ok(tile_local_maxima_from_usable(tile, &usable, log, tile_name))
}

pub fn bin_peaks(raw: Vec<Peak>, bin_size_m: f64) -> Vec<Peak> {
    let bin_m = bin_size_m.max(MIN_BIN_M);
    let mut bins: HashMap<(i64, i64), Peak> = HashMap::new();
    for p in raw {
        let (x, y) = wgs84_to_web_mercator(p.lon, p.lat);
        let bx = (x / bin_m).floor() as i64;
        let by = (y / bin_m).floor() as i64;
        bins.entry((bx, by))
            .and_modify(|best| {
                if p.elev_m > best.elev_m {
                    *best = p;
                }
            })
            .or_insert(p);
    }
    let mut out: Vec<Peak> = bins.into_values().collect();
    out.sort_by(|a, b| {
        b.elev_m
            .partial_cmp(&a.elev_m)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| a.lon.partial_cmp(&b.lon).unwrap_or(std::cmp::Ordering::Equal))
            .then_with(|| a.lat.partial_cmp(&b.lat).unwrap_or(std::cmp::Ordering::Equal))
    });
    out
}

fn binned_peaks_in_tile_names(
    dem: &DemMosaic,
    tile_names: &[String],
    land: Arc<LandFilterIndex>,
    mask_cache_dir: Option<&Path>,
    bin_size_m: f64,
    verbose: bool,
    log_label: &str,
    on_tile_scan: Option<TileScanCallback>,
) -> Result<Vec<Peak>> {
    let total = tile_names.len();
    let log_enabled = verbose || std::env::var("PEAKY_PEAK_SCAN_LOG").ok().as_deref() == Some("1");
    if log_enabled {
        eprintln!(
            "[splatter] [phase] {log_label} start tiles={total} bin={bin_size_m}m workers={}",
            rayon::current_num_threads()
        );
        if let Some(dir) = mask_cache_dir {
            eprintln!("[splatter] [progress] {log_label} dem_masks={}", dir.display());
        }
        let _ = io::stderr().flush();
    }
    let started = AtomicUsize::new(0);
    let finished = AtomicUsize::new(0);
    let raw_peak_count = AtomicUsize::new(0);
    let scan_t0 = Instant::now();
    let raw: Result<Vec<Vec<Peak>>> = tile_names
        .par_iter()
        .map(|name| {
            let s = started.fetch_add(1, Ordering::Relaxed) + 1;
            if let Some(ref cb) = on_tile_scan {
                cb(TileScanUpdate::Started {
                    tile: name.clone(),
                    started: s,
                    total,
                });
            }
            if log_enabled {
                let _ = writeln!(
                    io::stderr(),
                    "[splatter] [progress] {log_label} tile start [{s}/{total}] {name}"
                );
                let _ = io::stderr().flush();
            }
            let tile_t0 = Instant::now();
            let stem = name.trim_end_matches(".gz").trim_end_matches(".hgt");
            let coord = tile_stem_to_coord(stem)?;
            let tile = dem
                .get_tile(coord)
                .with_context(|| format!("missing DEM tile {name}"))?;
            let peaks = tile_local_maxima(
                tile,
                &land,
                mask_cache_dir,
                verbose,
                name,
                on_tile_scan.as_ref(),
            )?;
            let peak_n = peaks.len();
            raw_peak_count.fetch_add(peak_n, Ordering::Relaxed);
            let f = finished.fetch_add(1, Ordering::Relaxed) + 1;
            if let Some(ref cb) = on_tile_scan {
                cb(TileScanUpdate::Done {
                    tile: name.clone(),
                    peaks: peaks.clone(),
                    done: f,
                    total,
                });
            }
            if log_enabled {
                let in_flight = started.load(Ordering::Relaxed).saturating_sub(f);
                let _ = writeln!(
                    io::stderr(),
                    "[splatter] [progress] {log_label} tile done [{f}/{total}] {name} peaks={peak_n} raw_total={} in_flight={in_flight} elapsed={:.1}s scan={:.0}s",
                    raw_peak_count.load(Ordering::Relaxed),
                    tile_t0.elapsed().as_secs_f64(),
                    scan_t0.elapsed().as_secs_f64()
                );
                let _ = io::stderr().flush();
            }
            Ok(peaks)
        })
        .collect();
    let raw: Vec<Peak> = raw?.into_iter().flatten().collect();
    if log_enabled {
        eprintln!(
            "[splatter] [phase] {log_label} raw peaks={} binning…",
            raw.len()
        );
        let _ = io::stderr().flush();
    }
    let binned = bin_peaks(raw, bin_size_m);
    if log_enabled {
        eprintln!(
            "[splatter] [phase] {log_label} done peaks={}",
            binned.len()
        );
        let _ = io::stderr().flush();
    }
    Ok(binned)
}

pub fn binned_peaks_in_polygon(
    dem: &DemMosaic,
    eligible: &MultiPolygon<f64>,
    bin_size_m: f64,
    verbose: bool,
) -> Result<Vec<Peak>> {
    let Some((minx, miny, maxx, maxy)) = eligible_bounds(eligible) else {
        return Ok(Vec::new());
    };
    let tile_names = required_tile_names_for_bounds(minx, miny, maxx, maxy);
    let land = Arc::new(LandFilterIndex::from_multipolygon(eligible));
    binned_peaks_in_tile_names(
        dem,
        &tile_names,
        land,
        None,
        bin_size_m,
        verbose,
        "corridor peak scan",
        None,
    )
}

/// Scan peaks in an explicit WGS-84 bbox; keep only cells touching eligible land.
pub fn binned_peaks_in_bounds(
    dem: &DemMosaic,
    west: f64,
    south: f64,
    east: f64,
    north: f64,
    land: Arc<LandFilterIndex>,
    mask_cache_dir: Option<&Path>,
    bin_size_m: f64,
    verbose: bool,
    on_tile_scan: Option<TileScanCallback>,
) -> Result<Vec<Peak>> {
    let tile_names = required_tile_names_for_bounds(west, south, east, north);
    binned_peaks_in_tile_names(
        dem,
        &tile_names,
        land,
        mask_cache_dir,
        bin_size_m,
        verbose,
        "corridor peak scan",
        on_tile_scan,
    )
}

pub fn tile_stem_to_coord(stem: &str) -> Result<(i32, i32)> {
    if stem.len() < 7 {
        bail!("bad tile stem {stem:?}");
    }
    let ns = stem.chars().next().context("ns")?;
    let ew = stem.chars().nth(3).context("ew")?;
    let lat_d: i32 = stem[1..3].parse().context("lat digits")?;
    let lon_d: i32 = stem[4..7].parse().context("lon digits")?;
    let sw_lat = if ns == 'N' || ns == 'n' {
        lat_d
    } else {
        -lat_d
    };
    let sw_lon = if ew == 'E' || ew == 'e' {
        lon_d
    } else {
        -lon_d
    };
    Ok((sw_lat, sw_lon))
}

#[cfg(test)]
mod tests {
    use super::*;
    use geo::{coord, Polygon};

    #[test]
    fn bin_peeps_keeps_highest_per_cell() {
        let raw = vec![
            Peak {
                lon: -119.0,
                lat: 39.0,
                elev_m: 2100.0,
            },
            Peak {
                lon: -119.0001,
                lat: 39.0001,
                elev_m: 2200.0,
            },
        ];
        let binned = bin_peaks(raw, 1500.0);
        assert_eq!(binned.len(), 1);
        assert!((binned[0].elev_m - 2200.0).abs() < f64::EPSILON);
    }

    #[test]
    fn parse_polygon_geojson() {
        let gj = r#"{"type":"Polygon","coordinates":[[[-120,39],[-119,39],[-119,40],[-120,40],[-120,39]]]}"#;
        let mp = parse_eligible_geojson(gj).unwrap();
        assert_eq!(mp.0.len(), 1);
        assert!(point_in_eligible(&mp, -119.5, 39.5));
    }

    #[test]
    fn hop_disc_binned_finds_ridge_crest() {
        let n = 31usize;
        let mut elevations = vec![2000i16; n * n];
        for ix in 5..25 {
            elevations[15 * n + ix] = 2400;
        }
        let tile = DemTile {
            sw_lat: 39.0,
            sw_lon: -120.0,
            n,
            elevations,
        };
        let local = tile_local_maxima_in_disc(&tile, 39.5, -119.5, 30_000.0);
        assert!(
            local.iter().any(|p| p.elev_m >= 2399.0),
            "prominence local-max should catch ridge crest cells, got {local:?}"
        );
        let dem = DemMosaic::with_single_tile((39, -120), tile);
        let peaks =
            binned_peaks_in_hop_disc(
                &dem,
                39.5,
                -119.5,
                30_000.0,
                1500.0,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
            )
                .expect("binned");
        assert!(
            peaks.iter().any(|p| p.elev_m >= 2399.0),
            "ridge crest should appear as binned peak, got {peaks:?}"
        );
    }

    #[test]
    fn hop_disc_local_max_finds_spike() {
        let n = 31usize;
        let mut elevations = vec![2000i16; n * n];
        let summit = 15usize;
        elevations[summit * n + summit] = 2500;
        let tile = DemTile {
            sw_lat: 39.0,
            sw_lon: -120.0,
            n,
            elevations,
        };
        let peaks = tile_local_maxima_in_disc(&tile, 39.5, -119.5, 30_000.0);
        assert!(peaks.iter().any(|p| (p.elev_m - 2500.0).abs() < f64::EPSILON));
    }

    #[test]
    fn hop_disc_rejects_flat_basin() {
        let n = 31usize;
        let elevations = vec![1500i16; n * n];
        let tile = DemTile {
            sw_lat: 39.0,
            sw_lon: -120.0,
            n,
            elevations,
        };
        let dem = DemMosaic::with_single_tile((39, -120), tile);
        let peaks =
            binned_peaks_in_hop_disc(
                &dem,
                39.5,
                -119.5,
                30_000.0,
                1500.0,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
            )
                .expect("binned");
        assert!(
            peaks.is_empty(),
            "flat DEM must not emit peaks, got {} (e.g. {:?})",
            peaks.len(),
            peaks.first()
        );
    }

    #[test]
    fn local_max_on_spike() {
        let n = 5usize;
        let mut elevations = vec![VOID_SRTM; n * n];
        let sw_lat = 39.0;
        let sw_lon = -120.0;
        for iy in 0..n {
            for ix in 0..n {
                elevations[iy * n + ix] = 1000;
            }
        }
        elevations[2 * n + 2] = 2000;
        let tile = DemTile {
            sw_lat,
            sw_lon,
            n,
            elevations,
        };
        let poly: MultiPolygon = MultiPolygon(vec![Polygon::new(
            geo::LineString(vec![
                coord! { x: -121.0, y: 38.0 },
                coord! { x: -118.0, y: 38.0 },
                coord! { x: -118.0, y: 41.0 },
                coord! { x: -121.0, y: 41.0 },
                coord! { x: -121.0, y: 38.0 },
            ]),
            vec![],
        )]);
        let land = LandFilterIndex::from_multipolygon(&poly);
        let peaks = tile_local_maxima(&tile, &land, None, false, "test-tile", None).expect("peaks");
        assert!(!peaks.is_empty());
        assert!(peaks.iter().any(|p| (p.elev_m - 2000.0).abs() < f64::EPSILON));
    }

    #[test]
    fn eligible_peak_kept_when_ineligible_neighbor_is_higher_in_same_bin() {
        let n = 31usize;
        let mut elevations = vec![2000i16; n * n];
        let eligible_ix = 10usize;
        let ineligible_ix = 20usize;
        let row = 15usize;
        elevations[row * n + eligible_ix] = 2200;
        elevations[row * n + ineligible_ix] = 3200;
        let tile = DemTile {
            sw_lat: 39.0,
            sw_lon: -120.0,
            n,
            elevations,
        };
        let spacing = tile.spacing_deg();
        let north = tile.sw_lat + 1.0;
        let center_lat = north - (row as f64 + 0.5) * spacing;
        let center_lon = tile.sw_lon + ((eligible_ix + ineligible_ix) as f64 / 2.0 + 0.5) * spacing;
        let dem = DemMosaic::with_single_tile((39, -120), tile);
        // Western half of tile is eligible; taller eastern summit must not steal the bin.
        let poly: MultiPolygon = MultiPolygon(vec![Polygon::new(
            geo::LineString(vec![
                coord! { x: -120.0, y: 39.0 },
                coord! { x: -119.5, y: 39.0 },
                coord! { x: -119.5, y: 40.0 },
                coord! { x: -120.0, y: 40.0 },
                coord! { x: -120.0, y: 39.0 },
            ]),
            vec![],
        )]);
        let peaks = binned_peaks_in_hop_disc(
            &dem,
            center_lat,
            center_lon,
            30_000.0,
            1500.0,
            None,
            None,
            None,
            Some(&poly),
            None,
            None,
            None,
        )
        .expect("binned");
        assert!(
            peaks.iter().any(|p| (p.elev_m - 2200.0).abs() < f64::EPSILON),
            "expected eligible 2200m summit, got {:?}",
            peaks
        );
    }
}
