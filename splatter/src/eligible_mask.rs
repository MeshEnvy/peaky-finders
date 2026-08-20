//! Per-DEM-tile eligible-land bitmasks (polygon burn once, O(1) cell lookup).

use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::time::Instant;

use anyhow::{Context, Result};
use geo::{CoordsIter, Polygon};

use crate::dem::{DemTile, VOID_SRTM};
use crate::peaks::LandFilterIndex;
use crate::progress_log::{loop_log, phase, progress_enabled};

const MAGIC: &[u8; 4] = b"ELMK";
/// v2: polygon scanline burn (not per-cell point-in-polygon).
const VERSION: u8 = 2;
/// Width of downsampled row sent to the finder watch UI (defrag canvas).
pub const MASK_ROW_SAMPLES: usize = 128;
const MASK_ROW_SSE_EVERY: usize = 4;

fn downsample_row(row: &[bool], samples: usize) -> Vec<u8> {
    let n = row.len();
    if n == 0 || samples == 0 {
        return vec![1; samples];
    }
    let mut out = vec![1u8; samples];
    for s in 0..samples {
        let ix0 = s * n / samples;
        let ix1 = ((s + 1) * n / samples).min(n);
        for ix in ix0..ix1 {
            if row[ix] {
                out[s] = 2;
                break;
            }
        }
    }
    out
}

pub struct TileEligibleMask {
    n: u32,
    bits: Vec<u8>,
}

impl TileEligibleMask {
    pub fn get(&self, iy: usize, ix: usize, n: usize) -> bool {
        if iy >= n || ix >= n {
            return false;
        }
        let idx = iy * n + ix;
        (self.bits[idx / 8] >> (idx % 8)) & 1 == 1
    }

    fn from_flat(n: usize, cells: &[bool]) -> Self {
        let cell_count = n * n;
        debug_assert_eq!(cells.len(), cell_count);
        let byte_len = cell_count.div_ceil(8);
        let mut bits = vec![0u8; byte_len];
        for (idx, &on) in cells.iter().enumerate() {
            if on {
                bits[idx / 8] |= 1 << (idx % 8);
            }
        }
        Self {
            n: n as u32,
            bits,
        }
    }

    fn write_file(&self, path: &Path) -> Result<()> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let mut out = Vec::with_capacity(9 + self.bits.len());
        out.extend_from_slice(MAGIC);
        out.push(VERSION);
        out.extend_from_slice(&self.n.to_le_bytes());
        out.extend_from_slice(&self.bits);
        let tmp = path.with_extension("elmk.tmp");
        std::fs::write(&tmp, &out).with_context(|| format!("write {}", tmp.display()))?;
        std::fs::rename(&tmp, path).with_context(|| format!("rename {}", path.display()))?;
        Ok(())
    }

    fn read_file(path: &Path, expected_n: usize) -> Result<Option<Self>> {
        let bytes = match std::fs::read(path) {
            Ok(b) => b,
            Err(e) if e.kind() == io::ErrorKind::NotFound => return Ok(None),
            Err(e) => {
                return Err(e).with_context(|| format!("read {}", path.display()));
            }
        };
        if bytes.len() < 9 || &bytes[..4] != MAGIC || bytes[4] != VERSION {
            return Ok(None);
        }
        let n = u32::from_le_bytes(bytes[5..9].try_into().unwrap()) as usize;
        if n != expected_n {
            return Ok(None);
        }
        let need = n * n;
        let byte_len = need.div_ceil(8);
        if bytes.len() != 9 + byte_len {
            return Ok(None);
        }
        Ok(Some(Self {
            n: n as u32,
            bits: bytes[9..].to_vec(),
        }))
    }

    pub fn has_eligible_cells(&self) -> bool {
        self.bits.iter().any(|&b| b != 0)
    }

    /// Downsample to `MASK_ROW_SAMPLES²` watch grid: 1 = not eligible, 2 = eligible.
    pub fn viz_samples(&self) -> Vec<u8> {
        let n = self.n as usize;
        let w = MASK_ROW_SAMPLES;
        let mut out = vec![1u8; w * w];
        if n == 0 {
            return out;
        }
        for sy in 0..w {
            let iy0 = sy * n / w;
            let iy1 = ((sy + 1) * n / w).min(n);
            for sx in 0..w {
                let ix0 = sx * n / w;
                let ix1 = ((sx + 1) * n / w).min(n);
                let mut any = false;
                'cell: for iy in iy0..iy1 {
                    for ix in ix0..ix1 {
                        if self.get(iy, ix, n) {
                            any = true;
                            break 'cell;
                        }
                    }
                }
                out[sy * w + sx] = if any { 2 } else { 1 };
            }
        }
        out
    }

    fn read_file_any(path: &Path) -> Result<Option<Self>> {
        let bytes = match std::fs::read(path) {
            Ok(b) => b,
            Err(e) if e.kind() == io::ErrorKind::NotFound => return Ok(None),
            Err(e) => {
                return Err(e).with_context(|| format!("read {}", path.display()));
            }
        };
        if bytes.len() < 9 || &bytes[..4] != MAGIC || bytes[4] != VERSION {
            return Ok(None);
        }
        let n = u32::from_le_bytes(bytes[5..9].try_into().unwrap()) as usize;
        let need = n * n;
        let byte_len = need.div_ceil(8);
        if bytes.len() != 9 + byte_len {
            return Ok(None);
        }
        Ok(Some(Self {
            n: n as u32,
            bits: bytes[9..].to_vec(),
        }))
    }
}

