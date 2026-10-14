//! Apply ``roads.yaml`` to a cached OSM+GTLF graph without re-parsing the PBF.

use std::collections::HashSet;
use std::time::Instant;

use peaky_preset::{RoadRole, RoadsBook};
use tracing::info;

use crate::osm::{
    merge_road_customs_into_routing, merge_supplemental_jeep_lines, paved_interpolated_samples,
    sample_way, OsmRouting, SupplementalLine,
};
use crate::road_inventory::{inventory_role_from_road, InventoryRole, OsmRoadInventory};
use crate::segment_groups::{RoadSegmentGroup, SegmentGroupKey, SegmentGroupTags};

/// Remove welded custom drive segments so the graph can be re-patched or cached as base-only.
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
        adj.retain(|e| {
            e.highway != "custom" && !custom_groups.contains(&e.group_id)
        });
    }
    info!(removed, "roads patch: stripped custom drive segments");
}

fn role_for_group(group: &RoadSegmentGroup, book: &RoadsBook) -> RoadRole {
    match &group.key {
        SegmentGroupKey::Osm { osm_id } => book.classify_osm_way(
            *osm_id,
            &group.tags.highway,
            group.tags.tracktype.as_deref(),
            group.tags.surface.as_deref(),
            group.tags.fourwd_only,
        ),
        SegmentGroupKey::Gtlf {
            layer_id,
            coord_hash,
        } => {
            for ov in &book.overrides {
                if let peaky_preset::SegmentOverrideKey::Gtlf { gtlf } = &ov.key {
                    if gtlf.layer == *layer_id && gtlf.coord_hash == *coord_hash {
                        return ov.role;
                    }
                }
            }
            // Non-foot GTLF lines default to jeep. The hash is already on the group key.
            RoadRole::Jeep
        }
        SegmentGroupKey::Custom { .. } => group.role,
    }
}

/// Returns true when any group's paved membership changed.
fn reclassify_segment_groups(routing: &mut OsmRouting, book: &RoadsBook) -> bool {
    let mut paved_changed = false;
    let total = routing.segment_groups.len();
    let mut last_log = Instant::now();
    for (i, group) in routing.segment_groups.iter_mut().enumerate() {
        if matches!(group.key, SegmentGroupKey::Custom { .. }) {
            continue;
        }
        if i > 0 && (i % 100_000 == 0 || last_log.elapsed().as_secs() >= 5) {
            info!(
                done = i,
                total,
                "roads patch: reclassify progress"
            );
            last_log = Instant::now();
        }
        let next = role_for_group(group, book);
        if next != group.role {
            if group.role == RoadRole::Paved || next == RoadRole::Paved {
                paved_changed = true;
            }
            group.role = next;
        }
    }
    paved_changed
}

fn apply_group_roles_to_edges(routing: &mut OsmRouting) {
    let roles: Vec<RoadRole> = routing
        .segment_groups
        .iter()
        .map(|g| g.role)
        .collect();
    for adj in routing.graph.adj_slice_mut() {
        for edge in adj.iter_mut() {
            if let Some(role) = roles.get(edge.group_id as usize) {
                edge.role = *role;
            }
        }
    }
}

