//! Access difficulty labels (easy / medium / difficult / extreme).

use crate::model::{PeakCatalogEntry, PeakHikeProfile, PeakJeepProfile, PeakJeepRoadSegment};

pub fn difficulty_rank(label: &str) -> u8 {
    match label {
        "extreme" => 3,
        "difficult" => 2,
        "medium" => 1,
        _ => 0,
    }
}

pub fn difficulty_label(rank: u8) -> &'static str {
    match rank {
        3 => "extreme",
        2 => "difficult",
        1 => "medium",
        _ => "easy",
    }
}

/// Worse of two labels (hike vs jeep for pin color).
pub fn worse_difficulty(a: &str, b: &str) -> &'static str {
    difficulty_label(difficulty_rank(a).max(difficulty_rank(b)))
}

/// Razorback-class: hours of sustained steep climbing.
pub const HIKE_EXTREME_MIN_GAIN_M: f64 = 250.0;
pub const HIKE_EXTREME_MIN_AVG_GRADE_PCT: f64 = 18.0;

/// Goldfield-class: real scramble, not a roadside bump.
pub const HIKE_DIFFICULT_MIN_GAIN_M: f64 = 80.0;
pub const HIKE_DIFFICULT_MIN_AVG_GRADE_PCT: f64 = 15.0;

/// Sustained effort or scrambling on a short pad.
pub const HIKE_MEDIUM_MIN_MAX_GRADE_PCT: f64 = 15.0;
pub const HIKE_MEDIUM_MIN_AVG_GRADE_PCT: f64 = 8.0;
pub const HIKE_MEDIUM_MIN_GAIN_M: f64 = 40.0;

/// Hike difficulty from gain + sustained avg grade. Max grade only signals scrambling (medium).
pub fn hike_difficulty(
    max_grade_pct: f64,
    avg_grade_pct: f64,
    _horiz_m: f64,
    gain_m: f64,
) -> &'static str {
    if gain_m >= HIKE_EXTREME_MIN_GAIN_M && avg_grade_pct >= HIKE_EXTREME_MIN_AVG_GRADE_PCT {
        "extreme"
    } else if gain_m >= HIKE_DIFFICULT_MIN_GAIN_M && avg_grade_pct >= HIKE_DIFFICULT_MIN_AVG_GRADE_PCT
    {
        "difficult"
    } else if max_grade_pct >= HIKE_MEDIUM_MIN_MAX_GRADE_PCT
        || avg_grade_pct >= HIKE_MEDIUM_MIN_AVG_GRADE_PCT
        || gain_m >= HIKE_MEDIUM_MIN_GAIN_M
    {
        "medium"
    } else {
        "easy"
    }
}

/// OSM track/highway class → rank. Grade spikes are ignored; surface class is the signal.
///
/// `grade4` is a rough jeep road (difficult), not a no-go. Only `grade5` is extreme.
pub fn jeep_road_rank(highway: &str, tracktype: Option<&str>) -> u8 {
    match highway {
        "motorway" | "trunk" | "primary" | "secondary" | "tertiary" => 0,
        "residential" | "unclassified" => match tracktype {
            Some("grade5") => 3,
            Some("grade4") | Some("grade3") => 2,
            Some("grade2") => 1,
            Some("grade1") => 0,
            _ => 1,
        },
        "service" => 1,
        "track" => match tracktype {
            Some("grade5") => 3,
            Some("grade4") | Some("grade3") => 2,
            Some("grade2") | Some("grade1") => 1,
            _ => 1,
        },
        _ => 1,
    }
}

/// OSM ways are often 10–50 m. Merge consecutive same class before judging a stretch.
fn coalesce_jeep_segments(segments: &[PeakJeepRoadSegment]) -> Vec<PeakJeepRoadSegment> {
    let mut out: Vec<PeakJeepRoadSegment> = Vec::new();
    for s in segments {
        if s.dist_m <= 0.0 {
            continue;
        }
        if let Some(last) = out.last_mut() {
            if last.highway == s.highway && last.tracktype == s.tracktype {
                last.dist_m += s.dist_m;
                continue;
            }
        }
        out.push(s.clone());
    }
    out
}

fn qualifying_jeep_segments(segments: &[PeakJeepRoadSegment]) -> Vec<PeakJeepRoadSegment> {
    let coalesced = coalesce_jeep_segments(segments);
    let total: f64 = coalesced.iter().map(|s| s.dist_m.max(0.0)).sum();
    let min_len = (total * 0.02).max(40.0);
    coalesced
        .into_iter()
        .filter(|s| total < 80.0 || s.dist_m >= min_len)
        .collect()
}

/// Worst road class among stretches that are at least 2% of the route (40 m floor).
pub fn jeep_difficulty(segments: &[PeakJeepRoadSegment]) -> &'static str {
    let rank = qualifying_jeep_segments(segments)
        .iter()
        .map(|s| jeep_road_rank(&s.highway, s.tracktype.as_deref()))
        .max()
        .unwrap_or(0);
    difficulty_label(rank)
}

