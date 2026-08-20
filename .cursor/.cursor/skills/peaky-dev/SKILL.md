---
name: peaky-dev
description: >-
  Peaky Docker dev workflow: ./peaky serve and ./peaky test in peaky:dev image.
  Use when running locally or debugging Docker/test issues.
---

# Peaky dev

Read [MEMORY.md](../../MEMORY.md) for env vars.

## Commands

```bash
./peaky serve                  # fast start: skips rebuild when splatter unchanged
./peaky serve --rebuild-splatter   # force Rust recompile
./peaky serve --no-reload
./peaky test                   # pytest in container
./peaky test --build           # rebuild Docker image (Dockerfile / lockfile changes)
./peaky run python3 …          # ops scripts (skips splatter)
```

Never `poetry run pytest` on the macOS host.

## What runs when

| Action | When |
|--------|------|
| **`./peaky --build`** | Layered image build: `cargo fetch` → splatter wheel → `poetry install` (cached per lockfile / Cargo.lock) |
| **`./peaky serve`** (normal) | Stamp check → skip splatter if sources unchanged; `pip install -e peaky_finders` only |
| **`./peaky serve --rebuild-splatter`** | Force maturin rebuild (uses persisted cargo + target caches) |
| **`./peaky test`** | Poetry dev deps once per lockfile stamp; splatter rebuild only if Rust sources changed |

## Host caches (`~/.peaky/dev-cache/`)

| Path | Role |
|------|------|
| `cargo-registry/` | Downloaded Rust crates (survives container restarts) |
| `cargo-git/` | Cargo git checkouts |
| `splatter-target/` | Incremental Rust build artifacts |
| `build-stamps/` | Source-hash stamps — skip redundant installs |

DEM mirror stays in `PEAKY_CACHE_DIR` (default `~/.peaky/splat_cache`).

## Mounts

| Host | Container |
|------|-----------|
| `peaky_home/` | `/.peaky` (`PEAKY_HOME`) |
| `peaky_finders/` | `/app/peaky_finders` |
| `splatter/` | `/app/splatter` |
| `PEAKY_CACHE_DIR` | `/.peaky/splat_cache` |

Serve publishes `PEAKY_SERVE_PORT` (default 8080).
