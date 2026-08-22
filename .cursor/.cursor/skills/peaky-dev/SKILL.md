---
name: peaky-dev
description: >-
  Peaky v5 host cargo workflow and Docker image. Use when running locally
  or packaging peaky to run in a container.
---

# Peaky v5 dev workflow

Read [MEMORY.md](../../MEMORY.md) for env vars.

## Host

```bash
cargo build
cargo build --release
export PEAKY_HOME=/Volumes/Code/repos/meshenvy/ops/peaky_home   # optional slug fallback only
cargo run -p peaky -- serve /Volumes/Code/repos/meshenvy/peaky-nevada --host 127.0.0.1 --port 8080
cargo test
```

Skadi mirror: `<project>/.peaky/cache/skadi`. Optional `SPLAT_CACHE` override. Use `--release` only for long RF runs.

## Docker

```bash
docker build -t peaky:latest .
docker run --rm -p 8080:8080 \
  -v /path/to/peaky-nevada:/project \
  peaky:latest
```

Image `peaky serve /project`. Skadi cache persists in the project mount. Extra args replace `serve` (e.g. `find path --help`).

## Ops (BLM — not in this repo)

```bash
# from ops/
python3 peaky_home/scripts/tag_public_land.py --project nevada --dry-run
python3 peaky_home/scripts/export_blm_fo_packet.py --project nevada --tag blm-sierra-fo --dry-run
```

## v4 reference

Python serve and batch logic live in `peaky-finders-v4/`. Port behavior from there; do not import or depend on v4 at runtime.
