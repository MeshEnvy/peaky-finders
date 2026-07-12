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

## CLI

Built with `--no-default-features` (no PyO3):

```bash
cargo build --release --bin splatter --no-default-features
splatter run --work-dir /work [--verbose]
splatter run-batch --work-dir /work [--verbose]
splatter input-sha256 --request /work/request.json
```

Env:

| Var | Default | Role |
|-----|---------|------|
| `SPLAT_CACHE` | `<work-dir>/.tile_cache` | Skadi HGT mirror root |
| `SPLATTER_BATCH_JOBS` | Rayon thread count | Parallel coverage jobs in one batch |

Docker: `splatter/Dockerfile` → `splatter:latest` (CLI only).

## PyO3 `Session` API

Process-wide singleton via `get_session(mirror_root=..., verbose=..., reset=...)`.

| Method | Purpose |
|--------|---------|
| `preload_tiles(names)` | Load HGT tiles into resident mosaic |
| `ensure_tiles_for_points(points, buffer_m)` | Tile union for lat/lon list + buffer |
| `run(work_dir)` | Single coverage from `work_dir/request.json` |
| `run_batch(work_dir, batch_jobs, requests_json=None)` | Batch; JSON array inline or from `request.json` |
| `link_eval` / `link_viable` | One-way hop |
| `link_mutual_viable` | Both directions must meet threshold |
| `link_mutual_batch` | Parallel mutual checks |
| `input_sha256(req)` | Hash helper |

Module: `splatter._core` (maturin); thin wrapper in `splatter/python/splatter/__init__.py`.

## Physics summary

- **Coverage raster**: For each pixel, ray from TX along great-circle bearing; knife-edge excess loss (ITU-R P.526) on dominant terrain obstruction; FSPL + EIRP chain vs `signal_threshold`.
- **Links**: Same ray physics along TX→RX profile (`PROFILE_STEP_M` ≈ 120 m); mutual = viable both ways.
- **DEM**: Skadi 1° HGT tiles (`N##W###.hgt.gz`); fetched on demand to mirror; bilinear sample, void → 0 (ocean).
- **Earth model**: 4/3 effective radius for LOS refraction.

## Schema version bump checklist

1. Increment `SPLAT_CACHE_SCHEMA_VERSION` in `splatter/src/hash.rs` and `peaky_finders/splat_input_hash.py`.
2. Update golden hash in `splatter/tests/fixtures/splat_request_hash_fixture.json` test + `test_coverage_binaries.py` if fixture unchanged.
3. Run `cargo test` in `splatter/` and `./peaky test tests/test_peaky_los_input_hash.py tests/test_coverage_binaries.py`.
4. Document breaking impact; users re-run `peaky build --force` on affected presets.
5. Update MEMORY.md if artifact or workflow semantics change.

## Packaging

```
splatter/
  pyproject.toml       # maturin, module splatter._core
  python/splatter/     # get_session, reset_session
  src/lib.rs           # re-exports Session, hash
  src/python.rs        # PyO3 module
```

Poetry path dep: `peaky_finders/pyproject.toml` → `splatter = { path = "../splatter" }`.
