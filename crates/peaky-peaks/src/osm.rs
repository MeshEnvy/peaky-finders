//! OSM jeep-class road index (Geofabrik PBF → sampled points → R-tree).

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::time::Instant;

use anyhow::{Context, Result};
use osmpbf::{Element, ElementReader};
use rstar::{RTree, RTreeObject, AABB};
use tracing::info;

use crate::hike::haversine_m;

pub const JEEP_HIGHWAY_TAGS: &[&str] = &[
    "track",
    "unclassified",
    "service",
    "residential",
    "tertiary",
];

const ROAD_SAMPLE_STEP_M: f64 = 40.0;
const GEOFABRIK_NV_URL: &str = "https://download.geofabrik.de/north-america/us/nevada-latest.osm.pbf";

#[derive(Debug, Clone, Copy)]
pub struct RoadPoint {
    pub lat: f64,
    pub lon: f64,
}

impl RTreeObject for RoadPoint {
    type Envelope = AABB<[f64; 2]>;

    fn envelope(&self) -> Self::Envelope {
        AABB::from_point([self.lon, self.lat])
    }
}

pub struct JeepRoadIndex {
    tree: RTree<RoadPoint>,
    pub point_count: usize,
    pub way_count: usize,
}

impl JeepRoadIndex {
    pub fn is_empty(&self) -> bool {
        self.point_count == 0
    }

    /// Nearest jeep-road sample within ``max_m`` (haversine).
    pub fn nearest_within(&self, lat: f64, lon: f64, max_m: f64) -> Option<(f64, f64, f64)> {
        if self.point_count == 0 {
            return None;
        }
        let deg = (max_m / 111_000.0).max(0.002);
        let envelope = AABB::from_corners([lon - deg, lat - deg], [lon + deg, lat + deg]);
        let mut best: Option<(f64, f64, f64)> = None;
        for pt in self.tree.locate_in_envelope_intersecting(&envelope) {
            let d = haversine_m(lat, lon, pt.lat, pt.lon);
            if d > max_m + 1.0 {
                continue;
            }
            if best.map(|(_, _, bd)| d < bd).unwrap_or(true) {
                best = Some((pt.lat, pt.lon, d));
            }
        }
        best
    }
}

pub fn osm_cache_path(project_dir: &Path) -> PathBuf {
    project_dir.join(".peaky/cache/osm/nevada-latest.osm.pbf")
}

pub fn ensure_osm_pbf(project_dir: &Path, force: bool) -> Result<PathBuf> {
    let path = osm_cache_path(project_dir);
    if path.is_file() && !force {
        info!(
            path = %path.display(),
            bytes = std::fs::metadata(&path).map(|m| m.len()).unwrap_or(0),
            "peaks: OSM cache hit"
        );
        return Ok(path);
    }
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).context("create osm cache dir")?;
    }
    info!(url = GEOFABRIK_NV_URL, "peaks: downloading OSM PBF…");
    let t0 = Instant::now();
    let client = reqwest::blocking::Client::builder()
        .timeout(std::time::Duration::from_secs(600))
        .build()
        .context("build HTTP client")?;
    let bytes = client
        .get(GEOFABRIK_NV_URL)
        .send()
        .context("download OSM PBF")?
        .error_for_status()
        .context("OSM PBF HTTP status")?
        .bytes()
        .context("read OSM PBF body")?;
    std::fs::write(&path, &bytes).context("write OSM PBF cache")?;
    info!(
        path = %path.display(),
        bytes = bytes.len(),
        elapsed_secs = t0.elapsed().as_secs_f64(),
        "peaks: OSM download done"
    );
    Ok(path)
}

fn is_jeep_highway<'a, I>(tags: I) -> bool
where
    I: IntoIterator<Item = (&'a str, &'a str)>,
{
    let mut highway: Option<&str> = None;
    let mut tracktype: Option<&str> = None;
    let mut surface: Option<&str> = None;
    let mut fourwd_only = false;
    for (k, v) in tags {
        match k {
            "highway" => highway = Some(v),
            "tracktype" => tracktype = Some(v),
            "surface" => surface = Some(v),
            "4wd_only" if v == "yes" => fourwd_only = true,
            _ => {}
        }
    }
    let Some(hw) = highway else {
        return false;
    };
    if !JEEP_HIGHWAY_TAGS.iter().any(|t| *t == hw) {
        return false;
    }
    if hw == "track" {
        if fourwd_only {
            return true;
        }
        if let Some(tt) = tracktype {
            if matches!(tt, "grade1" | "grade2") {
                return false;
            }
        }
        if surface == Some("paved") {
            return false;
        }
    }
    true
}

fn sample_way(nodes: &[(f64, f64)], step_m: f64) -> Vec<RoadPoint> {
    if nodes.len() < 2 {
        return nodes
            .iter()
            .map(|(lat, lon)| RoadPoint { lat: *lat, lon: *lon })
            .collect();
    }
    let mut out = Vec::new();
    for w in nodes.windows(2) {
        let (lat1, lon1) = w[0];
        let (lat2, lon2) = w[1];
        let seg_len = haversine_m(lat1, lon1, lat2, lon2);
        let n = ((seg_len / step_m).ceil() as usize).max(1);
        for i in 0..=n {
            let t = (i as f64) / (n as f64);
            out.push(RoadPoint {
                lat: lat1 + t * (lat2 - lat1),
                lon: lon1 + t * (lon2 - lon1),
            });
        }
    }
    out
}

fn node_in_bbox(lat: f64, lon: f64, west: f64, south: f64, east: f64, north: f64) -> bool {
    lon >= west && lon <= east && lat >= south && lat <= north
}

