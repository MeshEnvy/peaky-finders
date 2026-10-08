//! Access routing network GeoJSON for map overlays.

use std::path::Path;

use anyhow::Result;
use peaky_peaks::{NetworkLayer, NetworkViewBBox};

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
