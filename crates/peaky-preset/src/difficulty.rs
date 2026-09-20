//! Access difficulty labels (easy / medium / difficult / extreme).

use crate::model::{
    HikeDifficultyBands, JeepDifficultyBands, PeakHikeProfile, PeakJeepProfile,
    PeakJeepRoadSegment, PeaksDifficultyConfig,
};
use crate::peaks_index::PeakIndexRow;

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

pub const HIKE_EXTREME_MIN_GAIN_M: f64 = 250.0;
pub const HIKE_EXTREME_MIN_AVG_GRADE_PCT: f64 = 18.0;
pub const HIKE_DIFFICULT_MIN_GAIN_M: f64 = 80.0;
pub const HIKE_DIFFICULT_MIN_AVG_GRADE_PCT: f64 = 15.0;
pub const HIKE_MEDIUM_MIN_MAX_GRADE_PCT: f64 = 15.0;
pub const HIKE_MEDIUM_MIN_AVG_GRADE_PCT: f64 = 8.0;
pub const HIKE_MEDIUM_MIN_GAIN_M: f64 = 40.0;

pub fn default_hike_bands() -> HikeDifficultyBands {
    HikeDifficultyBands::default()
}

pub fn default_jeep_bands() -> JeepDifficultyBands {
    JeepDifficultyBands::default()
}

/// Hike difficulty from gain + sustained avg grade. Max grade only signals scrambling (medium).
pub fn classify_hike(
    max_grade_pct: f64,
    avg_grade_pct: f64,
    gain_m: f64,
    bands: &HikeDifficultyBands,
) -> &'static str {
    if gain_m >= bands.extreme.min_gain_m && avg_grade_pct >= bands.extreme.min_avg_grade_pct {
        "extreme"
    } else if gain_m >= bands.difficult.min_gain_m
        && avg_grade_pct >= bands.difficult.min_avg_grade_pct
    {
        "difficult"
    } else if max_grade_pct >= bands.medium.min_max_grade_pct
        || avg_grade_pct >= bands.medium.min_avg_grade_pct
        || gain_m >= bands.medium.min_gain_m
    {
        "medium"
    } else {
        "easy"
    }
}

pub fn hike_difficulty(
    max_grade_pct: f64,
    avg_grade_pct: f64,
    _horiz_m: f64,
    gain_m: f64,
) -> &'static str {
    classify_hike(
        max_grade_pct,
        avg_grade_pct,
        gain_m,
        &default_hike_bands(),
    )
}

/// OSM track/highway class → rank using YAML jeep bands.
pub fn jeep_segment_rank(
    highway: &str,
    tracktype: Option<&str>,
    bands: &JeepDifficultyBands,
) -> u8 {
    if let Some(tt) = tracktype {
        if bands.extreme.tracktypes.iter().any(|t| t == tt) {
            return 3;
        }
        if bands.difficult.tracktypes.iter().any(|t| t == tt) {
            return 2;
        }
        if bands.medium.tracktypes.iter().any(|t| t == tt) {
            return 1;
        }
    }
    match highway {
        "motorway" | "trunk" | "primary" | "secondary" | "tertiary" => 0,
        "residential" | "unclassified" => match tracktype {
            Some("grade1") => 0,
            _ => 1,
        },
        "service" => 1,
        "track" => 1,
        _ if bands.medium.highways.iter().any(|h| h == highway) => 1,
        _ => 1,
    }
}

/// Legacy rank table (default bands).
pub fn jeep_road_rank(highway: &str, tracktype: Option<&str>) -> u8 {
    jeep_segment_rank(highway, tracktype, &default_jeep_bands())
}

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

pub fn classify_jeep(segments: &[PeakJeepRoadSegment], bands: &JeepDifficultyBands) -> &'static str {
    let rank = qualifying_jeep_segments(segments)
        .iter()
        .map(|s| jeep_segment_rank(&s.highway, s.tracktype.as_deref(), bands))
        .max()
        .unwrap_or(0);
    difficulty_label(rank)
}

pub fn jeep_difficulty(segments: &[PeakJeepRoadSegment]) -> &'static str {
    classify_jeep(segments, &default_jeep_bands())
}

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

