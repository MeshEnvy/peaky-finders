//! Unified roads.yaml refresh (master graph rebuild).

use std::collections::HashSet;

use peaky_preset::RoadsBook;
use tracing::info;

use crate::osm::{duplicate_osm_routing, OsmRouting};
use crate::road_inventory::OsmRoadInventory;
use crate::roads_pipeline::{
    patch_preserves_trusted_reach as rules_fp_unchanged, update_roads, SourceFetch,
};
use crate::segment_groups::SegmentGroupKey;

pub fn patch_preserves_trusted_reach(prev: &RoadsBook, next: &RoadsBook) -> bool {
    rules_fp_unchanged(prev, next)
}

pub fn apply_roads_book_patch(
    _routing: &mut OsmRouting,
    _inventory: &mut OsmRoadInventory,
    _book: &RoadsBook,
    _sample_step_m: f64,
) {
}

pub fn refresh_inventory_roles(_inventory: &mut OsmRoadInventory, _book: &RoadsBook) {}

pub fn osm_routing_base_for_cache(routing: &OsmRouting) -> OsmRouting {
    let mut base = duplicate_osm_routing(routing);
    strip_custom_drive_segments(&mut base);
    base
}

pub fn strip_custom_drive_segments(routing: &mut OsmRouting) {
    let custom_groups: HashSet<u32> = routing
        .segment_groups
        .iter()
        .enumerate()
        .filter(|(_, g)| g.is_custom_drive())
        .map(|(i, _)| i as u32)
        .collect();
    if custom_groups.is_empty() {
        return;
    }
    let removed = custom_groups.len();
    routing
        .segment_groups
        .retain(|g| !matches!(g.key, SegmentGroupKey::Custom { .. }));
    for adj in routing.graph.adj_slice_mut() {
        adj.retain(|e| e.highway != "custom" && !custom_groups.contains(&e.group_id));
    }
    info!(removed, "roads patch: stripped custom drive segments");
}

pub fn refresh_after_roads_book_save(config_path: &std::path::Path) -> anyhow::Result<()> {
    update_roads(config_path, SourceFetch::AssumeFresh)
}
