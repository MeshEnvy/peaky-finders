//! Access routing network GeoJSON and project ``roads.yaml`` API.

use std::path::Path;

use anyhow::{Context, Result};
use peaky_peaks::{NetworkLayer, NetworkViewBBox};
use peaky_preset::{ensure_roads_book, roads_path_for_config, save_roads_book, RoadsBook};

use crate::warm::WarmHub;

pub fn roads_network_layer_geojson_bytes(
    warm: &WarmHub,
    preset_path: &Path,
    layer: NetworkLayer,
    view: NetworkViewBBox,
    zoom: f64,
) -> Result<Vec<u8>> {
    warm.roads_network_layer_geojson_bytes(preset_path, layer, view, zoom)
}

pub fn roads_network_drive_geojson_bytes(
    warm: &WarmHub,
    preset_path: &Path,
    view: NetworkViewBBox,
    zoom: f64,
) -> Result<Vec<u8>> {
    warm.roads_network_drive_geojson_bytes(preset_path, view, zoom)
}

pub fn roads_network_all_geojson_bytes(
    warm: &WarmHub,
    preset_path: &Path,
    view: NetworkViewBBox,
) -> Result<Vec<u8>> {
    warm.roads_network_all_geojson_bytes(preset_path, view)
}

pub fn roads_book_get(preset_path: &Path) -> Result<RoadsBook> {
    ensure_roads_book(preset_path)
}

pub fn roads_book_put(preset_path: &Path, warm: &WarmHub, book: &RoadsBook) -> Result<()> {
    let previous = roads_book_get(preset_path)?;
    let path = roads_path_for_config(preset_path)?;
    save_roads_book(&path, book)?;
    warm.apply_roads_book_to_cached_routing(preset_path, &previous, book)?;
    Ok(())
}
