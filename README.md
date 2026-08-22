# Peaky Finders v5

Pure Rust LoRa mesh site planner. Greenfield replacement for v4 (Python + Docker + PyO3 splatter).

## Build

```bash
cargo build --release
```

Binary: `target/release/peaky`

## Serve

```bash
export PEAKY_HOME=/path/to/peaky_home   # default: ../../ops/peaky_home when present
cargo run -p peaky -- serve --port 8080
```

Open `http://127.0.0.1:8080/` and pick a project (e.g. Nevada).

## Docker

```bash
docker build -t peaky:latest .
docker run --rm -p 8080:8080 \
  -v /path/to/peaky_home:/peaky_home \
  -v "${SPLAT_CACHE:-$HOME/.cache/splat}:/splat_cache" \
  peaky:latest
```

Image defaults: `PEAKY_HOME=/peaky_home`, `SPLAT_CACHE=/splat_cache`, `peaky serve --host 0.0.0.0 --port 8080`. Override the command for `find path`.

## Ops (BLM tags / FO export)

BLM inventory tagging and FO export live in **ops**, not this repo:

```bash
# from ops/
python3 peaky_home/scripts/tag_public_land.py --dry-run
python3 peaky_home/scripts/export_blm_fo_packet.py --fo blm-sierra-fo --dry-run
```

## Test

```bash
cargo test
```

## Layout

See [MEMORY.md](MEMORY.md) for architecture and agent contract.