/// Rebuild paved snap anchors from segment groups. Does not touch the jeep R-tree
/// (already correct on cache load; supplemental merges update it in place).
fn rebuild_paved_anchors(routing: &mut OsmRouting, sample_step_m: f64) {
    let t0 = Instant::now();
    let paved_groups = routing
        .segment_groups
        .iter()
        .filter(|g| g.role == RoadRole::Paved && g.coords.len() >= 2)
        .count();
    info!(
        paved_groups,
        graph_nodes = routing.graph.node_count(),
        "roads patch: rebuilding paved anchors"
    );
    let mut paved_points = Vec::new();
    let mut paved_done = 0usize;
    let mut last_progress = Instant::now();
    const PAVED_PROGRESS_EVERY: usize = 5_000;

    for group in routing.segment_groups.iter() {
        if group.coords.len() < 2 {
            continue;
        }
        match group.role {
            RoadRole::Paved => {
                paved_done += 1;
                if paved_done == 1
                    || paved_done % PAVED_PROGRESS_EVERY == 0
                    || last_progress.elapsed().as_secs() >= 5
                {
                    info!(
                        paved_done,
                        paved_groups,
                        paved_anchors = paved_points.len(),
                        elapsed_secs = t0.elapsed().as_secs_f64(),
                        "roads patch: paved anchors progress"
                    );
                    last_progress = Instant::now();
                }
                let mut vert_nodes = Vec::with_capacity(group.coords.len());
                for &(lat, lon) in &group.coords {
                    if let Some(idx) = routing.graph_nodes.nearest_node(lat, lon, 80.0) {
                        vert_nodes.push(idx);
                    }
                }
                if vert_nodes.len() >= 2 {
                    for i in 0..group.coords.len().saturating_sub(1) {
                        let (lat1, lon1) = group.coords[i];
                        let (lat2, lon2) = group.coords[i + 1];
                        if i < vert_nodes.len() {
                            paved_points.push(crate::osm::PavedPoint {
                                lat: lat1,
                                lon: lon1,
                                node_idx: vert_nodes[i],
                            });
                        }
                        if i + 1 < vert_nodes.len() {
                            paved_points.push(crate::osm::PavedPoint {
                                lat: lat2,
                                lon: lon2,
                                node_idx: vert_nodes[i + 1],
                            });
                        }
                    }
                    paved_points.extend(paved_interpolated_samples(
                        &group.coords,
                        &vert_nodes,
                        sample_step_m,
                    ));
                }
            }
            _ => {}
        }
    }

    let anchor_count = paved_points.len();
    routing.paved = crate::osm::PavedAnchorIndex::from_points(paved_points);
    info!(
        paved_anchors = anchor_count,
        elapsed_secs = t0.elapsed().as_secs_f64(),
        "roads patch: paved anchors ready"
    );
}

fn weld_driveable_inventory_ways(
    routing: &mut OsmRouting,
    inventory: &OsmRoadInventory,
    book: &RoadsBook,
    sample_step_m: f64,
) {
    let known_osm: HashSet<i64> = routing
        .segment_groups
        .iter()
        .filter_map(|g| match g.key {
            SegmentGroupKey::Osm { osm_id } => Some(osm_id),
            _ => None,
        })
        .collect();
    let mut lines = Vec::new();
    for way in &inventory.ways {
        let Some(osm_id) = way.osm_id else {
            continue;
        };
        if way.coords.len() < 2 {
            continue;
        }
        let role = book.classify_osm_way(
            osm_id,
            &way.highway,
            way.tracktype.as_deref(),
            way.surface.as_deref(),
            way.fourwd_only,
        );
        if !matches!(role, RoadRole::Paved | RoadRole::Jeep) {
            continue;
        }
        if known_osm.contains(&osm_id) {
            continue;
        }
        let coords: Vec<(f64, f64)> = way
            .coords
            .iter()
            .map(|(lat, lon)| (*lat as f64, *lon as f64))
            .collect();
        let group_id = routing.segment_groups.len() as u32;
        routing.segment_groups.push(RoadSegmentGroup {
            key: SegmentGroupKey::Osm { osm_id },
            coords: coords.clone(),
            tags: SegmentGroupTags {
                highway: way.highway.clone(),
                tracktype: way.tracktype.clone(),
                surface: way.surface.clone(),
                fourwd_only: way.fourwd_only,
            },
            role,
            reason: None,
        });
        lines.push(SupplementalLine {
            coords,
            highway: way.highway.clone(),
            tracktype: way.tracktype.clone(),
            role,
            group_id,
            gtlf_layer: None,
        });
    }
    if !lines.is_empty() {
        info!(
            supplemental_ways = lines.len(),
            "roads patch: welding driveable inventory ways"
        );
        merge_supplemental_jeep_lines(routing, &lines, sample_step_m);
    }
}

