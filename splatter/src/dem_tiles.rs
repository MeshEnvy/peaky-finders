//! Web Mercator terrain tiles from the Skadi HGT mirror (same DEM as peak/RF analysis).

use std::io::Cursor;

use anyhow::{bail, Result};
use image::{ImageBuffer, Rgb};

use crate::dem::DemMosaic;
use crate::engine::required_tile_names_for_bounds;

pub const DEM_TILE_SIZE: u32 = 256;
/// Skadi SRTM ~30 m; zoom 13 ≈ 19 m/px at mid-latitudes.
pub const DEM_TERRAIN_MAX_ZOOM: u32 = 13;

pub fn tile_bounds_wgs84(z: u32, x: u32, y: u32) -> (f64, f64, f64, f64) {
    let n = 2.0_f64.powi(z as i32);
    let west = x as f64 / n * 360.0 - 180.0;
    let east = (x + 1) as f64 / n * 360.0 - 180.0;
    let north = tile_y_to_lat(y, n);
    let south = tile_y_to_lat(y + 1, n);
    (west, south, east, north)
}

fn tile_y_to_lat(y: u32, n: f64) -> f64 {
    let fy = y as f64 / n;
    let lat_rad = (std::f64::consts::PI * (1.0 - 2.0 * fy)).sinh().atan();
    lat_rad.to_degrees()
}

pub fn pixel_to_lat_lon(z: u32, x: u32, y: u32, px: u32, py: u32) -> (f64, f64) {
    let n = 2.0_f64.powi(z as i32);
    let fx = (x as f64 + (px as f64 + 0.5) / DEM_TILE_SIZE as f64) / n;
    let fy = (y as f64 + (py as f64 + 0.5) / DEM_TILE_SIZE as f64) / n;
    let lon = fx * 360.0 - 180.0;
    let lat = (std::f64::consts::PI * (1.0 - 2.0 * fy)).sinh().atan().to_degrees();
    (lat, lon)
}

pub fn required_tile_names_for_xyz(z: u32, x: u32, y: u32) -> Vec<String> {
    required_hgt_names_for_xyz(z, x, y, 0.0)
}

/// Skadi HGT cells needed for a web mercator tile, optionally padded for hillshade gradients.
pub fn required_hgt_names_for_xyz(z: u32, x: u32, y: u32, pad_deg: f64) -> Vec<String> {
    let (west, south, east, north) = tile_bounds_wgs84(z, x, y);
    required_tile_names_for_bounds(
        west - pad_deg,
        south - pad_deg,
        east + pad_deg,
        north + pad_deg,
    )
}

/// ~2 km pad covers 30 m hillshade finite differences near HGT/web-mercator edges.
pub const HILLSHADE_HGT_PAD_DEG: f64 = 0.02;

pub fn encode_terrarium_rgb(elevation_m: f64) -> [u8; 3] {
    let v = (elevation_m + 32768.0).round().clamp(0.0, 65535.0) as u32;
    [
        ((v >> 8) & 0xFF) as u8,
        (v & 0xFF) as u8,
        0,
    ]
}

pub fn decode_terrarium_rgb(rgb: [u8; 3]) -> f64 {
    rgb[0] as f64 * 256.0 + rgb[1] as f64 + rgb[2] as f64 / 256.0 - 32768.0
}

fn hillshade_value(dem: &DemMosaic, lat: f64, lon: f64) -> u8 {
    let spacing_m = 30.0;
    let lat_rad = lat.to_radians();
    let dlat = spacing_m / 111_320.0;
    let dlon = spacing_m / (111_320.0 * lat_rad.cos().max(0.01));
    let z = dem.sample_m(lat, lon);
    let zx = dem.sample_m(lat, lon + dlon);
    let zy = dem.sample_m(lat + dlat, lon);
    let dzdx = (zx - z) / spacing_m;
    let dzdy = (zy - z) / spacing_m;
    let slope = (dzdx * dzdx + dzdy * dzdy).sqrt();
    let aspect = dzdy.atan2(-dzdx);
    let az = 315.0_f64.to_radians();
    let alt = 50.0_f64.to_radians();
    let slope_rad = slope.atan();
    let directional = (alt.sin() * slope_rad.cos()
        + alt.cos() * slope_rad.sin() * (az - aspect).cos())
        .clamp(0.0, 1.0);
    // Lift shadows so steep north faces are not pure black (common GIS ambient term).
    const AMBIENT: f64 = 0.28;
    const WEIGHT: f64 = 0.72;
    let shade = (AMBIENT + WEIGHT * directional).clamp(0.0, 1.0);
    (shade * 255.0).round() as u8
}

pub fn render_terrarium_png(dem: &DemMosaic, z: u32, x: u32, y: u32) -> Result<Vec<u8>> {
    validate_tile_xyz(z, x, y)?;
    let mut img: ImageBuffer<Rgb<u8>, Vec<u8>> =
        ImageBuffer::new(DEM_TILE_SIZE, DEM_TILE_SIZE);
    for py in 0..DEM_TILE_SIZE {
        for px in 0..DEM_TILE_SIZE {
            let (lat, lon) = pixel_to_lat_lon(z, x, y, px, py);
            let elev = dem.sample_m(lat, lon);
            let rgb = encode_terrarium_rgb(elev);
            img.put_pixel(px, py, Rgb(rgb));
        }
    }
    encode_png(&img)
}

pub fn render_hillshade_png(dem: &DemMosaic, z: u32, x: u32, y: u32) -> Result<Vec<u8>> {
    validate_tile_xyz(z, x, y)?;
    let mut img: ImageBuffer<Rgb<u8>, Vec<u8>> =
        ImageBuffer::new(DEM_TILE_SIZE, DEM_TILE_SIZE);
    for py in 0..DEM_TILE_SIZE {
        for px in 0..DEM_TILE_SIZE {
            let (lat, lon) = pixel_to_lat_lon(z, x, y, px, py);
            let g = hillshade_value(dem, lat, lon);
            img.put_pixel(px, py, Rgb([g, g, g]));
        }
    }
    encode_png(&img)
}

fn validate_tile_xyz(z: u32, x: u32, y: u32) -> Result<()> {
    if z > DEM_TERRAIN_MAX_ZOOM {
        bail!("zoom {z} exceeds Skadi terrain max {DEM_TERRAIN_MAX_ZOOM}");
    }
    let n = 1u32 << z;
    if x >= n || y >= n {
        bail!("tile {z}/{x}/{y} out of range");
    }
    Ok(())
}

fn encode_png(img: &ImageBuffer<Rgb<u8>, Vec<u8>>) -> Result<Vec<u8>> {
    let mut buf = Vec::new();
    img.write_to(&mut Cursor::new(&mut buf), image::ImageFormat::Png)?;
    Ok(buf)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::dem::{DemMosaic, DemTile};

    #[test]
    fn terrarium_roundtrip() {
        for elev in [-100.0, 0.0, 1580.0, 8848.0] {
            let rgb = encode_terrarium_rgb(elev);
            let back = decode_terrarium_rgb(rgb);
            assert!((back - elev).abs() < 1.0, "elev {elev} -> {back}");
        }
    }

    #[test]
    fn hillshade_tile_renders() {
        let n = 121usize;
        let tile = DemTile {
            sw_lat: 39.0,
            sw_lon: -120.0,
            n,
            elevations: vec![2000; n * n],
        };
        let dem = DemMosaic::with_single_tile((39, -120), tile);
        let png = render_hillshade_png(&dem, 10, 165, 395).expect("hillshade");
        assert!(png.starts_with(&[0x89, 0x50, 0x4E, 0x47]));
    }
}
