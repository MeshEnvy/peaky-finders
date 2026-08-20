//! Vector footprint from SPLAT ``output.ppm`` (minimal bounding-box polygon).

pub use splatter::ppm::write_ppm_rgb;

use std::path::Path;

use anyhow::{bail, Context, Result};
use geo::{Coord, Geometry, LineString, Polygon};

#[derive(Debug, Clone)]
pub struct LatLonBox {
    pub north: f64,
    pub south: f64,
    pub east: f64,
    pub west: f64,
    pub rotation_deg: f64,
}

impl LatLonBox {
    pub fn from_map(bbox: &std::collections::HashMap<String, f64>) -> Result<Self> {
        let north = *bbox.get("north").context("bbox.north")?;
        let south = *bbox.get("south").context("bbox.south")?;
        let east = *bbox.get("east").context("bbox.east")?;
        let west = *bbox.get("west").context("bbox.west")?;
        let rotation_deg = bbox.get("rotation").copied().unwrap_or(0.0);
        let rotation_deg = if rotation_deg.is_nan() {
            0.0
        } else {
            rotation_deg
        };
        Ok(Self {
            north,
            south,
            east,
            west,
            rotation_deg,
        })
    }
}

pub fn coverage_mask_from_splat_ppm_rgb(rgb: &[u8], width: u32, height: u32) -> Vec<u8> {
    let mut mask = vec![0u8; (width * height) as usize];
    let w = width as usize;
    let h = height as usize;
    for row in 0..h {
        for col in 0..w {
            let idx = (row * w + col) * 3;
            if idx + 2 >= rgb.len() {
                continue;
            }
            let r = rgb[idx];
            let g = rgb[idx + 1];
            let b = rgb[idx + 2];
            if !(r == 255 && g == 255 && b == 255) {
                mask[row * w + col] = 1;
            }
        }
    }
    mask
}

pub fn read_ppm_rgb(path: &Path) -> Result<(u32, u32, Vec<u8>)> {
    let bytes = std::fs::read(path).with_context(|| format!("read {}", path.display()))?;
    let header_end = find_ppm_header_end(&bytes).context("PPM header")?;
    let header = std::str::from_utf8(&bytes[..header_end]).context("PPM header utf8")?;
    let mut tokens = header
        .split_whitespace()
        .filter(|t| !t.starts_with('#'))
        .flat_map(|line| line.split('#').next().unwrap_or("").split_whitespace())
        .peekable();
    let magic = tokens.next().context("PPM magic")?;
    if magic != "P6" {
        bail!("expected P6 PPM, got {magic}");
    }
    let width: u32 = tokens.next().context("PPM width")?.parse().context("PPM width parse")?;
    let height: u32 = tokens.next().context("PPM height")?.parse().context("PPM height parse")?;
    let maxval: u32 = tokens.next().context("PPM maxval")?.parse().context("PPM maxval parse")?;
    if maxval != 255 {
        bail!("only 8-bit PPM supported");
    }
    let expected = (width * height * 3) as usize;
    let rgb = bytes[header_end..]
        .iter()
        .copied()
        .skip_while(u8::is_ascii_whitespace)
        .take(expected)
        .collect::<Vec<u8>>();
    if rgb.len() != expected {
        bail!(
            "PPM body length {} != expected {}",
            rgb.len(),
            expected
        );
    }
    Ok((width, height, rgb))
}

fn find_ppm_header_end(bytes: &[u8]) -> Option<usize> {
    let mut count = 0usize;
    let mut i = 0usize;
    while i < bytes.len() {
        if bytes[i].is_ascii_whitespace() {
            i += 1;
            continue;
        }
        if bytes[i] == b'#' {
            while i < bytes.len() && bytes[i] != b'\n' {
                i += 1;
            }
            continue;
        }
        let start = i;
        while i < bytes.len() && !bytes[i].is_ascii_whitespace() && bytes[i] != b'#' {
            i += 1;
        }
        let token = &bytes[start..i];
        if !token.is_empty() {
            count += 1;
            if count == 4 {
                while i < bytes.len() && bytes[i].is_ascii_whitespace() {
                    i += 1;
                }
                return Some(i);
            }
        }
    }
    None
}

pub fn fraction_to_lat_lon(
    u_frac: f64,
    v_frac: f64,
    bbox: &LatLonBox,
) -> Result<(f64, f64)> {
    let lon_half = (bbox.east - bbox.west) / 2.0;
    let lat_half = (bbox.north - bbox.south) / 2.0;
    if lon_half <= 0.0 || lat_half <= 0.0 {
        bail!("Invalid bbox half-extents");
    }
    let lon_c = (bbox.east + bbox.west) / 2.0;
    let lat_c = (bbox.north + bbox.south) / 2.0;
    let theta = bbox.rotation_deg.to_radians();
    let ix = 2.0 * u_frac - 1.0;
    let iy = 1.0 - 2.0 * v_frac;
    let gx = ix * theta.cos() - iy * theta.sin();
    let gy = ix * theta.sin() + iy * theta.cos();
    let lon = lon_c + gx * lon_half;
    let lat = lat_c + gy * lat_half;
    Ok((lat, lon))
}

pub fn pixel_to_lat_lon(
    col: f64,
    row: f64,
    width: u32,
    height: u32,
    bbox: &LatLonBox,
) -> Result<(f64, f64)> {
    if width == 0 || height == 0 {
        bail!("width and height must be positive");
    }
    fraction_to_lat_lon(
        col / f64::from(width),
        row / f64::from(height),
        bbox,
    )
}

/// Minimal polygonize: axis-aligned bounding box of covered pixels mapped to WGS84.
pub fn polygonize_ppm_coverage_bbox(
    ppm_path: &Path,
    bbox: &LatLonBox,
) -> Result<Option<Geometry<f64>>> {
    let (width, height, rgb) = read_ppm_rgb(ppm_path)?;
    let mask = coverage_mask_from_splat_ppm_rgb(&rgb, width, height);
    let w = width as usize;
    let h = height as usize;

    let mut min_col = w;
    let mut min_row = h;
    let mut max_col = 0usize;
    let mut max_row = 0usize;
    let mut any = false;
    for row in 0..h {
        for col in 0..w {
            if mask[row * w + col] == 1 {
                any = true;
                min_col = min_col.min(col);
                min_row = min_row.min(row);
                max_col = max_col.max(col + 1);
                max_row = max_row.max(row + 1);
            }
        }
    }
    if !any {
        return Ok(None);
    }

    let corners = [
        pixel_to_lat_lon(min_col as f64, min_row as f64, width, height, bbox)?,
        pixel_to_lat_lon(max_col as f64, min_row as f64, width, height, bbox)?,
        pixel_to_lat_lon(max_col as f64, max_row as f64, width, height, bbox)?,
        pixel_to_lat_lon(min_col as f64, max_row as f64, width, height, bbox)?,
    ];
    let mut ring: LineString = corners
        .into_iter()
        .map(|(lat, lon)| Coord { x: lon, y: lat })
        .collect();
    if let Some(first) = ring.0.first().copied() {
        ring.0.push(first);
    }
    Ok(Some(Geometry::Polygon(Polygon::new(ring, vec![]))))
}

pub fn polygonize_ppm_coverage(
    ppm_path: &Path,
    bbox_map: &std::collections::HashMap<String, f64>,
) -> Result<Option<Geometry<f64>>> {
    let bbox = LatLonBox::from_map(bbox_map)?;
    polygonize_ppm_coverage_bbox(ppm_path, &bbox)
}
