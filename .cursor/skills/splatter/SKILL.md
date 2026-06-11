---
name: splatter
description: >-
  Splatter RF coverage engine (Rust/PyO3): Fresnel knife-edge + FSPL rasters,
  LoRa decode thresholds, pairwise link checks, input hashing, Skadi DEM.
  Use when editing splatter/, SplatCoverageRequest, viewshed workspaces,
  preset RF mapping, link_mutual_*, schema version bumps, or coverage tests.
---

# Splatter

Read [MEMORY.md](../../MEMORY.md) and [`splatter/README.md`](../../../splatter/README.md) first.

**Splatter** is Peaky's RF engine — ITU-R P.526-style knife-edge diffraction + FSPL over Skadi SRTM. Outputs are SPLAT-shaped: `output.ppm`, `output.kml`, `splat.png`, `manifest.json`.

Peaky uses the **PyO3 extension** in-process (`get_session`); the Rust **CLI** is for container/debug runs only.

## Interfaces

| Interface | When |
|-----------|------|
| **PyO3** (`splatter.get_session`) | Peaky build, serve, suggest — shared resident DEM |
| **CLI** (`splatter run` / `run-batch`) | Docker `splatter:latest`, `test-los.sh`, isolated debugging |

Build extension: `maturin develop --release` in `splatter/`, or rebuild `peaky:dev` (`./peaky --build`). Never `poetry run pytest` on macOS host for coverage tests — use `./peaky test`.

## Request contract

Propagation inputs are a **`SplatCoverageRequest`** JSON object (`request.json` in each workspace):

- Python model: `peaky_finders.models.SplatCoverageRequest`
- Rust struct: `splatter::hash::Request` (serde `snake_case`)
- Preset → request: `preset_mapping.preset_to_request()` (modem/environment catalogs → threshold, clutter, Fresnel, radius, raster)

**`modem` is required** — splatter derives decode cutoff + reliability margin from LoRa params. Lat/lon affect terrain tile selection and raster placement only; identical RF params at different coords share the same **digest** (workspace folder name).

Limits (enforced in Rust + Python normalize): `radius` ≤ 100 km, `raster_dimension` 128–4096 px square.

## Input hash (cache key)

`splat_input_sha256` fingerprints normalized propagation params (terrain excluded). Used for:

- `<preset>/build/viewsheds/<digest>/` workspace dirs (`viewshed_workspace.py`)
- Build staleness / manifest `splat_input_sha256`
- Golden contract: `splatter/tests/fixtures/splat_request_hash_fixture.json`

Schema version **7** — keep in sync:

- `splatter/src/hash.rs` → `SPLAT_CACHE_SCHEMA_VERSION`
- `peaky_finders/splat_input_hash.py` → `SPLAT_CACHE_SCHEMA_VERSION`
- Rust + Python golden tests (`test_peaky_los_input_hash.py`, `test_coverage_binaries.py`)

**Bump schema** when normalization or coverage semantics change; invalidate viewshed caches (`peaky build --force`). Greenfield: no migration of old digests.

## Python integration

| Module | Role |
|--------|------|
| `splat_pipeline.py` | `run_splatter_site`, `run_splatter_batch`, footprint vectorize |
| `viewshed_batch.py` | Array `request.json` at viewsheds root → one batch run |
| `viewshed_workspace.py` | Digest → workdir path |
| `preset_mapping.py` | Preset RF → `SplatCoverageRequest` |
| `splat_input_hash.py` | Host-safe hash wrapper (delegates to Rust) |
| `site_suggestions/rf_link.py` | `link_mutual_viable` / `link_mutual_batch` for mesh/suggest/serve |

Typical coverage flow:

```python
from splatter import get_session

session = get_session(mirror_root=str(skadi_mirror), verbose=True)
session.run("/path/to/workspace")  # request.json inside
session.run_batch("/viewsheds/root", batch_jobs=8, requests_json=open("batch.json").read())
```

Pairwise hops (same physics as raster, no viewshed):

```python
session.ensure_tiles_for_points([(lat, lon), ...], buffer_m=5000.0)
session.link_mutual_viable(lat_a, lon_a, lat_b, lon_b, rf_json)
session.link_mutual_batch(pairs, rf_json)  # rayon parallel inside Rust
```

`rf_json` = `preset_to_request(...).model_dump_json()` — position fields ignored for link eval; `radius` caps max hop range.

## Outputs & downstream

Per workspace after `run`:

| File | Purpose |
|------|---------|
| `output.ppm` | Raw RGB coverage raster |
| `splat.png` | PNG for KML overlay (Python post-step in `splat_pipeline`) |
| `output.kml` | Ground overlay LatLonBox |
| `manifest.json` | `bbox`, `splat_input_sha256`, schema version |
| `splat.gpkg` / `splat.kml` | Footprint polygons (Python vectorize, not Rust) |

Batch mode writes `<digest>/` subdirs under the work root (one per request).

## Parallelism

Individual splatter viewsheds are **cheap** (~<0.5s) — parallelize at **site/batch/wave** level, not micro-batches of one viewshed.

| Knob | Location |
|------|----------|
| `simulation.max_workers.splatter` | Preset YAML |
| `SPLATTER_BATCH_JOBS` | Env override inside one `run-batch` |
| `resolved_splatter_batch_jobs()` | Caps workers to workspace count |

Footprint vectorize uses separate `ProcessPoolExecutor` in Python (`vectorize_coverage_footprints_parallel`).

## Tests

```bash
cd splatter && cargo test
./peaky test tests/test_peaky_los_input_hash.py tests/test_coverage_binaries.py
./peaky test tests/test_viewshed_batch.py -k splatter
```

CLI smoke: `splatter/test-los.sh` (Docker, needs workspace with `request.json`).

## Rust layout

| Module | Role |
|--------|------|
| `engine.rs` | Coverage raster, batch orchestration, SPLAT outputs |
| `propagate.rs` | Point-to-point link eval, mutual hop |
| `session.rs` | Shared DEM session (PyO3 + library) |
| `hash.rs` | Request schema, normalize, `splat_input_sha256` |
| `lora.rs` | LoRa sensitivity, reliability margin |
| `dem.rs` | Skadi HGT mosaic |
| `ray_cache.rs` | Terrain profiles along rays |
| `python.rs` | PyO3 bindings (`splatter._core`) |

## Additional resources

- Request fields, CLI flags, schema-bump checklist: [reference.md](reference.md)
- Peaky build/viewshed DAG: skill `peaky-build`
- Preset RF catalogs: skill `peaky-preset`
- Dev Docker / `SPLAT_CACHE`: skill `peaky-dev`