/// OSM class that set the jeep badge (worst qualifying highway + tracktype).
#[derive(Debug, Clone, PartialEq)]
pub struct JeepClassReason {
    pub highway: String,
    pub tracktype: Option<String>,
    pub dist_m: f64,
}

impl JeepClassReason {
    pub fn label(&self) -> String {
        match &self.tracktype {
            Some(tt) if !tt.is_empty() => format!("{} {}", self.highway, tt),
            _ => self.highway.clone(),
        }
    }
}

pub fn jeep_class_reason(segments: &[PeakJeepRoadSegment]) -> Option<JeepClassReason> {
    let kept = qualifying_jeep_segments(segments);
    let rank = kept
        .iter()
        .map(|s| jeep_road_rank(&s.highway, s.tracktype.as_deref()))
        .max()?;
    let mut by_class: std::collections::HashMap<(String, Option<String>), f64> =
        std::collections::HashMap::new();
    for s in kept
        .iter()
        .filter(|s| jeep_road_rank(&s.highway, s.tracktype.as_deref()) == rank)
    {
        *by_class
            .entry((s.highway.clone(), s.tracktype.clone()))
            .or_insert(0.0) += s.dist_m.max(0.0);
    }
    let ((highway, tracktype), dist_m) = by_class.into_iter().max_by(|a, b| {
        a.1.partial_cmp(&b.1).unwrap_or(std::cmp::Ordering::Equal)
    })?;
    Some(JeepClassReason {
        highway,
        tracktype,
        dist_m,
    })
}

/// Copy hike grade facts from a profile onto a thin peak row.
pub fn copy_hike_facts_from_profile(entry: &mut PeakCatalogEntry, h: &PeakHikeProfile) {
    entry.hike_gain_m = Some(h.gain_m);
    entry.hike_avg_grade_pct = Some(h.avg_grade_pct);
    entry.hike_max_grade_pct = Some(h.max_grade_pct);
    if entry.hike_m.is_none() {
        entry.hike_m = Some(h.horiz_m);
    }
    if entry.max_slope_deg.is_none() {
        entry.max_slope_deg = Some(h.max_slope_deg);
    }
}

/// Copy jeep OSM class facts from a profile onto a thin peak row.
pub fn copy_jeep_facts_from_profile(entry: &mut PeakCatalogEntry, j: &PeakJeepProfile) {
    if let Some(reason) = jeep_class_reason(&j.segments) {
        entry.jeep_highway = Some(reason.highway);
        entry.jeep_tracktype = reason.tracktype;
    }
}

/// Hike label at read time. Facts win over stored ``hike_difficulty``.
pub fn derived_hike_difficulty(entry: &PeakCatalogEntry) -> Option<String> {
    if let (Some(gain), Some(avg), Some(max)) = (
        entry.hike_gain_m,
        entry.hike_avg_grade_pct,
        entry.hike_max_grade_pct,
    ) {
        let horiz = entry.hike_m.unwrap_or(0.0);
        return Some(hike_difficulty(max, avg, horiz, gain).to_string());
    }
    if let Some(h) = entry.hike.as_ref() {
        return Some(
            hike_difficulty(h.max_grade_pct, h.avg_grade_pct, h.horiz_m, h.gain_m).to_string(),
        );
    }
    if entry.hike_difficulty.is_some() {
        return entry.hike_difficulty.clone();
    }
    if let (Some(hike_m), Some(max_deg)) = (entry.hike_m, entry.max_slope_deg) {
        let max_grade = max_deg.to_radians().tan() * 100.0;
        return Some(hike_difficulty(max_grade, 0.0, hike_m, 0.0).to_string());
    }
    None
}