pub fn jeep_class_reason_with_bands(
    segments: &[PeakJeepRoadSegment],
    bands: &JeepDifficultyBands,
) -> Option<JeepClassReason> {
    let kept = qualifying_jeep_segments(segments);
    let rank = kept
        .iter()
        .map(|s| jeep_segment_rank(&s.highway, s.tracktype.as_deref(), bands))
        .max()?;
    let mut by_class: std::collections::HashMap<(String, Option<String>), f64> =
        std::collections::HashMap::new();
    for s in kept
        .iter()
        .filter(|s| jeep_segment_rank(&s.highway, s.tracktype.as_deref(), bands) == rank)
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

pub fn jeep_class_reason(segments: &[PeakJeepRoadSegment]) -> Option<JeepClassReason> {
    jeep_class_reason_with_bands(segments, &default_jeep_bands())
}

pub fn access_difficulty_from_stored(
    hike: Option<&str>,
    jeep: Option<&str>,
) -> Option<String> {
    let hike = hike.filter(|s| !s.is_empty());
    let jeep = jeep.filter(|s| !s.is_empty());
    match (hike, jeep) {
        (Some(h), Some(j)) => Some(worse_difficulty(h, j).to_string()),
        (Some(h), None) => Some(h.to_string()),
        (None, Some(j)) => Some(j.to_string()),
        _ => None,
    }
}

fn legacy_classify_hike(_entry: &PeakIndexRow, _bands: &HikeDifficultyBands) -> Option<String> {
    None
}

fn legacy_classify_jeep(_entry: &PeakIndexRow, _bands: &JeepDifficultyBands) -> Option<String> {
    None
}

/// Stored hike label from index row.
pub fn stored_hike_difficulty(
    entry: &PeakIndexRow,
    bands: Option<&HikeDifficultyBands>,
) -> Option<String> {
    if let Some(ref s) = entry.hike_difficulty {
        if !s.is_empty() {
            return Some(s.clone());
        }
    }
    legacy_classify_hike(entry, bands.unwrap_or(&default_hike_bands()))
}

/// Stored jeep label; legacy rows fall back to classifying from facts.
pub fn stored_jeep_difficulty(
    entry: &PeakIndexRow,
    bands: Option<&JeepDifficultyBands>,
) -> Option<String> {
    if let Some(ref s) = entry.jeep_difficulty {
        if !s.is_empty() {
            return Some(s.clone());
        }
    }
    legacy_classify_jeep(entry, bands.unwrap_or(&default_jeep_bands()))
}

pub fn stored_access_difficulty(
    entry: &PeakIndexRow,
    difficulty: Option<&PeaksDifficultyConfig>,
) -> Option<String> {
    let hike_bands = difficulty.map(|d| &d.hike);
    let jeep_bands = difficulty.map(|d| &d.jeep);
    access_difficulty_from_stored(
        stored_hike_difficulty(entry, hike_bands).as_deref(),
        stored_jeep_difficulty(entry, jeep_bands).as_deref(),
    )
}

#[deprecated(note = "use stored_hike_difficulty")]
pub fn derived_hike_difficulty(entry: &PeakIndexRow) -> Option<String> {
    stored_hike_difficulty(entry, None)
}

#[deprecated(note = "use stored_jeep_difficulty")]
pub fn derived_jeep_difficulty(entry: &PeakIndexRow) -> Option<String> {
    stored_jeep_difficulty(entry, None)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::peaks_index::PeakIndexRow;

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
    fn stored_hike_prefers_stamped_label() {
        let entry = PeakIndexRow {
            name: None,
            loc: [37.28, -115.64],
            elev_m: Some(1625.0),
            source: "dem".into(),
            kind: crate::peaks_index::PeakKind::Peak,
            point_key: None,
            road_m: None,
            road_loc: None,
            hike_m: Some(224.0),
            jeep_m: None,
            trail_m: None,
            paved_loc: None,
            hike_difficulty: Some("extreme".into()),
            jeep_difficulty: None,
            access_difficulty: None,
            eligible: true,
        };
        assert_eq!(stored_hike_difficulty(&entry, None).as_deref(), Some("extreme"));
    }

    #[test]
    fn stored_jeep_returns_stamped_label() {
        let entry = PeakIndexRow {
            name: None,
            loc: [38.0, -117.0],
            elev_m: None,
            source: "dem".into(),
            kind: crate::peaks_index::PeakKind::Peak,
            point_key: None,
            road_m: None,
            road_loc: None,
            hike_m: None,
            jeep_m: None,
            trail_m: None,
            paved_loc: None,
            hike_difficulty: None,
            jeep_difficulty: Some("difficult".into()),
            access_difficulty: None,
            eligible: true,
        };
        assert_eq!(stored_jeep_difficulty(&entry, None).as_deref(), Some("difficult"));
    }
}
