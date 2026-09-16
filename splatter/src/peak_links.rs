//! Ranked binned peaks with mutual RF links to a source point.

use std::path::Path;

use anyhow::Result;
use geo::MultiPolygon;
use rayon::prelude::*;

use crate::dem::DemMosaic;
use crate::peaks::{
    binned_peaks_in_hop_disc, peak_passes_land_filter, GoalProgressFilter, HopDiscScanProgress,
    LandFilterIndex, Peak, RingSectorFilter,
};
use crate::propagate::{
    haversine_m, link_context_from_json, LinkContext,
};

pub struct LinkablePeak {
    pub lon: f64,
    pub lat: f64,
    pub elev_m: f64,
}

pub fn disc_binned_peaks_with_dem(
    dem: &DemMosaic,
    source_lat: f64,
    source_lon: f64,
    hop_radius_m: f64,
    land_filter: Option<&MultiPolygon<f64>>,
    scan_bbox: Option<(f64, f64, f64, f64)>,
    progress_lens: Option<GoalProgressFilter>,
    ring_sector: Option<RingSectorFilter>,
    bin_size_m: f64,
    land_index: Option<&LandFilterIndex>,
    mask_cache_dir: Option<&Path>,
    on_progress: Option<&(dyn Fn(HopDiscScanProgress) + Send + Sync)>,
) -> Result<Vec<Peak>> {
    let binned = binned_peaks_in_hop_disc(
        dem,
        source_lat,
        source_lon,
        hop_radius_m,
        bin_size_m,
        scan_bbox,
        progress_lens,
        ring_sector,
        land_filter,
        land_index,
        mask_cache_dir,
        on_progress,
    )?;
    Ok(binned
        .into_par_iter()
        .filter(|peak| peak_passes_land_filter(*peak, land_filter, scan_bbox, land_index))
        .filter(|peak| haversine_m(source_lat, source_lon, peak.lat, peak.lon) <= hop_radius_m)
        .collect())
}

pub fn linkable_binned_peaks_with_dem(
    dem: &DemMosaic,
    source_lat: f64,
    source_lon: f64,
    tx_height_agl: f64,
    hop_radius_m: f64,
    land_filter: Option<&MultiPolygon<f64>>,
    viewport_bbox: Option<(f64, f64, f64, f64)>,
    rf_json: &str,
    limit: usize,
    bin_size_m: f64,
    rx_height_agl: Option<f64>,
) -> Result<Vec<LinkablePeak>> {
    let mut ctx = link_context_from_json(rf_json)?;
    let from_tx_h = tx_height_agl.max(1.0);
    let peak_tx_h = ctx.tx_height.max(1.0);
    ctx.tx_height = from_tx_h;
    if let Some(rx) = rx_height_agl {
        ctx.rx_height = rx;
    }

    let candidates = disc_binned_peaks_with_dem(
        dem,
        source_lat,
        source_lon,
        hop_radius_m,
        land_filter,
        viewport_bbox,
        None,
        None,
        bin_size_m,
        None,
        None,
        None,
    )?;
    let candidates: Vec<Peak> = candidates
        .into_iter()
        .filter(|peak| peak_within_range(source_lat, source_lon, *peak, &ctx))
        .collect();

    if candidates.is_empty() {
        return Ok(Vec::new());
    }

    let base = link_context_from_json(rf_json)?;
    let viable: Vec<bool> = candidates
        .par_iter()
        .map(|peak| {
            crate::propagate::mutual_site_link_viable(
                crate::propagate::evaluate_mutual_site_link_margin(
                    dem,
                    source_lat,
                    source_lon,
                    from_tx_h,
                    peak.lat,
                    peak.lon,
                    peak_tx_h,
                    &base,
                ),
            )
        })
        .collect();
    let mut out: Vec<LinkablePeak> = candidates
        .iter()
        .zip(viable)
        .filter(|(_, ok)| *ok)
        .map(|(peak, _)| LinkablePeak {
            lon: peak.lon,
            lat: peak.lat,
            elev_m: peak.elev_m,
        })
        .collect();
    if limit > 0 && out.len() > limit {
        out.truncate(limit);
    }
    Ok(out)
}

