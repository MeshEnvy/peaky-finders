//! Resolve land data paths under ``project_dir/data/`` (GeoJSON at runtime).

use std::path::{Path, PathBuf};

use anyhow::{bail, Context, Result};
use peaky_preset::slugify_files_segment;

const LAND_GEOJSON_SUFFIXES: &[&str] = &[".geojson", ".json"];

pub fn is_land_geojson_path(path: impl AsRef<Path>) -> bool {
    let lower = path
        .as_ref()
        .to_string_lossy()
        .to_ascii_lowercase();
    LAND_GEOJSON_SUFFIXES
        .iter()
        .any(|suffix| lower.ends_with(suffix))
}

pub fn resolve_land_source_path(project_dir: &Path, rel_path: &str) -> Result<PathBuf> {
    let root = project_dir
        .canonicalize()
        .with_context(|| format!("resolve project dir {}", project_dir.display()))?;
    let data_root = root.join("data");
    let normalized = rel_path.trim().replace('\\', "/");
    if normalized.is_empty() {
        bail!("path is required");
    }
    if normalized.starts_with('/') || normalized.split('/').any(|part| part == "..") {
        bail!("path must be relative to the project directory");
    }
    let lower = normalized.to_ascii_lowercase();
    if !LAND_GEOJSON_SUFFIXES.iter().any(|s| lower.ends_with(s)) {
        bail!("path must end with .geojson or .json");
    }
    let resolved = root
        .join(&normalized)
        .canonicalize()
        .with_context(|| format!("GeoJSON not found: {normalized}"))?;
    if !resolved.is_file() {
        bail!("GeoJSON not found: {normalized}");
    }
    let data_canon = data_root.canonicalize().unwrap_or(data_root);
    if !resolved.starts_with(&data_canon) {
        bail!("path must be under data/");
    }
    Ok(resolved)
}

/// Resolve a land layer to a GeoJSON file. Preset paths may still reference ``.gdb``;
/// v5 reads v4-exported cache under ``.peaky/cache/land/{source_id}/{layer}.geojson``.
pub fn resolve_land_layer_geojson_path(
    project_dir: &Path,
    source_id: &str,
    layer_name: &str,
    rel_path: &str,
) -> Result<PathBuf> {
    let normalized = rel_path.trim().replace('\\', "/");
    let lower = normalized.to_ascii_lowercase();

    if LAND_GEOJSON_SUFFIXES.iter().any(|s| lower.ends_with(s)) {
        return resolve_land_source_path(project_dir, &normalized);
    }

    if lower.ends_with(".gdb") {
        if let Some(path) = full_geojson_fallback(project_dir, source_id) {
            return Ok(path);
        }

        let safe_layer = slugify_files_segment(layer_name);
        let cache_path = project_dir
            .join(".peaky/cache/land")
            .join(source_id)
            .join(format!("{safe_layer}.geojson"));
        if cache_path.is_file() {
            return cache_path
                .canonicalize()
                .with_context(|| format!("resolve cached GeoJSON {}", cache_path.display()));
        }
        bail!(
            "GeoJSON cache missing for GDB layer {source_id}/{layer_name} \
             (expected {}); warm land in v4 serve once or add GeoJSON under data/",
            cache_path.display()
        );
    }

    bail!("land path must end with .geojson, .json, or .gdb");
}

fn full_geojson_fallback(project_dir: &Path, source_id: &str) -> Option<PathBuf> {
    const FALLBACKS: &[(&str, &str)] = &[
        (
            "blm-nv-field-office-boundary-polygons",
            "data/blm-nv-field-office-boundaries.geojson",
        ),
        (
            "blm-nevada-surface-management-agency",
            "data/blm-nevada-surface-management-agency.geojson",
        ),
    ];
    for (sid, rel) in FALLBACKS {
        if *sid == source_id {
            let path = project_dir.join(rel);
            if path.is_file() {
                return path.canonicalize().ok();
            }
        }
    }
    None
}
