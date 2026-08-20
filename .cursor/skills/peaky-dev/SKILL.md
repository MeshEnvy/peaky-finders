# Peaky v5 dev workflow

Use for local build, serve, and test — **no Docker wrapper**.

## Build

```bash
cd peaky-finders-v5
cargo build          # debug
cargo build --release
```

## Serve

```bash
export PEAKY_HOME=/Volumes/Code/repos/meshenvy/ops/peaky_home   # or rely on default
cargo run -p peaky -- serve --host 127.0.0.1 --port 8080
```

Skadi mirror: `$PEAKY_HOME/splat_cache` (or `SPLAT_CACHE` env).

## Test

```bash
cargo test
cargo test -p splatter
cargo test -p peaky-preset
```

## Ops (BLM — not in this repo)

```bash
# from ops/
python3 peaky_home/scripts/tag_public_land.py --project nevada --dry-run
python3 peaky_home/scripts/export_blm_fo_packet.py --project nevada --tag blm-sierra-fo --dry-run
```

## v4 reference

Python serve and batch logic live in `peaky-finders-v4/`. Port behavior from there; do not import or depend on v4 at runtime.
