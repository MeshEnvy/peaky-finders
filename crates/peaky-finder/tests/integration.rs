//! Integration tests for peaky-finder.

use std::path::PathBuf;

use peaky_finder::cache::{digest_hex, FinderCache};
use peaky_finder::route::load_route;
use peaky_finder::telemetry::CacheLedger;

fn fixture(path: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../tests/fixtures").join(path)
}

#[test]
fn silver_triangle_kml_parses_17_vertices() {
    let kml = fixture("routes/onx-silver-triangle.kml");
    let route = load_route(&kml, 0.0).expect("parse silver triangle");
    assert_eq!(route.waypoints.len(), 17);
    assert_eq!(route.waypoints.len().saturating_sub(1), 16);
}

#[test]
fn cache_hit_on_second_bool_read() {
    let dir = tempfile::tempdir().unwrap();
    let preset = dir.path().join("config.yaml");
    std::fs::write(&preset, "sites: {}\nlinks: []\n").unwrap();
    let cache = FinderCache::new(&preset).unwrap();
    let mut ledger = CacheLedger::new(false);
    let key = digest_hex(&["test-bool"]);
    let v1 = cache
        .get_or_insert_bool(&ledger, "cover", "cover", &key, "site=a wp=0", || Ok(true))
        .unwrap();
    let v2 = cache
        .get_or_insert_bool(&ledger, "cover", "cover", &key, "site=a wp=0", || Ok(false))
        .unwrap();
    assert!(v1);
    assert!(v2);
    let stats = ledger.stats().get("cover").cloned().unwrap_or_default();
    assert_eq!(stats.hits, 1);
    assert_eq!(stats.misses, 1);
}
