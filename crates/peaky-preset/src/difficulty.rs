//! Access difficulty labels (easy / medium / difficult / extreme).

use crate::model::PeakJeepRoadSegment;

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

/// Avg grade only counts on hikes long enough that a steady slope is the work.
pub const HIKE_AVG_MIN_HORIZ_M: f64 = 400.0;

/// Hike difficulty from segment grades. Short pads use max only.
pub fn hike_difficulty(max_grade_pct: f64, avg_grade_pct: f64, horiz_m: f64) -> &'static str {
    let avg = if horiz_m >= HIKE_AVG_MIN_HORIZ_M {
        avg_grade_pct
    } else {
        0.0
    };
    if max_grade_pct >= 25.0 || avg >= 12.0 {
        "extreme"
    } else if max_grade_pct >= 18.0 || avg >= 8.0 {
        "difficult"
    } else if max_grade_pct >= 10.0 || avg >= 5.0 {
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
    fn short_hike_ignores_avg_grade() {
        assert_eq!(hike_difficulty(13.9, 9.7, 150.0), "medium");
        assert_eq!(hike_difficulty(13.9, 9.7, 400.0), "difficult");
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
}