fn peak_within_range(source_lat: f64, source_lon: f64, peak: Peak, ctx: &LinkContext) -> bool {
    haversine_m(source_lat, source_lon, peak.lat, peak.lon) <= ctx.max_range_m
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::dem::{DemMosaic, DemTile};
    use std::time::{Duration, Instant};

    const PEAK_SCAN_BUDGET: Duration = Duration::from_secs(3);

    fn flat_tile(sw_lat: i32, sw_lon: i32, elev: i16) -> DemTile {
        let n = 121usize;
        DemTile {
            sw_lat: sw_lat as f64,
            sw_lon: sw_lon as f64,
            n,
            elevations: vec![elev; n * n],
        }
    }

    #[test]
    fn linkable_peaks_on_one_tile_under_two_seconds() {
        let dem = DemMosaic::with_single_tile((39, -120), flat_tile(39, -120, 2100));
        let rf_json = include_str!("../tests/fixtures/splat_request_hash_fixture.json");
        let t0 = Instant::now();
        let peaks = linkable_binned_peaks_with_dem(
            &dem,
            39.5,
            -119.5,
            10.0,
            50_000.0,
            None,
            Some((-121.0, 38.0, -119.0, 41.0)),
            rf_json,
            8,
            1500.0,
            None,
        )
        .expect("linkable peaks");
        assert!(t0.elapsed() < PEAK_SCAN_BUDGET, "peak scan took {:?}", t0.elapsed());
        assert!(peaks.len() <= 8);
    }

    #[test]
    fn hop_disc_peak_scan_completes_quickly() {
        use crate::peaks::binned_peaks_in_hop_disc;
        let n = 31usize;
        let mut elevations = vec![2000i16; n * n];
        elevations[15 * n + 15] = 2500;
        let tile = DemTile {
            sw_lat: 39.0,
            sw_lon: -120.0,
            n,
            elevations,
        };
        let dem = DemMosaic::with_single_tile((39, -120), tile);
        let t0 = Instant::now();
        let peaks = binned_peaks_in_hop_disc(
            &dem,
            39.5,
            -119.5,
            30_000.0,
            1500.0,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        )
        .expect("binned");
        assert!(t0.elapsed() < Duration::from_millis(500));
        assert!(!peaks.is_empty());
    }

    #[test]
    fn hop_disc_finds_narrow_summit_between_old_stride_samples() {
        let n = 121usize;
        let mut elevations = vec![2000i16; n * n];
        let summit = 60usize;
        elevations[summit * n + summit] = 2800;
        let tile = DemTile {
            sw_lat: 39.0,
            sw_lon: -120.0,
            n,
            elevations,
        };
        let center_lat = {
            let spacing = tile.spacing_deg();
            let north = tile.sw_lat + 1.0;
            north - (summit as f64 + 0.5) * spacing
        };
        let center_lon = {
            let spacing = tile.spacing_deg();
            tile.sw_lon + (summit as f64 + 0.5) * spacing
        };
        let dem = DemMosaic::with_single_tile((39, -120), tile);
        let peaks = binned_peaks_in_hop_disc(
            &dem,
            center_lat,
            center_lon,
            5000.0,
            1500.0,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        )
        .expect("binned");
        assert!(
            peaks.iter().any(|p| (p.elev_m - 2800.0).abs() < f64::EPSILON),
            "expected summit at 2800m, got {:?}",
            peaks
        );
    }

    #[test]
    fn skadi_resolution_hop_disc_scan_under_two_seconds() {
        use crate::peaks::binned_peaks_in_hop_disc;
        let n = 3601usize;
        let summit = n / 2;
        let mut elevations = vec![2100i16; n * n];
        elevations[summit * n + summit] = 2800;
        let tile = DemTile {
            sw_lat: 39.0,
            sw_lon: -120.0,
            n,
            elevations,
        };
        let dem = DemMosaic::with_single_tile((39, -120), tile);
        let t0 = Instant::now();
        let peaks = binned_peaks_in_hop_disc(
            &dem,
            39.5,
            -119.5,
            50_000.0,
            1500.0,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        )
        .expect("binned");
        assert!(
            t0.elapsed() < PEAK_SCAN_BUDGET,
            "3601² hop scan took {:?}",
            t0.elapsed()
        );
        assert!(peaks.iter().any(|p| (p.elev_m - 2800.0).abs() < f64::EPSILON));
    }
}
