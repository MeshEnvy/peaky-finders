//! GDAL subprocess helpers: georeference native splat.png, mosaic, tile.

use std::path::{Path, PathBuf};
use std::process::Command;

use anyhow::{bail, Context, Result};
use peaky_geo::polygonize::{fraction_to_lat_lon, LatLonBox};
use peaky_serve::load_bounds_from_manifest;
use tracing::info;

fn find_on_path(names: &[&str]) -> Result<PathBuf> {
    let path_var = std::env::var_os("PATH").unwrap_or_default();
    for name in names {
        for dir in std::env::split_paths(&path_var) {
            let candidate = dir.join(name);
            if candidate.is_file() {
                return Ok(candidate);
            }
        }
    }
    bail!("{} not on PATH (install gdal-bin)", names.join(" or "))
}

fn png_size(path: &Path) -> Result<(u32, u32)> {
    let bytes = std::fs::read(path).with_context(|| format!("read {}", path.display()))?;
    if bytes.len() < 24 || &bytes[0..8] != b"\x89PNG\r\n\x1a\n" {
        bail!("{} is not a PNG", path.display());
    }
    let w = u32::from_be_bytes(bytes[16..20].try_into()?);
    let h = u32::from_be_bytes(bytes[20..24].try_into()?);
    if w == 0 || h == 0 {
        bail!("{} has empty PNG dimensions", path.display());
    }
    Ok((w, h))
}

/// Georeference a Peaky `splat.png` with its viewshed manifest bbox.
pub fn georeference_splat_png(png: &Path, manifest: &Path, dest: &Path) -> Result<()> {
    if !png.is_file() {
        bail!("missing splat.png {}", png.display());
    }
    let bbox_map =
        load_bounds_from_manifest(manifest).with_context(|| format!("bbox {}", manifest.display()))?;
    let bbox = LatLonBox::from_map(&bbox_map)?;
    let (width, height) = png_size(png)?;
    if dest.parent().is_some() {
        std::fs::create_dir_all(dest.parent().unwrap())?;
    }

    let gdal_translate = find_on_path(&["gdal_translate", "gdal_translate.py"])?;
    let mut cmd = Command::new(&gdal_translate);
    cmd.arg("-of")
        .arg("GTiff")
        .arg("-co")
        .arg("COMPRESS=DEFLATE")
        .arg("-colorinterp")
        .arg("red,green,blue,alpha");

    if bbox.rotation_deg.abs() < 1e-9 {
        cmd.arg("-a_srs")
            .arg("EPSG:4326")
            .arg("-a_ullr")
            .arg(format!("{}", bbox.west))
            .arg(format!("{}", bbox.north))
            .arg(format!("{}", bbox.east))
            .arg(format!("{}", bbox.south));
    } else {
        let corners = [
            (0.0, 0.0, 0.0, 0.0),
            (width as f64, 0.0, 1.0, 0.0),
            (width as f64, height as f64, 1.0, 1.0),
            (0.0, height as f64, 0.0, 1.0),
        ];
        for (px, py, u, v) in corners {
            let (lat, lon) = fraction_to_lat_lon(u, v, &bbox)?;
            cmd.arg("-gcp")
                .arg(format!("{px}"))
                .arg(format!("{py}"))
                .arg(format!("{lon}"))
                .arg(format!("{lat}"));
        }
    }

    cmd.arg(png).arg(dest);
    let status = cmd.status().context("gdal_translate splat.png")?;
    if !status.success() {
        bail!("gdal_translate failed for {}", png.display());
    }

    if bbox.rotation_deg.abs() >= 1e-9 {
        let gdalwarp = find_on_path(&["gdalwarp", "gdalwarp.py"])?;
        let warped = dest.with_extension("gcp.tif");
        let status = Command::new(&gdalwarp)
            .arg("-t_srs")
            .arg("EPSG:4326")
            .arg("-r")
            .arg("near")
            .arg("-co")
            .arg("COMPRESS=DEFLATE")
            .arg(dest)
            .arg(&warped)
            .status()
            .context("gdalwarp apply GCPs")?;
        if !status.success() {
            bail!("gdalwarp GCP apply failed for {}", png.display());
        }
        std::fs::rename(&warped, dest)?;
    }
    Ok(())
}