/// Load a cached `.elmk` and return the watch viz grid, if present.
pub fn load_tile_mask_viz(cache_dir: &Path, tile_stem: &str) -> Result<Option<Vec<u8>>> {
    let path = dem_mask_path(cache_dir, tile_stem);
    Ok(TileEligibleMask::read_file_any(&path)?.map(|m| m.viz_samples()))
}

/// Pixel space for a Skadi 1° tile: sample `(ix,iy)` center is `(ix+0.5, iy+0.5)`.
struct TileGrid {
    n: usize,
    sw_lon: f64,
    north: f64,
    spacing: f64,
}

impl TileGrid {
    fn new(tile: &DemTile) -> Self {
        Self {
            n: tile.n,
            sw_lon: tile.sw_lon,
            north: tile.sw_lat + 1.0,
            spacing: tile.spacing_deg(),
        }
    }

    fn to_pix(&self, lon: f64, lat: f64) -> (f64, f64) {
        let x = (lon - self.sw_lon) / self.spacing;
        let y = (self.north - lat) / self.spacing;
        (x, y)
    }
}

fn ring_to_pix(grid: &TileGrid, ring: &geo::LineString<f64>) -> Vec<(f64, f64)> {
    ring.coords_iter()
        .map(|c| grid.to_pix(c.x, c.y))
        .collect()
}

fn mark_cell(cells: &mut [bool], n: usize, ix: i32, iy: i32) {
    if ix >= 0 && iy >= 0 && (ix as usize) < n && (iy as usize) < n {
        cells[iy as usize * n + ix as usize] = true;
    }
}

/// Burn every DEM cell an edge crosses (all-touched edge contribution).
fn burn_edge(cells: &mut [bool], n: usize, x0: f64, y0: f64, x1: f64, y1: f64) {
    let dx = x1 - x0;
    let dy = y1 - y0;
    let steps = dx.abs().max(dy.abs()).ceil() as i32;
    let steps = steps.max(1);
    let inv = 1.0 / steps as f64;
    for i in 0..=steps {
        let t = i as f64 * inv;
        let x = x0 + dx * t;
        let y = y0 + dy * t;
        mark_cell(cells, n, x.floor() as i32, y.floor() as i32);
    }
}

