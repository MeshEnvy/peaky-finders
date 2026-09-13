//! Point elevation + Skadi map tiles.

use std::collections::HashMap;

use axum::{
    extract::{Path, Query, State},
    http::StatusCode,
    Json,
};
use serde_json::{json, Value};
use splatter::Session;

use crate::state::AppState;
use crate::viewshed_sim::parse_lat_lon_params;

pub fn elev_payload(lat: f64, lon: f64, elev_m: f64, project: &str) -> Value {
    json!({
        "lat": lat,
        "lon": lon,
        "elev_m": (elev_m * 10.0).round() / 10.0,
        "project": project,
    })
}

/// Ensure the Skadi tile is loaded, then bilinear-sample AMSL meters.
pub fn sample_place_elev(session: &Session, lat: f64, lon: f64) -> Result<f64, String> {
    session
        .ensure_tiles_for_points(&[(lat, lon)], 0.0)
        .map_err(|e| e.to_string())?;
    Ok(session.sample_elev_m(lat, lon))
}

pub async fn place_elev(
    State(state): State<AppState>,
    Path(_slug): Path<String>,
    Query(params): Query<HashMap<String, String>>,
) -> Result<Json<Value>, (StatusCode, String)> {
    let (lat, lon) = parse_lat_lon_params(&params).map_err(|e| (StatusCode::BAD_REQUEST, e))?;
    let session = state.session.clone();
    let elev = tokio::task::spawn_blocking(move || sample_place_elev(&session, lat, lon))
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
        .map_err(|e| (StatusCode::SERVICE_UNAVAILABLE, e))?;
    Ok(Json(elev_payload(lat, lon, elev, &state.slug)))
}

#[cfg(test)]
mod tests {
    use super::*;
    use splatter::dem::{DemMosaic, DemTile};
    use std::collections::HashMap;

    fn flat_mosaic(elev: i16) -> DemMosaic {
        let n = 11usize;
        let mut tiles = HashMap::new();
        tiles.insert(
            (40, -120),
            DemTile {
                sw_lat: 40.0,
                sw_lon: -120.0,
                n,
                elevations: vec![elev; n * n],
            },
        );
        DemMosaic::from_tiles(tiles)
    }

    #[test]
    fn elev_payload_rounds_tenth() {
        let v = elev_payload(39.5, -119.8, 2134.26, "nevada");
        assert_eq!(v["elev_m"], 2134.3);
        assert_eq!(v["project"], "nevada");
    }

    #[test]
    fn sample_place_elev_reads_installed_mosaic() {
        let tmp = tempfile::TempDir::new().expect("tempdir");
        let session = Session::new(tmp.path().join("mirror"), false, 1);
        session.install_dem_mosaic(flat_mosaic(2140));
        let elev = sample_place_elev(&session, 40.5, -119.5).expect("sample");
        assert!((elev - 2140.0).abs() < 0.01);
    }
}
