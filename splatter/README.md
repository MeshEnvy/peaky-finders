# splatter (v5)

Fresnel-aware knife-edge diffraction (ITU-R P.526) + FSPL coverage raster. Drop-in SPLAT-shaped outputs: `output.ppm`, `output.kml`, `splat.png`, `manifest.json`.

Library crate linked in-process by `peaky` (`Session` + resident Skadi DEM mosaic). No CLI binary, no Python bindings.

## Build / test

```bash
cargo test -p splatter
```

Golden hash fixture: `tests/fixtures/splat_request_hash_fixture.json`.