pub fn refresh_inventory_roles(inventory: &mut OsmRoadInventory, book: &RoadsBook) {
    for way in &mut inventory.ways {
        let Some(osm_id) = way.osm_id else {
            continue;
        };
        let role = book.classify_osm_way(
            osm_id,
            &way.highway,
            way.tracktype.as_deref(),
            way.surface.as_deref(),
            way.fourwd_only,
        );
        way.role = inventory_role_from_road(role);
        if way.role == InventoryRole::Other {
            way.reason = Some(way.highway.clone());
        }
    }
}

/// Reclassify edges from ``roads.yaml``, weld any newly driveable inventory ways, merge customs.
pub fn apply_roads_book_patch(
    routing: &mut OsmRouting,
    inventory: &mut OsmRoadInventory,
    book: &RoadsBook,
    sample_step_m: f64,
) {
    let t0 = Instant::now();
    let segment_groups = routing.segment_groups.len();
    let graph_nodes = routing.graph.node_count();
    let drive_customs = book
        .customs
        .iter()
        .filter(|c| matches!(c.role, RoadRole::Paved | RoadRole::Jeep))
        .count();
    info!(
        segment_groups,
        graph_nodes,
        drive_customs,
        overrides = book.overrides.len(),
        "roads patch: start"
    );
    strip_custom_drive_segments(routing);
    info!(
        segment_groups,
        elapsed_secs = t0.elapsed().as_secs_f64(),
        "roads patch: reclassify pass 1"
    );
    let mut paved_changed = reclassify_segment_groups(routing, book);
    info!(
        paved_changed,
        elapsed_secs = t0.elapsed().as_secs_f64(),
        "roads patch: copy roles onto edges"
    );
    apply_group_roles_to_edges(routing);
    weld_driveable_inventory_ways(routing, inventory, book, sample_step_m);
    info!(
        elapsed_secs = t0.elapsed().as_secs_f64(),
        "roads patch: reclassify pass 2"
    );
    paved_changed |= reclassify_segment_groups(routing, book);
    apply_group_roles_to_edges(routing);
    info!(
        paved_changed,
        elapsed_secs = t0.elapsed().as_secs_f64(),
        "roads patch: roles applied"
    );
    if drive_customs > 0 {
        info!(
            drive_customs,
            elapsed_secs = t0.elapsed().as_secs_f64(),
            "roads patch: merge roads.yaml custom drive segments"
        );
    }
    merge_road_customs_into_routing(routing, book, sample_step_m);
    let graph_nodes_after = routing.graph.node_count();
    if routing.graph_nodes.len() == 0 {
        info!(
            graph_nodes = graph_nodes_after,
            elapsed_secs = t0.elapsed().as_secs_f64(),
            "roads patch: rebuilding graph-node index"
        );
        let index_started = Instant::now();
        routing.graph_nodes = crate::osm::GraphNodeIndex::from_graph(&routing.graph);
        info!(
            graph_nodes = graph_nodes_after,
            elapsed_secs = index_started.elapsed().as_secs_f64(),
            "roads patch: graph-node index ready"
        );
    } else {
        info!(
            indexed = routing.graph_nodes.len(),
            graph_nodes = graph_nodes_after,
            "roads patch: graph-node index kept"
        );
    }
    if paved_changed || routing.paved.anchor_count() == 0 {
        rebuild_paved_anchors(routing, sample_step_m);
    } else {
        info!(
            paved_anchors = routing.paved.anchor_count(),
            "roads patch: paved anchors kept (no paved role changes)"
        );
    }
    info!(
        inventory_ways = inventory.ways.len(),
        elapsed_secs = t0.elapsed().as_secs_f64(),
        "roads patch: refresh inventory roles"
    );
    refresh_inventory_roles(inventory, book);
    info!(
        elapsed_secs = t0.elapsed().as_secs_f64(),
        "roads patch: done"
    );
}

