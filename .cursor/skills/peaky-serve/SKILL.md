---
name: peaky-serve
description: >-
  Peaky web UI (`peaky serve`): progressive on-demand interface over shared CLI
  and pipeline logic. Use when adding serve routes, map overlays, or HTTP APIs
  that mirror build/bundle/mesh/viewshed capabilities.
---

# Peaky serve (web UI)

Read [MEMORY.md](../../MEMORY.md) § Web UI first.

## Model

**Progressive shell, shared engine.** The web UI reuses the same preset, build artifacts, and splatter pipeline as the CLI. The difference is *when* work runs:

- **CLI / `peaky build`** — orchestrates many units (all sites' viewsheds, full mesh, bundle clip) with DAG staleness and `-j` parallelism.
- **`peaky serve`** — triggers one unit per user action (toggle a site overlay, open a project map), usually via `GET` API.

Do not fork business logic in HTTP handlers. Add or call helpers in `serve_*.py` that wrap existing modules.

## Layout

| Module | Role |
|--------|------|
| `serve_cli.py` | stdlib `HTTPServer`, pages, API routes, MapLibre project map |
| `serve_viewshed.py` | On-demand RF viewshed PNG + MapLibre metadata |
| `new_cli.py` | Project discovery/scaffold (shared with CLI `peaky new`) |

Runtime roots: `PEAKY_HOME` (default `~/.peaky`), `PEAKY_PROJECTS` (default `<home>/projects`). Dev: `./peaky serve` mounts repo, `--reload` on package source.

## Viewshed pattern (reference)

1. Load preset from `<project>/config.yaml`.
2. Resolve workspace: `viewshed_workspace_digest` → `<preset>/build/viewsheds/<digest>/`.
3. If cached + digest matches → return `splat.png`.
4. Else → `run_viewshed_coverage` + `ensure_splat_raster_png` (same as CLI viewshed path).

API: `GET /api/p/<slug>/viewsheds/<site>` (JSON bounds + PNG URL), `GET …/splat.png`.

## Adding a feature

1. Identify the CLI/build function that already does the work.
2. Expose a thin `ensure_*` or `load_*` in `serve_<area>.py` with request-scoped inputs.
3. Wire one route in `serve_cli.py`; keep batching out unless the UI truly needs it.
4. Reuse build artifact paths so `peaky build` and serve share caches.

Rule: `.cursor/rules/serve-web-ui.mdc`.