fn burn_ring_edges(cells: &mut [bool], n: usize, ring: &[(f64, f64)]) {
    if ring.len() < 2 {
        return;
    }
    for i in 0..ring.len() - 1 {
        let (x0, y0) = ring[i];
        let (x1, y1) = ring[i + 1];
        burn_edge(cells, n, x0, y0, x1, y1);
    }
}

/// Even-odd fill at pixel centers `(ix+0.5, iy+0.5)` for all rings of one polygon.
fn fill_polygon_scanlines(cells: &mut [bool], n: usize, rings: &[Vec<(f64, f64)>]) {
    let mut min_y = f64::INFINITY;
    let mut max_y = f64::NEG_INFINITY;
    for ring in rings {
        for &(_, y) in ring {
            min_y = min_y.min(y);
            max_y = max_y.max(y);
        }
    }
    if !min_y.is_finite() {
        return;
    }
    let iy0 = min_y.floor().max(0.0) as usize;
    let iy1 = max_y.ceil().min(n as f64) as usize;
    let iy1 = iy1.min(n);
    let mut xs = Vec::with_capacity(64);
    for iy in iy0..iy1 {
        let y = iy as f64 + 0.5;
        xs.clear();
        for ring in rings {
            if ring.len() < 2 {
                continue;
            }
            for i in 0..ring.len() - 1 {
                let (x0, y0) = ring[i];
                let (x1, y1) = ring[i + 1];
                // Half-open in y so vertices are not double-counted.
                if (y0 <= y && y1 > y) || (y1 <= y && y0 > y) {
                    let t = (y - y0) / (y1 - y0);
                    xs.push(x0 + t * (x1 - x0));
                }
            }
        }
        if xs.len() < 2 {
            continue;
        }
        xs.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
        let mut k = 0;
        while k + 1 < xs.len() {
            let x_lo = xs[k];
            let x_hi = xs[k + 1];
            k += 2;
            let ix0 = ((x_lo - 0.5).ceil() as i32).max(0);
            let ix1 = ((x_hi - 0.5).floor() as i32).min(n as i32 - 1);
            if ix1 < ix0 {
                continue;
            }
            let row = iy * n;
            for ix in ix0..=ix1 {
                cells[row + ix as usize] = true;
            }
        }
    }
}

fn burn_polygon(cells: &mut [bool], grid: &TileGrid, poly: &Polygon<f64>) {
    let mut rings = Vec::with_capacity(1 + poly.interiors().len());
    rings.push(ring_to_pix(grid, poly.exterior()));
    for hole in poly.interiors() {
        rings.push(ring_to_pix(grid, hole));
    }
    for ring in &rings {
        burn_ring_edges(cells, grid.n, ring);
    }
    fill_polygon_scanlines(cells, grid.n, &rings);
}

/// Rasterize eligible land onto the DEM sample grid (land only; voids applied later).
fn burn_land_cells(
    tile: &DemTile,
    land: &LandFilterIndex,
    tile_stem: &str,
    log: bool,
    on_progress: Option<&dyn Fn(usize, usize, usize, usize)>,
) -> Vec<bool> {
    let n = tile.n;
    let mut cells = vec![false; n * n];
    let grid = TileGrid::new(tile);
    // Slight pad so edge-touching parcels are included.
    let pad = grid.spacing;
    let polys = land.intersecting_polys(
        tile.sw_lon - pad,
        tile.sw_lat - pad,
        tile.sw_lon + 1.0 + pad,
        tile.sw_lat + 1.0 + pad,
    );
    let total = polys.len().max(1);
    loop_log(
        log,
        &format!("{tile_stem} eligible burn begin n={n} polys={total}"),
    );
    for (pi, poly) in polys.iter().enumerate() {
        burn_polygon(&mut cells, &grid, poly);
        if let Some(cb) = on_progress {
            if pi == 0 || pi % 25 == 0 || pi + 1 == total {
                let row = pi * n / total;
                cb(row, 0, n, 0);
            }
        }
        if log && (pi % 100 == 0 || pi + 1 == total) {
            loop_log(
                log,
                &format!("{tile_stem} eligible burn poly {}/{}", pi + 1, total),
            );
        }
    }
    loop_log(log, &format!("{tile_stem} eligible burn done"));
    cells
}