/// Graph safe to write to ``routing-merged.bin`` (no custom polylines).
pub fn osm_routing_base_for_cache(routing: &OsmRouting) -> OsmRouting {
    let mut base = crate::osm::duplicate_osm_routing(routing);
    strip_custom_drive_segments(&mut base);
    base
}

/// True when only customs/overrides changed in ways that leave the trusted edge set unchanged.
pub fn patch_preserves_trusted_reach(prev_book: &RoadsBook, next_book: &RoadsBook) -> bool {
    use peaky_preset::{roads_global_fingerprint, roads_fingerprint};
    if roads_global_fingerprint(prev_book) != roads_global_fingerprint(next_book) {
        return false;
    }
    let prev_drive_customs: HashSet<_> = prev_book
        .customs
        .iter()
        .filter(|c| matches!(c.role, RoadRole::Paved | RoadRole::Jeep))
        .map(|c| c.id.as_str())
        .collect();
    let next_drive_customs: HashSet<_> = next_book
        .customs
        .iter()
        .filter(|c| matches!(c.role, RoadRole::Paved | RoadRole::Jeep))
        .map(|c| c.id.as_str())
        .collect();
    prev_drive_customs == next_drive_customs && roads_fingerprint(prev_book) == roads_fingerprint(next_book)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::osm::{JeepRoadGraph, JeepRoadIndex, GraphNodeIndex, PavedAnchorIndex};
    use crate::segment_groups::{SegmentGroupKey, SegmentGroupTags};
    use peaky_preset::seed_roads_book;

    fn routing_with_one_osm_group(role: RoadRole, osm_id: i64) -> OsmRouting {
        let mut graph = JeepRoadGraph::new();
        let a = graph.insert_node(38.0, -117.0);
        let b = graph.insert_node(38.001, -117.0);
        graph.add_way_segment_classified(
            a,
            b,
            38.0,
            -117.0,
            38.001,
            -117.0,
            "track",
            None,
            None,
            false,
            role,
            0,
        );
        OsmRouting {
            jeep_roads: JeepRoadIndex::from_samples(Vec::new(), 0),
            graph,
            paved: PavedAnchorIndex::from_points(Vec::new()),
            graph_nodes: GraphNodeIndex::from_graph(&JeepRoadGraph::new()),
            segment_groups: vec![RoadSegmentGroup {
                key: SegmentGroupKey::Osm { osm_id },
                coords: vec![(38.0, -117.0), (38.001, -117.0)],
                tags: SegmentGroupTags {
                    highway: "track".into(),
                    tracktype: None,
                    surface: None,
                    fourwd_only: false,
                },
                role,
                reason: None,
            }],
        }
    }

    #[test]
    fn override_flips_edge_role_without_reparse() {
        let book = seed_roads_book();
        let mut routing = routing_with_one_osm_group(RoadRole::Jeep, 42);
        routing.graph_nodes = GraphNodeIndex::from_graph(&routing.graph);
        let mut inv = OsmRoadInventory::default();
        apply_roads_book_patch(&mut routing, &mut inv, &book, 40.0);
        assert_eq!(routing.graph.edges(0)[0].role, RoadRole::Jeep);
    }

    #[test]
    fn patch_keeps_jeep_rtree_without_resample() {
        let book = seed_roads_book();
        let mut routing = routing_with_one_osm_group(RoadRole::Jeep, 42);
        routing.jeep_roads = JeepRoadIndex::from_samples(
            vec![
                crate::osm::RoadPoint {
                    lat: 38.0,
                    lon: -117.0,
                },
                crate::osm::RoadPoint {
                    lat: 38.0005,
                    lon: -117.0,
                },
            ],
            1,
        );
        let before = routing.jeep_roads.point_count;
        routing.graph_nodes = GraphNodeIndex::from_graph(&routing.graph);
        let mut inv = OsmRoadInventory::default();
        apply_roads_book_patch(&mut routing, &mut inv, &book, 40.0);
        assert_eq!(routing.jeep_roads.point_count, before);
    }
}
