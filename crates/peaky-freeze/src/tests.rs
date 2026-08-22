//! Integration tests for freeze export.

use std::fs;
use std::path::PathBuf;

use crate::{
    build_freeze_dir, default_freeze_output_dir, FreezeOptions, FREEZE_CONFIG_NAME,
    FREEZE_GEOJSON_NAME, FREEZE_MANIFEST_NAME, FREEZE_SOURCE_ID,
};
use peaky_preset::load_preset;
use serde_json::Value;
use tempfile::tempdir;

fn fixture_project() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../tests/fixtures/freeze/project/config.yaml")
}

#[test]
fn freeze_writes_distilled_base_without_sites() {
    let preset_path = fixture_project();
    let out = tempdir().unwrap();
    let summary = build_freeze_dir(
        &preset_path,
        out.path(),
        FreezeOptions {
            include_sites: false,
            verbose: false,
        },
    )
    .expect("freeze");

    assert_eq!(summary.feature_count, 2);
    assert_eq!(summary.layers.len(), 1);
    assert!(!summary.include_sites);

    assert!(out.path().join(FREEZE_CONFIG_NAME).is_file());
    assert!(out.path().join(FREEZE_GEOJSON_NAME).is_file());
    assert!(out.path().join(FREEZE_MANIFEST_NAME).is_file());

    let preset = load_preset(&out.path().join(FREEZE_CONFIG_NAME)).expect("load frozen config");
    assert!(preset.sites.is_empty());
    assert_eq!(preset.land.sources.len(), 1);
    let base = preset.land.sources.get(FREEZE_SOURCE_ID).expect("base source");
    assert_eq!(base.path, "project.geojson");
    assert_eq!(base.layers.len(), 1);
    assert_eq!(base.layers[0].include.len(), 1);
    assert_eq!(base.layers[0].include[0].field, "_peaky_layer_key");

    let geo_text = fs::read_to_string(out.path().join(FREEZE_GEOJSON_NAME)).unwrap();
    let geo: Value = serde_json::from_str(&geo_text).unwrap();
    let features = geo["features"].as_array().unwrap();
    assert_eq!(features.len(), 2);
    for feat in features {
        let props = feat["properties"].as_object().unwrap();
        assert_eq!(props.get("kind"), None);
        assert_eq!(props.get("_peaky_layer_key").unwrap(), "keep");
    }
}

#[test]
fn freeze_include_sites() {
    let preset_path = fixture_project();
    let out = tempdir().unwrap();
    let summary = build_freeze_dir(
        &preset_path,
        out.path(),
        FreezeOptions {
            include_sites: true,
            verbose: false,
        },
    )
    .expect("freeze");

    assert!(summary.include_sites);
    let preset = load_preset(&out.path().join(FREEZE_CONFIG_NAME)).expect("load frozen config");
    assert_eq!(preset.sites.len(), 1);
    assert!(preset.sites.contains_key("site-a"));
}

#[test]
fn default_output_dir_name() {
    let preset_path = fixture_project();
    let dir = default_freeze_output_dir(&preset_path);
    let name = dir.file_name().unwrap().to_str().unwrap();
    assert!(name.starts_with("project-base-"));
}
