//! meshenvy.org /map contract: Nevada hub anchors for empty-overlay bbox.

/// Reno, Wells, Las Vegas downtown anchors, WGS84 [lng, lat].
pub const NEVADA_HUB_VERTICES: [[f64; 2]; 3] = [
    [-119.8138, 39.5296],
    [-114.9644, 41.1116],
    [-115.1398, 36.1699],
];

/// Padded `[west, south, east, north]` around the three hub vertices (empty fleet export).
pub fn nevada_empty_overlay_bbox(pad_deg: f64) -> [f64; 4] {
    let mut west = f64::INFINITY;
    let mut south = f64::INFINITY;
    let mut east = f64::NEG_INFINITY;
    let mut north = f64::NEG_INFINITY;
    for [lng, lat] in NEVADA_HUB_VERTICES {
        west = west.min(lng);
        south = south.min(lat);
        east = east.max(lng);
        north = north.max(lat);
    }
    [
        west - pad_deg,
        south - pad_deg,
        east + pad_deg,
        north + pad_deg,
    ]
}
