//! Site slug and API row helpers.

use std::collections::HashSet;

use serde_yaml::Mapping;

use crate::model::{slugify_files_segment, SiteEntry};

fn is_valid_site_tag(tag: &str) -> bool {
    if tag.is_empty() {
        return false;
    }
    let bytes = tag.as_bytes();
    if !bytes[0].is_ascii_alphanumeric() || !bytes[bytes.len() - 1].is_ascii_alphanumeric() {
        return false;
    }
    bytes
        .iter()
        .all(|b| b.is_ascii_alphanumeric() || *b == b'-' || *b == b'_')
}

/// Normalize site tags: lowercase, unique, stable order of first appearance.
pub fn normalize_site_tags(raw: Option<&serde_yaml::Value>) -> Result<Vec<String>, String> {
    let items: Vec<String> = match raw {
        None => Vec::new(),
        Some(serde_yaml::Value::String(s)) => vec![s.clone()],
        Some(serde_yaml::Value::Sequence(seq)) => seq
            .iter()
            .map(|v| {
                v.as_str()
                    .ok_or_else(|| "tags must be a list of strings".to_string())
                    .map(|s| s.to_string())
            })
            .collect::<Result<Vec<_>, _>>()?,
        Some(_) => return Err("tags must be a list of strings".to_string()),
    };

    let mut out = Vec::new();
    let mut seen = HashSet::new();
    for item in items {
        let tag = item.trim().to_lowercase();
        if tag.is_empty() {
            continue;
        }
        if !is_valid_site_tag(&tag) {
            return Err(format!(
                "invalid tag {item:?}: use lowercase letters, digits, hyphens, underscores"
            ));
        }
        if seen.insert(tag.clone()) {
            out.push(tag);
        }
    }
    Ok(out)
}

/// Derive a unique site key from `name` (same rules as list-import in coerce_preset_sites).
pub fn unique_site_slug(existing: &HashSet<String>, name: &str) -> String {
    let base = slugify_files_segment(name);
    if !existing.contains(&base) {
        return base;
    }
    let mut n = 2u32;
    loop {
        let candidate = format!("{base}-{n}");
        if !existing.contains(&candidate) {
            return candidate;
        }
        n += 1;
    }
}

pub fn validate_coords(lat: f64, lon: f64) -> Result<(), String> {
    if !(-90.0..=90.0).contains(&lat) {
        return Err(format!("lat out of bounds: {lat}"));
    }
    if !(-180.0..=180.0).contains(&lon) {
        return Err(format!("lon out of bounds: {lon}"));
    }
    Ok(())
}

pub fn validate_site_height(height_m: Option<f64>) -> Result<(), String> {
    if let Some(h) = height_m {
        if h < 1.0 {
            return Err("height_m must be >= 1".to_string());
        }
    }
    Ok(())
}

/// Build a serve API site row from a YAML `sites` entry (no full preset load).
pub fn site_row_from_entry(slug: &str, ent: &SiteEntry) -> serde_json::Map<String, serde_json::Value> {
    let mut row = serde_json::Map::new();
    row.insert("slug".into(), serde_json::Value::String(slug.to_string()));
    row.insert("name".into(), serde_json::Value::String(ent.name.clone()));
    row.insert("lat".into(), serde_json::json!(ent.lat()));
    row.insert("lon".into(), serde_json::json!(ent.lon()));
    row.insert(
        "tags".into(),
        serde_json::Value::Array(
            ent.tags
                .iter()
                .cloned()
                .map(serde_json::Value::String)
                .collect(),
        ),
    );
    if let Some(h) = ent.height_m {
        row.insert("height_m".into(), serde_json::json!(h));
    }
    if let Some(desc) = &ent.description {
        let s = desc.trim();
        if !s.is_empty() {
            row.insert("description".into(), serde_json::Value::String(s.to_string()));
        }
    }
    row
}

/// Build a serve API site row from a raw YAML mapping.
pub fn site_row_from_yaml_ent(slug: &str, ent: &Mapping) -> Result<serde_json::Map<String, serde_json::Value>, String> {
    let loc = ent
        .get(serde_yaml::Value::String("loc".into()))
        .and_then(|v| v.as_sequence())
        .ok_or_else(|| format!("sites.{slug}.loc must be [lat, lon]"))?;
    if loc.len() < 2 {
        return Err(format!("sites.{slug}.loc must be [lat, lon]"));
    }
    let lat = loc[0]
        .as_f64()
        .ok_or_else(|| format!("sites.{slug}.loc[0] must be numeric"))?;
    let lon = loc[1]
        .as_f64()
        .ok_or_else(|| format!("sites.{slug}.loc[1] must be numeric"))?;
    validate_coords(lat, lon)?;

    let name = ent
        .get(serde_yaml::Value::String("name".into()))
        .and_then(|v| v.as_str())
        .unwrap_or(slug)
        .to_string();
    let tags = normalize_site_tags(ent.get(serde_yaml::Value::String("tags".into())))?;

    let mut row = serde_json::Map::new();
    row.insert("slug".into(), serde_json::Value::String(slug.to_string()));
    row.insert("name".into(), serde_json::Value::String(name));
    row.insert("lat".into(), serde_json::json!(lat));
    row.insert("lon".into(), serde_json::json!(lon));
    row.insert(
        "tags".into(),
        serde_json::Value::Array(
            tags.iter()
                .cloned()
                .map(serde_json::Value::String)
                .collect(),
        ),
    );

    if let Some(h) = ent
        .get(serde_yaml::Value::String("height_m".into()))
        .and_then(|v| v.as_f64())
    {
        validate_site_height(Some(h))?;
        row.insert("height_m".into(), serde_json::json!(h));
    }
    for key in ["description"] {
        if let Some(val) = ent
            .get(serde_yaml::Value::String(key.into()))
            .and_then(|v| v.as_str())
        {
            let s = val.trim();
            if !s.is_empty() {
                row.insert(key.into(), serde_json::Value::String(s.to_string()));
            }
        }
    }
    Ok(row)
}

pub fn slugify(name: &str) -> String {
    slugify_files_segment(name)
}