/// Jeep label at read time. OSM class facts win over stored ``jeep_difficulty``.
pub fn derived_jeep_difficulty(entry: &PeakCatalogEntry) -> Option<String> {
    if let Some(hw) = entry.jeep_highway.as_deref() {
        let rank = jeep_road_rank(hw, entry.jeep_tracktype.as_deref());
        return Some(difficulty_label(rank).to_string());
    }
    if let Some(j) = entry.jeep.as_ref() {
        return Some(jeep_difficulty(&j.segments).to_string());
    }
    entry.jeep_difficulty.clone()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn seg(highway: &str, tracktype: Option<&str>, dist_m: f64) -> PeakJeepRoadSegment {
        PeakJeepRoadSegment {
            highway: highway.into(),
            tracktype: tracktype.map(str::to_string),
            dist_m,
        }
    }

    #[test]
    fn grade2_unclassified_is_medium() {
        let segs = vec![
            seg("unclassified", Some("grade2"), 3000.0),
            seg("unclassified", Some("grade2"), 3600.0),
        ];
        assert_eq!(jeep_difficulty(&segs), "medium");
    }

    #[test]
    fn grade4_track_is_difficult() {
        let segs = vec![
            seg("residential", None, 400.0),
            seg("track", Some("grade4"), 15000.0),
        ];
        assert_eq!(jeep_difficulty(&segs), "difficult");
        let reason = jeep_class_reason(&segs).unwrap();
        assert_eq!(reason.label(), "track grade4");
        assert!((reason.dist_m - 15000.0).abs() < 0.1);
    }

    #[test]
    fn grade5_track_is_extreme() {
        let segs = vec![
            seg("secondary", None, 4000.0),
            seg("track", Some("grade5"), 200.0),
        ];
        assert_eq!(jeep_difficulty(&segs), "extreme");
    }

    #[test]
    fn walked_bare_mountain_east_bump_is_medium() {
        assert_eq!(hike_difficulty(29.3, 16.0, 224.0, 36.0), "medium");
    }

    #[test]
    fn walked_sarcobatus_is_medium() {
        assert_eq!(hike_difficulty(29.0, 9.5, 1283.0, 47.2), "medium");
    }

    #[test]
    fn walked_goldfield_is_difficult() {
        assert_eq!(hike_difficulty(33.3, 24.9, 398.0, 99.1), "difficult");
    }

    #[test]
    fn walked_razorback_is_extreme() {
        assert_eq!(hike_difficulty(103.1, 56.5, 595.0, 336.4), "extreme");
    }

    #[test]
    fn flat_long_walk_is_easy() {
        assert_eq!(hike_difficulty(12.0, 3.7, 1283.0, 20.0), "easy");
    }

    #[test]
    fn chopped_grade4_counts_as_one_stretch() {
        let mut segs = vec![seg("residential", None, 400.0)];
        for _ in 0..300 {
            segs.push(seg("track", Some("grade4"), 50.0));
        }
        assert_eq!(jeep_difficulty(&segs), "difficult");
        let reason = jeep_class_reason(&segs).unwrap();
        assert_eq!(reason.label(), "track grade4");
        assert!((reason.dist_m - 15000.0).abs() < 0.1);
    }

    #[test]
    fn short_connector_does_not_promote() {
        let segs = vec![
            seg("unclassified", Some("grade2"), 5000.0),
            seg("track", Some("grade5"), 20.0),
        ];
        assert_eq!(jeep_difficulty(&segs), "medium");
    }

    #[test]
    fn worse_picks_hike_extreme_over_jeep_medium() {
        assert_eq!(worse_difficulty("extreme", "medium"), "extreme");
        assert_eq!(worse_difficulty("easy", "difficult"), "difficult");
    }

    #[test]
    fn derived_hike_uses_facts_not_stored_label() {
        let entry = PeakCatalogEntry {
            name: None,
            loc: [37.28, -115.64],
            elev_m: Some(1625.0),
            source: "dem".into(),
            compute_key: None,
            road_m: None,
            road_loc: None,
            hike_m: Some(224.0),
            hike_gain_m: Some(36.0),
            hike_avg_grade_pct: Some(16.0),
            hike_max_grade_pct: Some(29.3),
            max_slope_deg: None,
            hike: None,
            paved_loc: None,
            jeep_m: None,
            jeep_highway: None,
            jeep_tracktype: None,
            jeep: None,
            hike_difficulty: Some("extreme".into()),
            jeep_difficulty: None,
            deny: None,
        };
        assert_eq!(derived_hike_difficulty(&entry).as_deref(), Some("medium"));
    }

    #[test]
    fn derived_hike_falls_back_to_stored_label_without_facts() {
        let entry = PeakCatalogEntry {
            name: None,
            loc: [38.0, -117.0],
            elev_m: None,
            source: "dem".into(),
            compute_key: None,
            road_m: None,
            road_loc: None,
            hike_m: None,
            hike_gain_m: None,
            hike_avg_grade_pct: None,
            hike_max_grade_pct: None,
            max_slope_deg: None,
            hike: None,
            paved_loc: None,
            jeep_m: None,
            jeep_highway: None,
            jeep_tracktype: None,
            jeep: None,
            hike_difficulty: Some("difficult".into()),
            jeep_difficulty: None,
            deny: None,
        };
        assert_eq!(derived_hike_difficulty(&entry).as_deref(), Some("difficult"));
    }

    #[test]
    fn derived_jeep_uses_osm_facts() {
        let entry = PeakCatalogEntry {
            name: None,
            loc: [38.0, -117.0],
            elev_m: None,
            source: "dem".into(),
            compute_key: None,
            road_m: None,
            road_loc: None,
            hike_m: None,
            hike_gain_m: None,
            hike_avg_grade_pct: None,
            hike_max_grade_pct: None,
            max_slope_deg: None,
            hike: None,
            paved_loc: None,
            jeep_m: None,
            jeep_highway: Some("track".into()),
            jeep_tracktype: Some("grade4".into()),
            jeep: None,
            hike_difficulty: None,
            jeep_difficulty: Some("easy".into()),
            deny: None,
        };
        assert_eq!(derived_jeep_difficulty(&entry).as_deref(), Some("difficult"));
    }
}
