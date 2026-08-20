//! Coordinate placement prefetch (draft links at a lat/lon).

use std::path::Path;
use std::sync::Arc;

use anyhow::Result;
use peaky_preset::{load_preset, Preset};
use serde_json::{json, Value};
use splatter::Session;

use crate::links::load_coords_site_links;

#[derive(Debug, thiserror::Error)]
#[error("{0}")]
pub struct SitePrefetchError(pub String);

pub async fn load_site_placement_prefetch(
    session: Arc<Session>,
    preset_path: &Path,
    lat: f64,
    lon: f64,
    exclude_site_slug: Option<&str>,
) -> Result<Value, SitePrefetchError> {
    let preset = load_preset(preset_path).map_err(|e| SitePrefetchError(format!("invalid preset: {e:#}")))?;
    let preset_for_links = preset.clone();
    let links = tokio::task::spawn_blocking({
        let session = session.clone();
        let preset_path = preset_path.to_path_buf();
        let exclude = exclude_site_slug.map(str::to_string);
        move || load_coords_site_links(&session, &preset_path, &preset_for_links, lat, lon, exclude.as_deref())
    })
    .await
    .map_err(|e| SitePrefetchError(format!("links task join: {e}")))?
    .map_err(|e| SitePrefetchError(e.0))?;

    let link_features: Vec<Value> = links
        .iter()
        .filter_map(|row| coords_link_feature(lat, lon, &preset, row))
        .collect();

    Ok(json!({
        "lat": lat,
        "lon": lon,
        "links": links,
        "links_geojson": { "type": "FeatureCollection", "features": link_features },
    }))
}

fn coords_link_feature(lat: f64, lon: f64, preset: &Preset, row: &Value) -> Option<Value> {
    let slug = row.get("slug")?.as_str()?;
    let site = preset.sites.get(slug)?;
    Some(json!({
        "type": "Feature",
        "geometry": {
            "type": "LineString",
            "coordinates": [
                [lon, lat],
                [site.loc[1], site.loc[0]],
            ],
        },
        "properties": {
            "slug": slug,
            "manual": row.get("manual").and_then(|v| v.as_bool()).unwrap_or(false),
            "distance_km": row.get("distance_km"),
        },
    }))
}
