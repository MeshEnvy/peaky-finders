//! Snap a later road layer onto an earlier one and replace only the coincident stretch.

use std::collections::HashMap;
use std::time::Instant;

use peaky_preset::RoadRole;
use tracing::info;

use crate::roads_pipeline::RoadSegment;

const SNAP_M: f64 = 18.0;
const MIN_REPLACE_M: f64 = 36.0;
const CELL_M: f64 = 18.0;
const CELL_RADIUS: i32 = 2;

pub fn merge_layer(base: &mut Vec<RoadSegment>, layer: Vec<RoadSegment>, source: &str) {
    let incoming_count = layer.len();
    let before = base.len();
    let t0 = Instant::now();
    if layer.is_empty() {
        info!(source, incoming = 0, "roads merge: empty layer");
        return;
    }
    if base.is_empty() {
        base.extend(layer);
        info!(
            source,
            incoming = incoming_count,
            segments = base.len(),
            elapsed_secs = t0.elapsed().as_secs_f64(),
            "roads merge: took layer as the first graph"
        );
        return;
    }

    let mut alive = vec![true; base.len()];
    let mut index = SegIndex::new();
    let mut scratch = Vec::new();
    for (id, seg) in base.iter().enumerate() {
        index.insert(id as u32, &seg.coords, &mut scratch);
    }
    let mut stamp = vec![0u32; base.len()];
    let mut generation = 0u32;
    let mut replaced = 0usize;

    for (n, incoming) in layer.into_iter().enumerate() {
        if incoming.coords.len() < 2 {
            continue;
        }
        if n > 0 && n % 5_000 == 0 {
            info!(
                source,
                step = format!("[{n}/{incoming_count}]"),
                segments = base.len(),
                replaced,
                "roads merge: progress"
            );
        }
        let samples = densify(&incoming.coords, CELL_M / 2.0);
        let points = PointGrid::build(&samples);
        let hits = index.query(&incoming.coords, &mut stamp, &mut generation);
        let mut kept = Vec::new();
        for idx in hits {
            let idx = idx as usize;
            if !alive.get(idx).copied().unwrap_or(false) {
                continue;
            }
            match trim(&base[idx].coords, &points) {
                Trim::Unchanged => {}
                Trim::Remove => {
                    alive[idx] = false;
                    replaced += 1;
                }
                Trim::Split(pieces) => {
                    alive[idx] = false;
                    replaced += 1;
                    for coords in pieces {
                        if coords.len() >= 2 {
                            let mut seg = base[idx].clone();
                            seg.coords = coords;
                            kept.push(seg);
                        }
                    }
                }
            }
        }
        for seg in kept {
            push_segment(base, &mut alive, &mut stamp, &mut index, &mut scratch, seg);
        }
        push_segment(
            base,
            &mut alive,
            &mut stamp,
            &mut index,
            &mut scratch,
            incoming,
        );
    }

    compact(base, &alive);
    info!(
        source,
        incoming = incoming_count,
        before,
        after = base.len(),
        replaced,
        elapsed_secs = t0.elapsed().as_secs_f64(),
        "roads merge: layer done"
    );
}

fn push_segment(
    base: &mut Vec<RoadSegment>,
    alive: &mut Vec<bool>,
    stamp: &mut Vec<u32>,
    index: &mut SegIndex,
    scratch: &mut Vec<(i32, i32)>,
    seg: RoadSegment,
) {
    let id = base.len() as u32;
    index.insert(id, &seg.coords, scratch);
    base.push(seg);
    alive.push(true);
    if stamp.len() < base.len() {
        stamp.resize(base.len(), 0);
    }
}

fn compact(base: &mut Vec<RoadSegment>, alive: &[bool]) {
    let mut write = 0usize;
    for read in 0..base.len() {
        if alive.get(read).copied().unwrap_or(false) {
            if write != read {
                base.swap(write, read);
            }
            write += 1;
        }
    }
    base.truncate(write);
}

enum Trim {
    Unchanged,
    Remove,
    Split(Vec<Vec<(f64, f64)>>),
}

fn trim(base: &[(f64, f64)], incoming: &PointGrid) -> Trim {
    if base.len() < 2 || !bboxes_near(base, incoming) {
        return Trim::Unchanged;
    }
    let mask: Vec<bool> = base
        .iter()
        .map(|&(lat, lon)| incoming.covers(lat, lon))
        .collect();
    let runs = significant_runs(base, &mask);
    if runs.is_empty() {
        return Trim::Unchanged;
    }
    let pieces = keep_outside(base, &runs);
    if pieces.is_empty() {
        Trim::Remove
    } else {
        Trim::Split(pieces)
    }
}

