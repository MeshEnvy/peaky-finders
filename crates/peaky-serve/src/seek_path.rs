//! Forward progress reachability for goal seek.
//!
//! When some candidate of *this hop* already has an RF path to the goal
//! (preset sites ∪ scan peaks, each hop closer to the goal), drop the rest
//! as dead ends. If none do, the corridor is unfinished — keep RF-viable
//! first hops instead of zeroing the scan.

use std::collections::HashSet;

use peaky_preset::Preset;
use splatter::Session;

use crate::rf::{default_repeater_tx_height_m, resolved_site_tx_height_m};
use crate::seek_rank::haversine_m;

pub const SEEK_PROGRESS_MARGIN_M: f64 = 1.0;

const RF_CHUNK: usize = 512;

/// True when at least one candidate of this hop is on an RF path to the goal.
/// Distant sites that can reach the goal on their own do not count — only
/// this hop's peaks and site candidates. If none reach, skip dead-end prune.
pub fn current_hop_reaches_goal(
    peak_completes: &[bool],
    reachable_peak_indices: &HashSet<usize>,
    site_slugs: &[String],
    site_completes: &[bool],
    reachable_site_slugs: &HashSet<String>,
) -> bool {
    peak_completes
        .iter()
        .enumerate()
        .any(|(i, completes)| *completes || reachable_peak_indices.contains(&i))
        || site_slugs.iter().enumerate().any(|(i, slug)| {
            site_completes.get(i).copied().unwrap_or(false) || reachable_site_slugs.contains(slug)
        })
}

/// True when `to` is within hop of `from` and strictly closer to the goal than `from`.
pub fn hop_makes_goal_progress(
    from_lat: f64,
    from_lon: f64,
    goal_lat: f64,
    goal_lon: f64,
    to_lat: f64,
    to_lon: f64,
    hop_m: f64,
) -> bool {
    let hop = haversine_m(from_lat, from_lon, to_lat, to_lon);
    if hop <= 1.0 || hop > hop_m + 1.0 {
        return false;
    }
    let from_goal = haversine_m(from_lat, from_lon, goal_lat, goal_lon);
    let to_goal = haversine_m(to_lat, to_lon, goal_lat, goal_lon);
    to_goal < from_goal - SEEK_PROGRESS_MARGIN_M
}

enum PathNodeKind {
    Goal,
    Site(String),
    Peak(usize),
}

struct PathNode {
    kind: PathNodeKind,
    lat: f64,
    lon: f64,
    tx_h: f64,
    goal_dist_m: f64,
}

pub struct ForwardPathReachable {
    pub reachable_site_slugs: HashSet<String>,
    pub reachable_peak_indices: HashSet<usize>,
}

/// DP reachability given a directed RF oracle (`from` → `to` indices).
#[cfg_attr(not(test), allow(dead_code))]
pub fn forward_reachable_with_oracle(
    goal_dist_m: &[f64],
    goal_idx: usize,
    hop_m: f64,
    lat_lon: &[(f64, f64)],
    rf_viable: impl Fn(usize, usize) -> bool,
) -> HashSet<usize> {
    let n = goal_dist_m.len();
    if n == 0 {
        return HashSet::new();
    }
    let mut order: Vec<usize> = (0..n).collect();
    order.sort_by(|&a, &b| {
        goal_dist_m[a]
            .partial_cmp(&goal_dist_m[b])
            .unwrap_or(std::cmp::Ordering::Equal)
    });

    let forward_neighbors = forward_neighbor_lists(n, goal_dist_m, lat_lon, hop_m);
    let mut reachable = HashSet::new();
    reachable.insert(goal_idx);

    for &i in &order {
        if i == goal_idx {
            continue;
        }
        if goal_dist_m[i] <= hop_m + 1.0 && rf_viable(i, goal_idx) {
            reachable.insert(i);
            continue;
        }
        for &j in &forward_neighbors[i] {
            if reachable.contains(&j) && rf_viable(i, j) {
                reachable.insert(i);
                break;
            }
        }
    }
    reachable
}

fn forward_neighbor_lists(
    n: usize,
    goal_dist_m: &[f64],
    lat_lon: &[(f64, f64)],
    hop_m: f64,
) -> Vec<Vec<usize>> {
    let mut out = vec![Vec::new(); n];
    for i in 0..n {
        for j in 0..n {
            if i == j {
                continue;
            }
            if goal_dist_m[j] >= goal_dist_m[i] - SEEK_PROGRESS_MARGIN_M {
                continue;
            }
            let (ilat, ilon) = lat_lon[i];
            let (jlat, jlon) = lat_lon[j];
            if haversine_m(ilat, ilon, jlat, jlon) > hop_m + 1.0 {
                continue;
            }
            out[i].push(j);
        }
    }
    out
}