fn usable_from_land_cells(tile: &DemTile, cells: &[bool]) -> Vec<Vec<bool>> {
    let n = tile.n;
    let mut usable = vec![vec![false; n]; n];
    for iy in 0..n {
        for ix in 0..n {
            let elev = tile.elevations[iy * n + ix];
            if elev == VOID_SRTM || elev < -12000 {
                continue;
            }
            if cells[iy * n + ix] {
                usable[iy][ix] = true;
            }
        }
    }
    usable
}

fn emit_land_viz(
    cells: &[bool],
    n: usize,
    on_row: Option<&dyn Fn(usize, usize, &[u8])>,
    on_progress: Option<&dyn Fn(usize, usize, usize, usize)>,
) {
    let mut eligible_cells = 0usize;
    let mut row = vec![false; n];
    for iy in 0..n {
        for ix in 0..n {
            let on = cells[iy * n + ix];
            row[ix] = on;
            if on {
                eligible_cells += 1;
            }
        }
        if let Some(cb) = on_progress {
            if iy == 0 || iy % ((n / 72).max(25)) == 0 || iy + 1 == n {
                cb(iy, n.saturating_sub(1), n, eligible_cells);
            }
        }
        if let Some(cb) = on_row {
            if iy % MASK_ROW_SSE_EVERY == 0 || iy + 1 == n {
                let samples = downsample_row(&row, MASK_ROW_SAMPLES);
                cb(iy, n, &samples);
            }
        }
    }
}

/// Burn eligible polygons onto this DEM tile and apply elevation voids.
pub fn build_usable_grid(
    tile: &DemTile,
    land: &LandFilterIndex,
    tile_stem: &str,
    log: bool,
    on_row: Option<&dyn Fn(usize, usize, &[u8])>,
    on_progress: Option<&dyn Fn(usize, usize, usize, usize)>,
) -> (Vec<bool>, Vec<Vec<bool>>) {
    let cells = burn_land_cells(tile, land, tile_stem, log, on_progress);
    let usable = usable_from_land_cells(tile, &cells);
    emit_land_viz(&cells, tile.n, on_row, on_progress);
    (cells, usable)
}

pub fn dem_mask_path(cache_dir: &Path, tile_stem: &str) -> PathBuf {
    cache_dir.join(format!("{tile_stem}.elmk"))
}

/// Load a cached eligible mask for this DEM tile, or rasterize once and write it.
pub fn load_or_build_tile_mask(
    cache_dir: &Path,
    tile_stem: &str,
    tile: &DemTile,
    land: &LandFilterIndex,
    verbose: bool,
    on_row: Option<&dyn Fn(usize, usize, &[u8])>,
    on_progress: Option<&dyn Fn(usize, usize, usize, usize)>,
) -> Result<(TileEligibleMask, Option<Vec<Vec<bool>>>)> {
    let log = progress_enabled(verbose);
    std::fs::create_dir_all(cache_dir)
        .with_context(|| format!("create dem_masks dir {}", cache_dir.display()))?;
    let path = dem_mask_path(cache_dir, tile_stem);
    if let Some(mask) = TileEligibleMask::read_file(&path, tile.n)? {
        phase("eligible mask hit", &format!("{tile_stem} n={}", tile.n), log);
        return Ok((mask, None));
    }
    phase(
        "eligible mask build",
        &format!("{tile_stem} n={} polys≈ burn…", tile.n),
        log,
    );
    let t0 = Instant::now();
    let (cells, usable) = build_usable_grid(tile, land, tile_stem, log, on_row, on_progress);
    let mask = TileEligibleMask::from_flat(tile.n, &cells);
    mask.write_file(&path)?;
    if log {
        let eligible_cells: usize = usable
            .iter()
            .flat_map(|row| row.iter())
            .filter(|&&v| v)
            .count();
        let _ = writeln!(
            io::stderr(),
            "[splatter] [phase] eligible mask done {tile_stem} cells={eligible_cells} elapsed={:.1}s",
            t0.elapsed().as_secs_f64()
        );
        let _ = io::stderr().flush();
    }
    Ok((mask, Some(usable)))
}

