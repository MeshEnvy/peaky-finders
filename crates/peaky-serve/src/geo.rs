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