pub fn compute_forward_goal_reachable(
    session: &Session,
    preset: &Preset,
    from_lat: f64,
    from_lon: f64,
    goal_lat: f64,
    goal_lon: f64,
    goal_tx_h: f64,
    hop_m: f64,
    rf_json: &str,
    peak_candidates: &[(f64, f64, f64)],
    exclude_slugs: &HashSet<String>,
    ensure_active: &mut impl FnMut() -> Result<(), String>,
) -> Result<ForwardPathReachable, String> {
    let from_goal = haversine_m(from_lat, from_lon, goal_lat, goal_lon);
    let peak_tx_h = default_repeater_tx_height_m(preset);

    let mut nodes: Vec<PathNode> = Vec::new();
    nodes.push(PathNode {
        kind: PathNodeKind::Goal,
        lat: goal_lat,
        lon: goal_lon,
        tx_h: goal_tx_h,
        goal_dist_m: 0.0,
    });
    let goal_idx = 0;

    for (slug, site) in &preset.sites {
        if exclude_slugs.contains(slug) {
            continue;
        }
        let lat = site.loc[0];
        let lon = site.loc[1];
        let site_goal = haversine_m(lat, lon, goal_lat, goal_lon);
        if site_goal >= from_goal - SEEK_PROGRESS_MARGIN_M {
            continue;
        }
        let tx_h = resolved_site_tx_height_m(preset, site);
        nodes.push(PathNode {
            kind: PathNodeKind::Site(slug.clone()),
            lat,
            lon,
            tx_h,
            goal_dist_m: site_goal,
        });
    }

    for (pi, &(lon, lat, _elev)) in peak_candidates.iter().enumerate() {
        let peak_goal = haversine_m(lat, lon, goal_lat, goal_lon);
        nodes.push(PathNode {
            kind: PathNodeKind::Peak(pi),
            lat,
            lon,
            tx_h: peak_tx_h,
            goal_dist_m: peak_goal,
        });
    }

    if nodes.len() <= 1 {
        return Ok(ForwardPathReachable {
            reachable_site_slugs: HashSet::new(),
            reachable_peak_indices: HashSet::new(),
        });
    }

    let lat_lon: Vec<(f64, f64)> = nodes.iter().map(|n| (n.lat, n.lon)).collect();
    let goal_dist_m: Vec<f64> = nodes.iter().map(|n| n.goal_dist_m).collect();
    let forward_neighbors = forward_neighbor_lists(nodes.len(), &goal_dist_m, &lat_lon, hop_m);

    let points: Vec<(f64, f64)> = lat_lon.clone();
    session
        .ensure_tiles_for_points(&points, hop_m)
        .map_err(|e| e.to_string())?;

    let mut order: Vec<usize> = (0..nodes.len()).collect();
    order.sort_by(|&a, &b| {
        goal_dist_m[a]
            .partial_cmp(&goal_dist_m[b])
            .unwrap_or(std::cmp::Ordering::Equal)
    });

    let mut reachable = HashSet::new();
    reachable.insert(goal_idx);

    for &i in &order {
        if i == goal_idx {
            continue;
        }
        let node = &nodes[i];
        let mut batch_eps: Vec<(f64, f64, f64)> = Vec::new();

        if goal_dist_m[i] <= hop_m + 1.0 {
            batch_eps.push((goal_lat, goal_lon, goal_tx_h));
        }
        for &j in &forward_neighbors[i] {
            if reachable.contains(&j) {
                let nb = &nodes[j];
                batch_eps.push((nb.lat, nb.lon, nb.tx_h));
            }
        }

        if batch_eps.is_empty() {
            continue;
        }

        let mut linked = false;
        for chunk_start in (0..batch_eps.len()).step_by(RF_CHUNK) {
            ensure_active()?;
            let chunk_end = (chunk_start + RF_CHUNK).min(batch_eps.len());
            let chunk = &batch_eps[chunk_start..chunk_end];
            let flags = session
                .seek_repeater_link_batch(node.lat, node.lon, node.tx_h, chunk, rf_json)
                .map_err(|e| e.to_string())?;
            for ok in flags {
                if ok {
                    linked = true;
                    break;
                }
            }
            if linked {
                break;
            }
        }
        if linked {
            reachable.insert(i);
        }
    }

    let mut reachable_site_slugs = HashSet::new();
    let mut reachable_peak_indices = HashSet::new();
    for &idx in &reachable {
        match &nodes[idx].kind {
            PathNodeKind::Goal => {}
            PathNodeKind::Site(slug) => {
                reachable_site_slugs.insert(slug.clone());
            }
            PathNodeKind::Peak(pi) => {
                reachable_peak_indices.insert(*pi);
            }
        }
    }

    Ok(ForwardPathReachable {
        reachable_site_slugs,
        reachable_peak_indices,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn line_nodes() -> (Vec<f64>, Vec<(f64, f64)>, usize) {
        // Goal at 0, nodes at increasing distance from goal along a line.
        let lat_lon = vec![
            (39.0, -117.0), // goal idx 0
            (39.01, -117.0),
            (39.02, -117.0),
            (39.03, -117.0),
        ];
        let goal_dist_m: Vec<f64> = lat_lon
            .iter()
            .map(|(lat, lon)| haversine_m(*lat, *lon, lat_lon[0].0, lat_lon[0].1))
            .collect();
        (goal_dist_m, lat_lon, 0)
    }

    #[test]
    fn oracle_chain_reaches_goal() {
        let (goal_dist_m, lat_lon, goal_idx) = line_nodes();
        let hop_m = 50_000.0;
        let edges: HashSet<(usize, usize)> = [(1, 0), (2, 1), (3, 2)].into_iter().collect();
        let rf = |from: usize, to: usize| edges.contains(&(from, to));
        let reachable = forward_reachable_with_oracle(&goal_dist_m, goal_idx, hop_m, &lat_lon, rf);
        assert!(reachable.contains(&0));
        assert!(reachable.contains(&1));
        assert!(reachable.contains(&2));
        assert!(reachable.contains(&3));
    }

    #[test]
    fn oracle_dead_end_branch_excluded() {
        let (goal_dist_m, lat_lon, goal_idx) = line_nodes();
        let hop_m = 50_000.0;
        // 3 → 2 → 1 but nothing reaches goal.
        let edges: HashSet<(usize, usize)> = [(3, 2), (2, 1)].into_iter().collect();
        let rf = |from: usize, to: usize| edges.contains(&(from, to));
        let reachable = forward_reachable_with_oracle(&goal_dist_m, goal_idx, hop_m, &lat_lon, rf);
        assert!(reachable.contains(&0));
        assert!(!reachable.contains(&1));
        assert!(!reachable.contains(&2));
        assert!(!reachable.contains(&3));
    }

    #[test]
    fn oracle_direct_completer() {
        let (goal_dist_m, lat_lon, goal_idx) = line_nodes();
        let hop_m = 500_000.0;
        let edges: HashSet<(usize, usize)> = [(3, 0)].into_iter().collect();
        let rf = |from: usize, to: usize| edges.contains(&(from, to));
        let reachable = forward_reachable_with_oracle(&goal_dist_m, goal_idx, hop_m, &lat_lon, rf);
        assert!(reachable.contains(&3));
    }

    #[test]
    fn hop_without_goal_path_skips_dead_end_prune() {
        let reachable_peaks = HashSet::new();
        let reachable_sites = HashSet::new();
        assert!(!current_hop_reaches_goal(
            &[false, false],
            &reachable_peaks,
            &["ridge".into()],
            &[false],
            &reachable_sites,
        ));
    }

    #[test]
    fn distant_goal_site_does_not_count_as_this_hop() {
        let reachable_peaks = HashSet::new();
        let reachable_sites: HashSet<String> = ["potosi-north-1".into()].into_iter().collect();
        assert!(!current_hop_reaches_goal(
            &[false, false],
            &reachable_peaks,
            &["tonopah-next".into()],
            &[false],
            &reachable_sites,
        ));
    }

    #[test]
    fn hop_peak_on_goal_path_enables_prune() {
        let reachable_peaks: HashSet<usize> = [1].into_iter().collect();
        let reachable_sites = HashSet::new();
        assert!(current_hop_reaches_goal(
            &[false, false],
            &reachable_peaks,
            &[],
            &[],
            &reachable_sites,
        ));
    }

    #[test]
    fn hop_makes_goal_progress_requires_closer_to_goal() {
        let from = (39.0, -118.0);
        let goal = (39.0, -117.0);
        let backward = (39.0, -118.5);
        assert!(hop_makes_goal_progress(
            from.0, from.1, goal.0, goal.1, goal.0, goal.1, 100_000.0
        ));
        assert!(!hop_makes_goal_progress(
            from.0, from.1, goal.0, goal.1, backward.0, backward.1, 100_000.0
        ));
    }
}
