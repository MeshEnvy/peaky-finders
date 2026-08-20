//! Coarse decode bitmask for viewshed union tie-break.

use std::path::Path;
use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering};

use anyhow::Result;
use peaky_preset::{load_preset, Preset};
use peaky_serve::rf::rf_json_for_preset;
use rayon::prelude::*;
use serde::{Deserialize, Serialize};
use splatter::session::Session;

use crate::cache::{digest_hex, short_digest, FinderCache};
use crate::candidates::Candidate;
use crate::coverage::{rf_digest, site_tx_height};
use crate::telemetry::CacheLedger;

const TIE_BREAK_RASTER: u32 = 128;
const RX_HEIGHT_M: f64 = 2.0;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DecodeBitmask {
    pub cells: Vec<u64>,
    pub popcount: u32,
    pub dim: u32,
}

impl DecodeBitmask {
    pub fn union_with(&self, other: &DecodeBitmask) -> DecodeBitmask {
        assert_eq!(self.dim, other.dim);
        let mut cells = self.cells.clone();
        for (i, o) in other.cells.iter().enumerate() {
            cells[i] |= o;
        }
        let popcount = cells.iter().map(|c| c.count_ones()).sum();
        DecodeBitmask {
            cells,
            popcount,
            dim: self.dim,
        }
    }
}

fn preset_radius_m(preset: &Preset) -> f64 {
    let radius_km = match &preset.simulation.radius_km {
        serde_yaml::Value::Number(n) => n.as_f64().unwrap_or(50.0),
        serde_yaml::Value::String(s) => s.parse().unwrap_or(50.0),
        _ => 50.0,
    };
    radius_km * 1000.0
}

fn build_bitmask(
    session: &Session,
    preset: &Preset,
    candidate: &Candidate,
    rf_json: &str,
    ledger: &CacheLedger,
) -> Result<DecodeBitmask> {
    let dim = TIE_BREAK_RASTER;
    ledger.progress(&format!(
        "bitmask raster site={} dim={dim}",
        short_digest(&candidate.id)
    ));
    let radius_m = preset_radius_m(preset);
    let lat = candidate.lat;
    let lon = candidate.lon;
    let delta_deg = radius_m / 6_378_137.0 * (180.0 / std::f64::consts::PI);
    let cos_lat = lat.to_radians().cos().max(0.01);
    let lon_delta = delta_deg / cos_lat;
    let south = lat - delta_deg;
    let north = lat + delta_deg;
    let west = lon - lon_delta;
    let east = lon + lon_delta;
    let step_lat = (north - south) / dim as f64;
    let step_lon = (east - west) / dim as f64;
    let tx_h = site_tx_height(preset, candidate);
    let word_count = ((dim * dim) as usize + 63) / 64;
    let cells_atomic: Vec<AtomicU64> = (0..word_count).map(|_| AtomicU64::new(0)).collect();
    let rows_done = AtomicUsize::new(0);
    let row_log_every = (dim as usize / 16).max(1);

    (0..dim)
        .into_par_iter()
        .try_for_each(|row| -> Result<()> {
            for col in 0..dim {
                let cell_lat = south + (row as f64 + 0.5) * step_lat;
                let cell_lon = west + (col as f64 + 0.5) * step_lon;
                if session
                    .link_eval_with_heights(
                        lat,
                        lon,
                        tx_h,
                        cell_lat,
                        cell_lon,
                        RX_HEIGHT_M,
                        rf_json,
                    )?
                    .viable
                {
                    let idx = (row * dim + col) as usize;
                    let word = idx / 64;
                    let bit = idx % 64;
                    cells_atomic[word].fetch_or(1u64 << bit, Ordering::Relaxed);
                }
            }
            let n = rows_done.fetch_add(1, Ordering::Relaxed) + 1;
            if n % row_log_every == 0 || n as u32 == dim {
                ledger.progress(&format!("bitmask raster rows [{n}/{dim}]"));
            }
            Ok(())
        })?;

    let cells: Vec<u64> = cells_atomic
        .iter()
        .map(|c| c.load(Ordering::Relaxed))
        .collect();
    let popcount = cells.iter().map(|c| c.count_ones()).sum();
    Ok(DecodeBitmask {
        cells,
        popcount,
        dim,
    })
}

pub fn get_bitmask(
    preset_path: &Path,
    session: &Session,
    candidate: &Candidate,
    cache: &FinderCache,
    ledger: &CacheLedger,
) -> Result<DecodeBitmask> {
    let preset = load_preset(preset_path)?;
    let rf_json = rf_json_for_preset(&preset)?;
    let rf_key = rf_digest(&preset)?;
    let site_key = format!("{:.6},{:.6}", candidate.lat, candidate.lon);
    let key = digest_hex(&[rf_key.as_str(), &site_key, &format!("raster={TIE_BREAK_RASTER}")]);
    let detail = format!(
        "rf={} site={} raster={TIE_BREAK_RASTER}",
        short_digest(&rf_key),
        short_digest(&site_key)
    );
    let bytes = cache.get_or_insert_bytes(ledger, "bitmask", "bitmask", &key, &detail, || {
        let bm = build_bitmask(session, &preset, candidate, &rf_json, ledger)?;
        Ok(bincode::serialize(&bm)?)
    })?;
    bincode::deserialize(&bytes).map_err(Into::into)
}

pub fn path_viewshed_gain(
    preset_path: &Path,
    session: &Session,
    candidates: &[Candidate],
    path: &[usize],
    cache: &FinderCache,
    ledger: &CacheLedger,
) -> Result<u32> {
    ledger.progress(&format!("viewshed union path_len={}", path.len()));
    let done = AtomicUsize::new(0);
    let total = path.len();
    let bitmasks: Result<Vec<DecodeBitmask>> = path
        .par_iter()
        .map(|&idx| {
            let n = done.fetch_add(1, Ordering::Relaxed) + 1;
            ledger.progress(&format!("viewshed site [{n}/{total}]"));
            get_bitmask(preset_path, session, &candidates[idx], cache, ledger)
        })
        .collect();
    let union = bitmasks?.into_iter().reduce(|acc, bm| acc.union_with(&bm));
    let pop = union.map(|u| u.popcount).unwrap_or(0);
    ledger.progress(&format!("viewshed union popcount={pop}"));
    Ok(pop)
}
