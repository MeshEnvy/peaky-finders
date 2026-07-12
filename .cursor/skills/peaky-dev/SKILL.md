---
name: peaky-dev
description: >-
  Peaky Docker dev workflow: ./peaky (test, serve, build, …) in peaky:dev image,
  bind mounts, SPLAT_CACHE. Use when running locally, verifying changes, or
  debugging Docker/test environment issues.
---

# Peaky dev

Read [MEMORY.md](../../MEMORY.md) for env vars.

## Run CLI

From a **project directory** (contains `config.yaml`):

```bash
cd peaky_home/projects/sample
../../../peaky serve
```

`./peaky` mounts `<repo>/peaky_home` → `/.peaky` (`PEAKY_HOME`), `$PWD` as `/project`, bind-mounts `peaky_finders/`, runs the Python CLI inside `peaky:dev`.

`./peaky serve` uses the same `PEAKY_HOME` mount and passes `--reload` by default (disable with `--no-reload`). Serve is the on-demand web shell over the same pipeline as CLI — see skill `peaky-serve`.

Build image: `./peaky --build` or set `PEAKY_DEV_IMAGE`.

## Run tests

**Always** from repo root:

```bash
./peaky test                          # full suite
./peaky test tests/test_http_pool.py  # targeted
./peaky test -k mesh                  # filter
./peaky test --build                  # rebuild image
```

Never `poetry run pytest` on the macOS host — bind-mounted `.venv` must stay Linux-only.

## Mounts

| Host | Container |
|------|-----------|
| `$PWD` (project dir) | `/project` (cwd for CLI) |
| `peaky_home/` (or `PEAKY_HOME`) | `/.peaky` |
| `peaky_finders/` | `/app/peaky_finders` |
| `PEAKY_CACHE_DIR` (default `~/.peaky/splat_cache`) | `/.peaky/splat_cache` |
| Test fixtures `peaky_home/` | `/.peaky` (`./peaky test` only) |

## Env vars

| Var | Default | Role |
|-----|---------|------|
| `PEAKY_DEV_IMAGE` | `peaky:dev` | Docker image tag |
| `PEAKY_CACHE_DIR` | `~/.peaky/splat_cache` | Skadi tile cache on host |
| `PEAKY_HOME` | `<repo>/peaky_home` | Runtime home in container (`/.peaky`) |
| `SPLAT_CACHE` | `<peaky_home>/splat_cache` | Skadi mirror path |

## splatter submodule

Rust/PyO3 extension built into Docker image. GDAL + Poetry deps live in container only.
