//! Shared geodesy helpers for peaky-serve.

/// Initial bearing from (lat1, lon1) to (lat2, lon2) in degrees [0, 360).
pub fn bearing_deg(lat1: f64, lon1: f64, lat2: f64, lon2: f64) -> f64 {
    let phi1 = lat1.to_radians();
    let phi2 = lat2.to_radians();
    let dlambda = (lon2 - lon1).to_radians();
    let y = dlambda.sin() * phi2.cos();
    let x = phi1.cos() * phi2.sin() - phi1.sin() * phi2.cos() * dlambda.cos();
    (y.atan2(x).to_degrees() + 360.0) % 360.0
}

/// Project C onto A–B in a local meters frame. `None` if AB is degenerate.
/// `t` is unbounded (behind A is negative, past B is `> 1`).
pub fn along_track_t(
    a_lat: f64,
    a_lon: f64,
    b_lat: f64,
    b_lon: f64,
    c_lat: f64,
    c_lon: f64,
) -> Option<f64> {
    let mid_lat = (a_lat + b_lat) * 0.5;
    let m_per_deg_lat = 111_000.0;
    let m_per_deg_lon = 111_000.0 * mid_lat.to_radians().cos().max(0.01);
    let abx = (b_lon - a_lon) * m_per_deg_lon;
    let aby = (b_lat - a_lat) * m_per_deg_lat;
    let ab2 = abx * abx + aby * aby;
    if ab2 < 1.0 {
        return None;
    }
    let acx = (c_lon - a_lon) * m_per_deg_lon;
    let acy = (c_lat - a_lat) * m_per_deg_lat;
    Some((acx * abx + acy * aby) / ab2)
}

/// Project C onto A–B. `None` if AB is degenerate or the foot is on/behind
/// an endpoint (`t ∉ (0.05, 0.95)`).
pub fn along_track_fraction(
    a_lat: f64,
    a_lon: f64,
    b_lat: f64,
    b_lon: f64,
    c_lat: f64,
    c_lon: f64,
) -> Option<f64> {
    let t = along_track_t(a_lat, a_lon, b_lat, b_lon, c_lat, c_lon)?;
    if t <= 0.05 || t >= 0.95 {
        return None;
    }
    Some(t)
}

pub fn peak_between_endpoints(
    a_lat: f64,
    a_lon: f64,
    b_lat: f64,
    b_lon: f64,
    c_lat: f64,
    c_lon: f64,
) -> bool {
    along_track_fraction(a_lat, a_lon, b_lat, b_lon, c_lat, c_lon).is_some()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn along_track_accepts_midpoint_and_off_axis_rejects_behind() {
        let a = (39.0, -119.0);
        let b = (39.1, -119.0);
        let mid = (39.05, -119.0);
        let off = (39.05, -118.95);
        let behind = (38.99, -119.0);
        assert!(peak_between_endpoints(a.0, a.1, b.0, b.1, mid.0, mid.1));
        assert!(peak_between_endpoints(a.0, a.1, b.0, b.1, off.0, off.1));
        assert!(!peak_between_endpoints(a.0, a.1, b.0, b.1, behind.0, behind.1));
        let t = along_track_fraction(a.0, a.1, b.0, b.1, off.0, off.1).unwrap();
        assert!((t - 0.5).abs() < 0.05);
    }

    #[test]
    fn along_track_keeps_bare_schader_ridge_peak() {
        // Field hop Bare Mountain East → Bare-Schader; dem-36817 is off-axis
        // (path detour ~13%) but projects at t≈0.76.
        let a = (36.87133, -116.683758);
        let b = (36.74388, -116.45068);
        let ridge = (36.8171787207348, -116.46886915215099);
        let t = along_track_fraction(a.0, a.1, b.0, b.1, ridge.0, ridge.1).unwrap();
        assert!(t > 0.7 && t < 0.85, "t={t}");
        assert!(peak_between_endpoints(a.0, a.1, b.0, b.1, ridge.0, ridge.1));
    }
}