fn significant_runs(coords: &[(f64, f64)], mask: &[bool]) -> Vec<(usize, usize)> {
    let mut runs = Vec::new();
    let mut i = 0usize;
    while i < mask.len() {
        if !mask[i] {
            i += 1;
            continue;
        }
        let start = i;
        while i < mask.len() && mask[i] {
            i += 1;
        }
        let end = i - 1;
        if end > start && path_len(coords, start, end) >= MIN_REPLACE_M {
            runs.push((start, end));
        }
    }
    runs
}

fn path_len(coords: &[(f64, f64)], start: usize, end: usize) -> f64 {
    let mut total = 0.0;
    for i in start..end {
        total += haversine_m(coords[i].0, coords[i].1, coords[i + 1].0, coords[i + 1].1);
    }
    total
}

fn keep_outside(coords: &[(f64, f64)], runs: &[(usize, usize)]) -> Vec<Vec<(f64, f64)>> {
    let mut pieces = Vec::new();
    let mut cursor = 0usize;
    for &(start, end) in runs {
        if start > cursor {
            let cut = start + 1;
            if cut - cursor >= 2 {
                pieces.push(coords[cursor..cut].to_vec());
            }
        }
        cursor = end;
    }
    if coords.len() - cursor >= 2 {
        pieces.push(coords[cursor..].to_vec());
    }
    pieces
}

struct SegIndex {
    cells: HashMap<(i32, i32), Vec<u32>>,
}

impl SegIndex {
    fn new() -> Self {
        Self {
            cells: HashMap::new(),
        }
    }

    fn insert(&mut self, id: u32, coords: &[(f64, f64)], scratch: &mut Vec<(i32, i32)>) {
        scratch.clear();
        push_line_cells(coords, scratch);
        scratch.sort_unstable();
        scratch.dedup();
        for &cell in scratch.iter() {
            self.cells.entry(cell).or_default().push(id);
        }
    }

    fn query(&self, coords: &[(f64, f64)], stamp: &mut Vec<u32>, generation: &mut u32) -> Vec<u32> {
        *generation = generation.wrapping_add(1);
        if *generation == 0 {
            stamp.fill(0);
            *generation = 1;
        }
        let gen = *generation;
        let mut cells = Vec::new();
        push_line_cells(coords, &mut cells);
        let mut hits = Vec::new();
        for (cx, cy) in cells {
            for dx in -1..=1 {
                for dy in -1..=1 {
                    let Some(ids) = self.cells.get(&(cx + dx, cy + dy)) else {
                        continue;
                    };
                    for &id in ids {
                        let slot = id as usize;
                        if slot >= stamp.len() {
                            stamp.resize(slot + 1, 0);
                        }
                        if stamp[slot] != gen {
                            stamp[slot] = gen;
                            hits.push(id);
                        }
                    }
                }
            }
        }
        hits
    }
}

struct PointGrid {
    cells: HashMap<(i32, i32), Vec<(f64, f64)>>,
    west: f64,
    south: f64,
    east: f64,
    north: f64,
}

impl PointGrid {
    fn build(samples: &[(f64, f64)]) -> Self {
        let mut cells: HashMap<(i32, i32), Vec<(f64, f64)>> = HashMap::new();
        let mut west = f64::INFINITY;
        let mut south = f64::INFINITY;
        let mut east = f64::NEG_INFINITY;
        let mut north = f64::NEG_INFINITY;
        for &(lat, lon) in samples {
            west = west.min(lon);
            east = east.max(lon);
            south = south.min(lat);
            north = north.max(lat);
            cells.entry(cell_of(lat, lon)).or_default().push((lat, lon));
        }
        Self {
            cells,
            west,
            south,
            east,
            north,
        }
    }

    fn covers(&self, lat: f64, lon: f64) -> bool {
        let (cx, cy) = cell_of(lat, lon);
        for dx in -CELL_RADIUS..=CELL_RADIUS {
            for dy in -CELL_RADIUS..=CELL_RADIUS {
                let Some(points) = self.cells.get(&(cx + dx, cy + dy)) else {
                    continue;
                };
                for &(plat, plon) in points {
                    if haversine_m(lat, lon, plat, plon) <= SNAP_M {
                        return true;
                    }
                }
            }
        }
        false
    }
}

fn bboxes_near(base: &[(f64, f64)], incoming: &PointGrid) -> bool {
    const PAD: f64 = 0.0004;
    let mut west = f64::INFINITY;
    let mut south = f64::INFINITY;
    let mut east = f64::NEG_INFINITY;
    let mut north = f64::NEG_INFINITY;
    for &(lat, lon) in base {
        west = west.min(lon);
        east = east.max(lon);
        south = south.min(lat);
        north = north.max(lat);
    }
    !(east + PAD < incoming.west
        || incoming.east + PAD < west
        || north + PAD < incoming.south
        || incoming.north + PAD < south)
}