pub fn usable_grid_from_mask(
    tile: &DemTile,
    mask: &TileEligibleMask,
    tile_stem: &str,
    log: bool,
) -> Vec<Vec<bool>> {
    let n = tile.n;
    let mut usable = vec![vec![false; n]; n];
    for iy in 0..n {
        loop_log(log, &format!("{tile_stem} mask apply row {iy}/{n} begin"));
        for ix in 0..n {
            let elev = tile.elevations[iy * n + ix];
            if elev == VOID_SRTM || elev < -12000 {
                continue;
            }
            if mask.get(iy, ix, n) {
                usable[iy][ix] = true;
            }
        }
        loop_log(log, &format!("{tile_stem} mask apply row {iy}/{n} done"));
    }
    usable
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::dem::DemTile;
    use crate::peaks::LandFilterIndex;
    use geo::{coord, polygon, MultiPolygon};

    fn flat_tile(sw_lat: f64, sw_lon: f64, n: usize) -> DemTile {
        DemTile {
            sw_lat,
            sw_lon,
            n,
            elevations: vec![1000; n * n],
        }
    }

    #[test]
    fn burn_fills_interior_square() {
        // Polygon covers western half of N39W120 (lon -120..-119.5).
        let poly = MultiPolygon(vec![polygon![
            (x: -120.0, y: 39.0),
            (x: -119.5, y: 39.0),
            (x: -119.5, y: 40.0),
            (x: -120.0, y: 40.0),
            (x: -120.0, y: 39.0),
        ]]);
        let land = LandFilterIndex::from_multipolygon(&poly);
        let tile = flat_tile(39.0, -120.0, 101);
        let (cells, usable) = build_usable_grid(&tile, &land, "N39W120", false, None, None);
        let n = tile.n;
        // West-edge sample should be eligible; far-east sample should not.
        assert!(cells[0 * n + 0], "NW corner land");
        assert!(usable[n / 2][n / 4], "western interior usable");
        assert!(!cells[n / 2 * n + (n - 1)], "eastern edge outside poly");
        let on = cells.iter().filter(|&&v| v).count();
        assert!(on > n * n / 5, "expected substantial western fill, got {on}");
        assert!(on < n * n * 3 / 4, "should not fill whole tile, got {on}");
    }

    #[test]
    fn burn_respects_hole() {
        let poly = MultiPolygon(vec![Polygon::new(
            geo::LineString(vec![
                coord! { x: -120.0, y: 39.0 },
                coord! { x: -119.0, y: 39.0 },
                coord! { x: -119.0, y: 40.0 },
                coord! { x: -120.0, y: 40.0 },
                coord! { x: -120.0, y: 39.0 },
            ]),
            vec![geo::LineString(vec![
                coord! { x: -119.7, y: 39.3 },
                coord! { x: -119.3, y: 39.3 },
                coord! { x: -119.3, y: 39.7 },
                coord! { x: -119.7, y: 39.7 },
                coord! { x: -119.7, y: 39.3 },
            ])],
        )]);
        let land = LandFilterIndex::from_multipolygon(&poly);
        let tile = flat_tile(39.0, -120.0, 101);
        let (cells, _) = build_usable_grid(&tile, &land, "N39W120", false, None, None);
        let n = tile.n;
        // Center of tile ≈ hole.
        let cx = n / 2;
        let cy = n / 2;
        assert!(!cells[cy * n + cx], "hole center should be clear");
        assert!(cells[1 * n + 1], "outer ring corner should be filled");
    }
}
