# splatter (v5)

Fresnel-aware knife-edge diffraction (ITU-R P.526) + FSPL coverage raster. Drop-in SPLAT-shaped outputs: `output.ppm`, `output.kml`, `splat.png`, `manifest.json`.

In v5 this is a **Rust library crate** linked in-process by `peaky serve` (`Session` + resident Skadi DEM mosaic). No Python bindings.

## Build

```bash
cargo build -p splatter
```

## Optional CLI

```bash
cargo build --release -p splatter --bin splatter
./target/release/splatter --help
```

## Run (CLI)

Reads `request.json` in a work directory and writes SPLAT-shaped artifacts there. See `src/main.rs` for flags.

## Tests

```bash
cargo test -p splatter
```

Golden hash fixture: `tests/fixtures/splat_request_hash_fixture.json`.
