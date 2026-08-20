//! One-way decode coverage: site → waypoint or site → point.

use std::path::Path;

use anyhow::Result;
use peaky_preset::{load_preset, Preset};
use peaky_serve::rf::{default_repeater_tx_height_m, rf_json_for_preset};
use splatter::session::Session;

use crate::cache::{digest_hex, short_digest, FinderCache};
use crate::candidates::Candidate;
use crate::route::Waypoint;
use crate::telemetry::CacheLedger;

pub fn rf_digest(preset: &Preset) -> Result<String> {
    let rf_json = rf_json_for_preset(preset)?;
    Ok(digest_hex(&["rf", &rf_json]))
}

pub fn site_tx_height(preset: &Preset, candidate: &Candidate) -> f64 {
    candidate
        .height_m
        .unwrap_or_else(|| default_repeater_tx_height_m(preset))
}

fn cover_bool(
    session: &Session,
    rf_json: &str,
    from_lat: f64,
    from_lon: f64,
    tx_height: f64,
    to_lat: f64,
    to_lon: f64,
    rx_height: f64,
    cache: &FinderCache,
    ledger: &CacheLedger,
    rf_key: &str,
    site_key: &str,
    to_key: &str,
) -> Result<bool> {
    let cover_key = digest_hex(&[
        rf_key,
        site_key,
        &to_key,
        &format!("tx={tx_height}"),
        &format!("rx={rx_height}"),
    ]);
    let detail = format!(
        "rf={} site={} to={}",
        short_digest(rf_key),
        short_digest(site_key),
        short_digest(&to_key)
    );
    cache.get_or_insert_bool(ledger, "cover", "cover", &cover_key, &detail, || {
        Ok(session
            .link_eval_with_heights(
                from_lat,
                from_lon,
                tx_height,
                to_lat,
                to_lon,
                rx_height,
                rf_json,
            )?
            .viable)
    })
}

pub fn covers_waypoint(
    session: &Session,
    _preset: &Preset,
    rf_json: &str,
    candidate: &Candidate,
    tx_height: f64,
    waypoint: &Waypoint,
    cache: &FinderCache,
    ledger: &CacheLedger,
    rf_key: &str,
) -> Result<bool> {
    let site_key = format!("{:.6},{:.6}", candidate.lat, candidate.lon);
    let wp_key = format!("{:.6},{:.6}", waypoint.lat, waypoint.lon);
    cover_bool(
        session,
        rf_json,
        candidate.lat,
        candidate.lon,
        tx_height,
        waypoint.lat,
        waypoint.lon,
        2.0,
        cache,
        ledger,
        rf_key,
        &site_key,
        &wp_key,
    )
}

/// One-way decode: site → peak/point (rx at target elevation).
pub fn covers_point(
    session: &Session,
    rf_json: &str,
    from: &Candidate,
    tx_height: f64,
    to_lat: f64,
    to_lon: f64,
    rx_height: f64,
    cache: &FinderCache,
    ledger: &CacheLedger,
    rf_key: &str,
) -> Result<bool> {
    let site_key = format!("{:.6},{:.6}", from.lat, from.lon);
    let to_key = format!("{:.6},{:.6}", to_lat, to_lon);
    cover_bool(
        session,
        rf_json,
        from.lat,
        from.lon,
        tx_height,
        to_lat,
        to_lon,
        rx_height,
        cache,
        ledger,
        rf_key,
        &site_key,
        &to_key,
    )
}

/// One-way decode: site → another candidate (rx at target tx height).
pub fn covers_candidate_one_way(
    session: &Session,
    preset: &Preset,
    rf_json: &str,
    from: &Candidate,
    to: &Candidate,
    cache: &FinderCache,
    ledger: &CacheLedger,
    rf_key: &str,
) -> Result<bool> {
    let tx_h = site_tx_height(preset, from);
    let rx_h = site_tx_height(preset, to);
    covers_point(
        session,
        rf_json,
        from,
        tx_h,
        to.lat,
        to.lon,
        rx_h,
        cache,
        ledger,
        rf_key,
    )
}

/// Find existing registry indices that one-way cover a waypoint.
pub fn covering_indices(
    preset_path: &Path,
    session: &Session,
    candidates: &[Candidate],
    waypoint: &Waypoint,
    cache: &FinderCache,
    ledger: &CacheLedger,
) -> Result<Vec<usize>> {
    let preset = load_preset(preset_path)?;
    let rf_json = rf_json_for_preset(&preset)?;
    let rf_key = rf_digest(&preset)?;
    let mut out = Vec::new();
    for (idx, cand) in candidates.iter().enumerate() {
        let tx_h = site_tx_height(&preset, cand);
        if covers_waypoint(
            session,
            &preset,
            &rf_json,
            cand,
            tx_h,
            waypoint,
            cache,
            ledger,
            &rf_key,
        )? {
            out.push(idx);
        }
    }
    Ok(out)
}
