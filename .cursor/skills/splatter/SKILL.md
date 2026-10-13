---
name: splatter
description: >-
  Splatter RF coverage engine: Fresnel knife-edge + FSPL rasters,
  LoRa decode thresholds, pairwise link checks, input hashing, Skadi DEM.
  Use when editing splatter/, SplatCoverageRequest, viewshed workspaces,
  preset RF mapping, link_mutual_*, schema version bumps, or coverage tests.
---

# Splatter

Read [MEMORY.md](../../MEMORY.md) and [`splatter/README.md`](../../../splatter/README.md) first.

**Splatter** is Peaky's RF engine — ITU-R P.526-style knife-edge diffraction + FSPL over Skadi SRTM. Outputs are SPLAT-shaped: `output.ppm`, `output.kml`, `splat.png`, `manifest.json`.

Peaky links the **library** in-process (`splatter::Session`). There is no CLI binary and no PyO3.

## Request contract

Propagation inputs are a **`SplatCoverageRequest`** JSON object (`request.json` in each workspace):

- Rust struct: `splatter::hash::Request` (serde `snake_case`)
- Preset → request: peaky-serve `rf.rs` (`rf_json`)

**`modem` is required** — splatter derives decode cutoff + reliability margin from LoRa params. Lat/lon affect terrain tile selection and raster placement only; identical RF params at different coords share the same **digest** (workspace folder name).

Limits: `radius` ≤ 100 km, `raster_dimension` 128–4096 px square.

## Input hash (cache key)

`splat_input_sha256` fingerprints normalized propagation params (terrain excluded). Used for `<preset>/.peaky/cache/viewsheds/<digest>/`.

Schema version lives in `splatter/src/hash.rs` → `SPLAT_CACHE_SCHEMA_VERSION`. Bump when normalization or coverage semantics change.

## Tests

```bash
cargo test -p splatter
```

## Rust layout

| Module | Role |
|--------|------|
| `engine.rs` | Coverage raster, SPLAT outputs |
| `propagate.rs` | Point-to-point link eval, mutual hop |
| `session.rs` | Shared DEM session |
| `peaks.rs` | GeoJSON polygon mask, binned local maxima |
| `peak_links.rs` | Ranked mutual-RF peak walk for seek |
| `skadi_fetch.rs` | Skadi S3 fetch-on-miss, mirror inventory |
| `hash.rs` | Request schema, normalize, `splat_input_sha256` |
| `lora.rs` | LoRa sensitivity, reliability margin |
| `dem.rs` | Skadi HGT mosaic |

## Additional resources

- Request fields and schema-bump checklist: [reference.md](reference.md)
- Peaky serve on-demand viewsheds: skill `peaky-serve`
- Docker image: skill `peaky-dev` (`Dockerfile` at repo root)
