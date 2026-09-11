//! Read/write ``peaks.yaml`` eligible-peaks catalog.

use std::collections::HashMap;
use std::path::Path;

use anyhow::{Context, Result};
use serde_yaml::{Mapping, Value};

use crate::model::{PeakCatalogEntry, PeaksCatalog};
use crate::project::ProjectLayout;
use crate::yaml_io::{read_preset_document, write_preset_value};

fn yaml_key(s: &str) -> Value {
    Value::from(s)
}

pub fn peaks_catalog_path(config_path: &Path) -> Result<std::path::PathBuf> {
    Ok(ProjectLayout::from_config_path(config_path)?.peaks_document_path())
}

pub fn load_peaks_catalog(config_path: &Path) -> Result<PeaksCatalog> {
    let path = peaks_catalog_path(config_path)?;
    if !path.is_file() {
        return Ok(PeaksCatalog::default());
    }
    let doc = read_preset_document(&path)?;
    let peaks_val = doc
        .get(&yaml_key("peaks"))
        .cloned()
        .unwrap_or(Value::Mapping(Mapping::new()));
    serde_yaml::from_value(peaks_val).context("parse peaks.yaml")
}

pub fn write_peaks_catalog(config_path: &Path, catalog: &PeaksCatalog) -> Result<()> {
    let path = peaks_catalog_path(config_path)?;
    let mut doc = Mapping::new();
    doc.insert(
        yaml_key("peaks"),
        serde_yaml::to_value(catalog).context("serialize peaks catalog")?,
    );
    write_preset_value(&path, Value::Mapping(doc))
}

/// Merge ``deny: true`` rows from an existing catalog into a freshly built one.
pub fn preserve_denied_entries(
    new_catalog: &mut PeaksCatalog,
    previous: &PeaksCatalog,
) -> usize {
    let mut kept = 0usize;
    for (slug, entry) in &previous.entries {
        if !entry.deny.unwrap_or(false) {
            continue;
        }
        new_catalog.entries.insert(slug.clone(), entry.clone());
        kept += 1;
    }
    kept
}

pub fn denied_slugs(catalog: &PeaksCatalog) -> HashMap<String, PeakCatalogEntry> {
    catalog
        .entries
        .iter()
        .filter(|(_, e)| e.deny.unwrap_or(false))
        .map(|(k, v)| (k.clone(), v.clone()))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::PeakAccessRules;

    #[test]
    fn write_and_load_peaks_catalog_roundtrip() {
        let dir = tempfile::tempdir().unwrap();
        let config = dir.path().join("config.yaml");
        std::fs::write(&config, "simulation:\n  radius_km: 50\n").unwrap();
        let catalog = PeaksCatalog {
            generated_at: "2026-09-10".into(),
            rules: PeakAccessRules {
                max_hike_m: 805.0,
                max_slope_deg: 28.0,
                road_highways: vec!["track".into()],
            },
            entries: HashMap::from([(
                "peak-test".into(),
                PeakCatalogEntry {
                    name: Some("Test".into()),
                    loc: [38.0, -117.0],
                    elev_m: Some(2100.0),
                    source: "gnis".into(),
                    road_m: Some(120.0),
                    road_loc: Some([38.0, -117.01]),
                    hike_m: Some(80.0),
                    max_slope_deg: Some(12.0),
                    deny: None,
                },
            )]),
        };
        write_peaks_catalog(&config, &catalog).unwrap();
        let loaded = load_peaks_catalog(&config).unwrap();
        assert_eq!(loaded.generated_at, "2026-09-10");
        assert_eq!(loaded.entries.len(), 1);
        assert_eq!(loaded.entries["peak-test"].source, "gnis");
    }

    #[test]
    fn preserve_denied_entries_keeps_operator_overrides() {
        let mut prev = PeaksCatalog::default();
        prev.entries.insert(
            "skip-ridge".into(),
            PeakCatalogEntry {
                name: Some("Skip".into()),
                loc: [38.0, -117.0],
                elev_m: Some(2000.0),
                source: "dem".into(),
                road_m: None,
                road_loc: None,
                hike_m: None,
                max_slope_deg: None,
                deny: Some(true),
            },
        );
        let mut next = PeaksCatalog {
            generated_at: "2026-09-10".into(),
            rules: PeakAccessRules::default(),
            entries: HashMap::new(),
        };
        next.entries.insert(
            "good-peak".into(),
            PeakCatalogEntry {
                name: Some("Good".into()),
                loc: [38.1, -117.1],
                elev_m: Some(2100.0),
                source: "gnis".into(),
                road_m: Some(200.0),
                road_loc: Some([38.1, -117.11]),
                hike_m: Some(150.0),
                max_slope_deg: Some(12.0),
                deny: None,
            },
        );
        let n = preserve_denied_entries(&mut next, &prev);
        assert_eq!(n, 1);
        assert!(next.entries.contains_key("skip-ridge"));
        assert!(next.entries.contains_key("good-peak"));
        assert_eq!(next.entries["skip-ridge"].deny, Some(true));
    }
}
