---
name: peaky-dev
description: >-
  Peaky v5 host cargo workflow and Docker image. Use when running locally,
  packaging peaky in a container, or developing the Rust workspace.
---

# Peaky v5 dev workflow

Read [MEMORY.md](../../MEMORY.md) for env vars and architecture.

For publishing releases, use skill `peaky-release`.

## Host

```bash
cargo build
cargo build --locked --release -p peaky
cargo run -p peaky -- serve /path/to/project --host 127.0.0.1 --port 8080
cargo test --locked
```

Skadi mirror: `<project>/.peaky/cache/skadi`. Optional `SPLAT_CACHE` override. Use `--release` only for long RF runs.

Empty project dirs auto-init with MeshCore defaults on first `serve` (`crates/peaky-preset/src/init.rs`).

## Docker

```bash
docker build -t peaky:latest .
docker run --rm -p 8080:8080 \
  -v /path/to/project:/project \
  peaky:latest
```

Image default: `peaky serve /project`. Skadi cache persists in the project mount. Extra args replace `serve` (e.g. `find path --help`).

## v4 reference

Python serve and batch logic live in `peaky-finders-v4/`. Port behavior from there; do not import or depend on v4 at runtime.