pub fn mosaic_viewsheds_3857(geotiffs: &[PathBuf], dest: &Path) -> Result<()> {
    if geotiffs.is_empty() {
        bail!("no viewshed GeoTIFFs to mosaic");
    }
    if dest.parent().is_some() {
        std::fs::create_dir_all(dest.parent().unwrap())?;
    }
    let gdalwarp = find_on_path(&["gdalwarp", "gdalwarp.py"])?;
    let gdal_translate = find_on_path(&["gdal_translate", "gdal_translate.py"])?;
    let warped = dest.with_extension("warp.tif");
    if warped.exists() {
        std::fs::remove_file(&warped)?;
    }
    if dest.exists() {
        std::fs::remove_file(dest)?;
    }

    let mut cmd = Command::new(&gdalwarp);
    cmd.arg("-t_srs")
        .arg("EPSG:3857")
        .arg("-r")
        .arg("near")
        .arg("-multi")
        .arg("-wo")
        .arg("NUM_THREADS=ALL_CPUS")
        .arg("-co")
        .arg("COMPRESS=DEFLATE")
        .arg("-co")
        .arg("TILED=YES");
    for tif in geotiffs {
        cmd.arg(tif);
    }
    cmd.arg(&warped);
    info!("mosaicing {} native viewshed raster(s)", geotiffs.len());
    let status = cmd.status().context("gdalwarp mosaic")?;
    if !status.success() {
        bail!("gdalwarp mosaic failed with status {status}");
    }

    let status = Command::new(&gdal_translate)
        .arg("-colorinterp")
        .arg("red,green,blue,alpha")
        .arg("-co")
        .arg("PHOTOMETRIC=RGB")
        .arg("-co")
        .arg("COMPRESS=DEFLATE")
        .arg("-co")
        .arg("TILED=YES")
        .arg(&warped)
        .arg(dest)
        .status()
        .context("gdal_translate rgba tags")?;
    let _ = std::fs::remove_file(&warped);
    if !status.success() {
        bail!("gdal_translate rgba tags failed");
    }
    Ok(())
}

pub fn run_gdal2tiles(rgba3857: &Path, out_dir: &Path, z_min: u32, z_max: u32, tile_workers: u32) -> Result<()> {
    let gdal2tiles = find_on_path(&["gdal2tiles.py", "gdal2tiles"])?;
    if out_dir.exists() {
        std::fs::remove_dir_all(out_dir)?;
    }
    std::fs::create_dir_all(out_dir)?;

    let mut cmd = Command::new(&gdal2tiles);
    cmd.arg("-z")
        .arg(format!("{z_min}-{z_max}"))
        .arg("--webviewer=none")
        .arg("--xyz")
        .arg("--resampling=near")
        .arg(rgba3857)
        .arg(out_dir);
    if tile_workers > 1 {
        cmd.arg("--processes").arg(tile_workers.to_string());
    }
    info!("running gdal2tiles: {:?}", cmd);
    let status = cmd.status().context("gdal2tiles")?;
    if !status.success() {
        bail!("gdal2tiles failed with status {status}");
    }
    Ok(())
}

pub fn write_tile_sidecar(out_dir: &Path, coverage_bbox: [f64; 4], tile_max_zoom: u32) -> Result<()> {
    let payload = serde_json::json!({
        "west": coverage_bbox[0],
        "south": coverage_bbox[1],
        "east": coverage_bbox[2],
        "north": coverage_bbox[3],
        "max_zoom": tile_max_zoom,
    });
    std::fs::write(out_dir.join("bounds.json"), serde_json::to_string(&payload)?)?;
    Ok(())
}
