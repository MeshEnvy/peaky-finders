# Splatter reference

## `SplatCoverageRequest` fields

| Field | Notes |
|-------|-------|
| `lat`, `lon` | Site position; drives Skadi tile set |
| `tx_height`, `rx_height` | Meters AGL (≥1) |
| `tx_power` | dBm |
| `tx_gain`, `rx_gain` | dBi |
| `frequency_mhz` | Carrier |
| `signal_threshold` | dBm — preset sets `modem_decode + reliability_margin` |
| `clutter_height` | Ground clutter (m) |
| `ground_dielectric`, `ground_conductivity`, `atmosphere_bending` | Environment |
| `radius` | Coverage range (m); also max hop range for link checks |
| `system_loss` | Combined tx+rx cable/connector loss (dB) |
| `radio_climate`, `polarization` | SPLAT-style climate enum / H or V |
| `situation_fraction`, `time_fraction` | Reliability % (LoRa fade margin) |
| `fresnel_clearance_fraction` | 0–1; knife-edge clearance floor |
| `colormap`, `min_dbm`, `max_dbm` | Raster display |
| `raster_dimension` | Square pixel count; ground step ≈ `radius / raster_dimension` |
| `modem` | **Required** — `spreading_factor`, `bandwidth_khz`, `coding_rate`, `implementation_margin_db`, optional `sensitivity_dbm` |

Preset mapping folds `environment.coverage_pessimism_db` into `modem.implementation_margin_db` at emit time (catalog YAML stays literal).

## Workspace layout

```
<preset>/.peaky/cache/viewsheds/
  request.json          # batch: JSON array of all workspace requests
  <64-char-hex-digest>/
    request.json        # single-site propagation object
    output.ppm
    output.kml
    splat.png
    manifest.json
    splat.gpkg          # Python vectorize (optional)
    splat.kml
```

Sites with identical propagation inputs (coords excluded from hash) reuse one digest folder.

## `Session` API

In-process via `splatter::Session` (shared resident DEM).

| Method | Purpose |
|--------|---------|
| `ensure_tiles_for_points` | Tile union for lat/lon list + buffer |
| `run` / `run_batch` | Coverage from `request.json` |
| `link_eval` / `link_viable` | One-way hop |
| `link_mutual_viable` | Both directions must meet threshold |
| `link_mutual_batch` | Parallel mutual checks |
| `input_sha256` | Hash helper |

Env: `SPLAT_CACHE` (Skadi HGT mirror), `SPLATTER_BATCH_JOBS` (rayon cap for one batch).

## Physics summary

- **Coverage raster**: For each pixel, ray from TX along great-circle bearing; knife-edge excess loss (ITU-R P.526) on dominant terrain obstruction; FSPL + EIRP chain vs `signal_threshold`.
- **Links / P2P**: Same knife-edge + FSPL physics; terrain step = native DEM spacing (~30 m). Viewshed rasters use coarser `radius / raster_dimension` for display only.
- **Mutual**: viable both ways.
- **DEM**: Skadi 1° HGT tiles (`N##W###.hgt.gz`); fetched on demand to mirror; bilinear sample, void → 0 (ocean).
- **Earth model**: 4/3 effective radius for LOS refraction.

## Schema version bump checklist

1. Increment `SPLAT_CACHE_SCHEMA_VERSION` in `splatter/src/hash.rs`.
2. Update golden hash in `splatter/tests/fixtures/splat_request_hash_fixture.json`.
3. Run `cargo test -p splatter`.
4. Document breaking impact; viewshed caches invalidate by digest.
5. Update MEMORY.md if artifact or workflow semantics change.
