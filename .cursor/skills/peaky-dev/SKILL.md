---
name: peaky-dev
description: >-
  Peaky Docker dev workflow: ./peaky CLI runner, ./peaky-test pytest in
  peaky:dev image, bind mounts, SPLAT_CACHE. Use when running locally,
  verifying changes, or debugging Docker/test environment issues.
---

# Peaky dev

Read [MEMORY.md](../../MEMORY.md) for env vars.

## Run CLI

From a **project directory** (contains `config.yaml`):

```bash
cd projects/nevada
../../peaky build
../../peaky build --verbose -j 4
```

`./peaky` mounts `$PWD` as `/project`, bind-mounts `peaky_finders/`, runs `peaky` inside `peaky:dev`.

`./peaky serve` also mounts the repo at `/app`, sets `PEAKY_HOME=/app` and `PEAKY_PROJECTS=/app/projects`, and passes `--reload` (disable with `--no-reload`). Serve is the on-demand web shell over the same pipeline as CLI — see skill `peaky-serve`.

Build image: `./peaky --build` or set `PEAKY_DEV_IMAGE`.

## Run tests

**Always** from repo root:

```bash
./peaky-test                          # full suite
./peaky-test tests/test_http_pool.py  # targeted
./peaky-test -k mesh                  # filter
./peaky-test --build                  # rebuild image
```

Never `poetry run pytest` on the macOS host — bind-mounted `.venv` must stay Linux-only.

## Mounts

| Host | Container |
|------|-----------|
| `$PWD` (project dir) | `/project` (cwd for CLI) |
| `peaky_finders/` | `/app/peaky_finders` |
| `PEAKY_CACHE_DIR` (default `~/.peaky/splat_cache`) | `/.peaky/splat_cache` |
| Test fixtures projects | `/app/projects` (peaky-test only) |

## Env vars

| Var | Default | Role |
|-----|---------|------|
| `PEAKY_DEV_IMAGE` | `peaky:dev` | Docker image tag |
| `PEAKY_CACHE_DIR` | `~/.peaky/splat_cache` | Skadi tile cache on host |
| `PEAKY_HOME` | repo root | Runtime home in container |
| `SPLAT_CACHE` | `<peaky_home>/splat_cache` | Skadi mirror path |

## splatter submodule

Rust/PyO3 extension built into Docker image. GDAL + Poetry deps live in container only.