fn densify(coords: &[(f64, f64)], step_m: f64) -> Vec<(f64, f64)> {
    let mut out = Vec::new();
    let Some(&first) = coords.first() else {
        return out;
    };
    out.push(first);
    for w in coords.windows(2) {
        let dist = haversine_m(w[0].0, w[0].1, w[1].0, w[1].1);
        let steps = (dist / step_m).ceil().max(1.0) as usize;
        for step in 1..=steps {
            let t = step as f64 / steps as f64;
            out.push((
                w[0].0 + t * (w[1].0 - w[0].0),
                w[0].1 + t * (w[1].1 - w[0].1),
            ));
        }
    }
    out
}

fn push_line_cells(coords: &[(f64, f64)], out: &mut Vec<(i32, i32)>) {
    for w in coords.windows(2) {
        let dist = haversine_m(w[0].0, w[0].1, w[1].0, w[1].1);
        let steps = (dist / CELL_M).ceil().max(1.0) as usize;
        for step in 0..=steps {
            let t = step as f64 / steps as f64;
            let lat = w[0].0 + t * (w[1].0 - w[0].0);
            let lon = w[0].1 + t * (w[1].1 - w[0].1);
            out.push(cell_of(lat, lon));
        }
    }
}

fn cell_of(lat: f64, lon: f64) -> (i32, i32) {
    let (x, y) = xy(lat, lon);
    ((x / CELL_M).floor() as i32, (y / CELL_M).floor() as i32)
}

fn xy(lat: f64, lon: f64) -> (f64, f64) {
    let cos = lat.to_radians().cos().abs().max(0.2);
    (lon * 111_320.0 * cos, lat * 111_320.0)
}

fn haversine_m(lat1: f64, lon1: f64, lat2: f64, lon2: f64) -> f64 {
    const R: f64 = 6_371_000.0;
    let dlat = (lat2 - lat1).to_radians();
    let dlon = (lon2 - lon1).to_radians();
    let a = (dlat / 2.0).sin().powi(2)
        + lat1.to_radians().cos() * lat2.to_radians().cos() * (dlon / 2.0).sin().powi(2);
    R * 2.0 * a.sqrt().asin()
}

fn segment(id: &str, coords: Vec<(f64, f64)>) -> RoadSegment {
    RoadSegment {
        id: id.to_string(),
        kind: "track".into(),
        surface: "unpaved".into(),
        grade: "unknown".into(),
        access: "unknown".into(),
        role: RoadRole::Jeep,
        coords,
    }
}

/// About 100 m of longitude at latitude 39.5.
fn lon_step() -> f64 {
    100.0 / (111_320.0 * 39.5_f64.to_radians().cos())
}

fn east_line(lat: f64, lon0: f64, points: usize) -> Vec<(f64, f64)> {
    let step = lon_step();
    (0..points).map(|i| (lat, lon0 + i as f64 * step)).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn later_layer_replaces_only_the_overlapping_stretch() {
        let lat = 39.5;
        let lon0 = -119.8;
        let earlier = east_line(lat, lon0, 10);
        let overlap_lat = lat + 10.0 / 111_320.0;
        let later = east_line(overlap_lat, lon0 + 3.0 * lon_step(), 5);
        let mut base = vec![segment("osm:1", earlier)];
        merge_layer(&mut base, vec![segment("blm:1", later.clone())], "blm");
        let old: Vec<_> = base.iter().filter(|s| s.id == "osm:1").collect();
        let new: Vec<_> = base.iter().filter(|s| s.id == "blm:1").collect();
        assert_eq!(new.len(), 1);
        assert!(old.len() >= 2, "ends of the earlier road should remain");
        let overlap_west = later.first().unwrap().1;
        let overlap_east = later.last().unwrap().1;
        for seg in &old {
            let west = seg.coords.iter().map(|p| p.1).fold(f64::INFINITY, f64::min);
            let east = seg
                .coords
                .iter()
                .map(|p| p.1)
                .fold(f64::NEG_INFINITY, f64::max);
            let inside = west > overlap_west && east < overlap_east;
            assert!(!inside, "kept stretch sits inside the replacement");
        }
    }

    #[test]
    fn a_crossing_does_not_delete_the_earlier_road() {
        let lat = 39.5;
        let lon0 = -119.8;
        let earlier = east_line(lat, lon0, 8);
        let cross_lon = earlier[1].1;
        let cross = vec![(lat - 0.002, cross_lon), (lat + 0.002, cross_lon)];
        let mut base = vec![segment("osm:1", earlier.clone())];
        merge_layer(&mut base, vec![segment("blm:cross", cross)], "blm");
        let old: Vec<_> = base.iter().filter(|s| s.id == "osm:1").collect();
        assert_eq!(old.len(), 1);
        assert_eq!(old[0].coords.len(), earlier.len());
    }
}