/// Parse PBF and build jeep-road index clipped to WGS84 bbox (with ~0.05° pad).
pub fn build_jeep_road_index(
    pbf_path: &Path,
    west: f64,
    south: f64,
    east: f64,
    north: f64,
) -> Result<JeepRoadIndex> {
    let pad = 0.05;
    let west = west - pad;
    let south = south - pad;
    let east = east + pad;
    let north = north + pad;

    info!(
        path = %pbf_path.display(),
        bbox = format!("{west:.4},{south:.4},{east:.4},{north:.4}"),
        "peaks: parsing OSM ways…"
    );
    let t0 = Instant::now();

    let mut nodes: HashMap<i64, (f64, f64)> = HashMap::new();
    ElementReader::from_path(pbf_path)
        .context("open OSM PBF")?
        .for_each(|element| {
            let (id, lat, lon) = match element {
                Element::Node(node) => (node.id(), node.lat(), node.lon()),
                Element::DenseNode(node) => (node.id(), node.lat(), node.lon()),
                _ => return,
            };
            if node_in_bbox(lat, lon, west, south, east, north) {
                nodes.insert(id, (lat, lon));
            }
        })?;
    info!(
        nodes = nodes.len(),
        elapsed_secs = t0.elapsed().as_secs_f64(),
        "peaks: OSM nodes loaded"
    );

    let mut samples: Vec<RoadPoint> = Vec::new();
    let mut way_count = 0usize;
    let mut highway_counts: HashMap<String, usize> = HashMap::new();

    ElementReader::from_path(pbf_path)
        .context("reopen OSM PBF")?
        .for_each(|element| {
            if let Element::Way(way) = element {
                if !is_jeep_highway(way.tags()) {
                    return;
                }
                let mut coords = Vec::new();
                for nid in way.refs() {
                    if let Some((lat, lon)) = nodes.get(&nid) {
                        coords.push((*lat, *lon));
                    }
                }
                if coords.len() < 2 {
                    return;
                }
                for (k, v) in way.tags() {
                    if k == "highway" {
                        *highway_counts.entry(v.to_string()).or_default() += 1;
                    }
                }
                samples.extend(sample_way(&coords, ROAD_SAMPLE_STEP_M));
                way_count += 1;
            }
        })?;

    let point_count = samples.len();
    let tree = RTree::bulk_load(samples);
    info!(
        ways = way_count,
        points = point_count,
        highways = ?highway_counts,
        elapsed_secs = t0.elapsed().as_secs_f64(),
        "peaks: jeep road index ready"
    );

    Ok(JeepRoadIndex {
        tree,
        point_count,
        way_count,
    })
}

#[cfg(test)]
pub(crate) fn test_index_from_points(points: &[(f64, f64)]) -> JeepRoadIndex {
    let samples: Vec<RoadPoint> = points
        .iter()
        .map(|(lat, lon)| RoadPoint {
            lat: *lat,
            lon: *lon,
        })
        .collect();
    let n = samples.len();
    JeepRoadIndex {
        tree: RTree::bulk_load(samples),
        point_count: n,
        way_count: n.max(1),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn jeep_highway_accepts_track_and_rejects_footway() {
        assert!(is_jeep_highway([("highway", "track")]));
        assert!(is_jeep_highway([("highway", "service")]));
        assert!(!is_jeep_highway([("highway", "footway")]));
        assert!(!is_jeep_highway([("highway", "motorway")]));
    }

    #[test]
    fn nearest_within_finds_road_sample() {
        let idx = test_index_from_points(&[(38.0, -117.0), (38.001, -117.0)]);
        let hit = idx.nearest_within(38.0005, -117.0, 500.0);
        assert!(hit.is_some());
        let (_, _, d) = hit.unwrap();
        assert!(d < 500.0);
    }

    #[test]
    fn road_at_six_tenths_mile_is_beyond_half_mile_cap() {
        use crate::hike::{haversine_m, DEFAULT_MAX_HIKE_M};
        let idx = test_index_from_points(&[(38.0, -117.0)]);
        let peak_lat = 38.0087;
        let peak_lon = -117.0;
        let dist = haversine_m(38.0, -117.0, peak_lat, peak_lon);
        assert!(dist > DEFAULT_MAX_HIKE_M);
        assert!(idx.nearest_within(peak_lat, peak_lon, DEFAULT_MAX_HIKE_M).is_none());
    }

    #[test]
    fn distant_peak_has_no_road_within_half_mile() {
        use crate::hike::DEFAULT_MAX_HIKE_M;
        let idx = test_index_from_points(&[(38.0, -117.0)]);
        assert!(idx.nearest_within(39.0, -117.0, DEFAULT_MAX_HIKE_M).is_none());
    }

    #[test]
    fn eip_like_site_at_road_passes_road_gate() {
        use crate::hike::{profile_hike, profile_passes, DEFAULT_MAX_HIKE_M, HikeSampleElev};

        struct FlatElev(f64);
        impl HikeSampleElev for FlatElev {
            fn sample_elev_m(&self, _lat: f64, _lon: f64) -> f64 {
                self.0
            }
        }

        let idx = test_index_from_points(&[(38.0, -117.0)]);
        let peak_lat = 38.0003;
        let peak_lon = -117.0;
        let road = idx.nearest_within(peak_lat, peak_lon, DEFAULT_MAX_HIKE_M);
        assert!(road.is_some());
        let (rlat, rlon, road_m) = road.unwrap();
        assert!(road_m < 100.0);
        let flat = FlatElev(2000.0);
        let profile = profile_hike(&flat, rlat, rlon, peak_lat, peak_lon, 30.0).unwrap();
        assert!(profile_passes(&profile, DEFAULT_MAX_HIKE_M, 45.0));
    }
}
