//! Shared site creation helpers for peaky-serve.

use std::path::Path;

use peaky_preset::{
    insert_preset_site, place_slugs, unique_place_slug, validate_coords, SiteEntry,
};

#[derive(Debug, thiserror::Error)]
#[error("{0}")]
pub struct SiteCreateError(pub String);

pub fn create_site_from_peak(
    preset_path: &Path,
    name: String,
    lat: f64,
    lon: f64,
    tags: Vec<String>,
    preferred_slug: &str,
) -> Result<(String, SiteEntry), SiteCreateError> {
    validate_coords(lat, lon).map_err(SiteCreateError)?;
    let mut existing =
        place_slugs(preset_path).map_err(|e| SiteCreateError(format!("place slugs: {e}")))?;
    let pref = preferred_slug.trim();
    if !pref.is_empty() {
        existing.remove(pref);
    }
    let site_slug = if pref.is_empty() {
        peaky_preset::unique_site_slug(&existing, &name)
    } else {
        unique_place_slug(&existing, pref, &name)
    };
    let entry = SiteEntry {
        name,
        loc: [lat, lon],
        tags,
        height_m: None,
        description: None,
        node: None,
    };
    insert_preset_site(preset_path, &site_slug, &entry)
        .map_err(|e| SiteCreateError(format!("insert site: {e}")))?;
    Ok((site_slug, entry))
}
