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
| `serve_cli.py` | Waitress WSGI runner, reload supervisor, CLI flags |
| `serve_app.py` | WSGI route dispatcher (pages + API) |
| `serve_viewshed.py` | On-demand RF viewshed PNG + MapLibre metadata |
| `serve_links.py` | On-demand mutual site links (`link_mutual_viable` / `link_mutual_batch`) |
| `new_cli.py` | Project discovery/scaffold (shared with CLI `peaky new`) |

Runtime roots: `PEAKY_HOME` (default `~/.peaky`), `PEAKY_PROJECTS` (default `<home>/projects`). Dev: `./peaky serve` mounts repo, `--reload` on package source.

## Viewshed pattern (reference)

1. Load preset from `<project>/config.yaml`.
2. Resolve workspace: `viewshed_workspace_digest` → `<preset>/build/viewsheds/<digest>/`.
3. If cached + digest matches → return `splat.png`.
4. Else → `run_viewshed_coverage` + `ensure_splat_raster_png` (same as CLI viewshed path).

API: `GET /api/p/<slug>/events` (SSE hub); `POST /api/p/<slug>/viewsheds/<site>/warm` (queue generation, `202` or cached `200`); `GET …/viewsheds/<site>` and `GET …/splat.png` read cache only; `viewshed` SSE events carry `queued` / `running` / `ready` / `error` with overlay metadata on `ready`.

## Site links pattern

1. Load preset; union preset `links` (manual) with viewshed mutual coverage.
2. `GET /links` returns warm cache when link fingerprint matches (site coords +
   sim radius/raster/modem/env/antenna heights + manual pairs) — **not** raw
   `config.yaml` mtime (renames/tags must not clear the mesh).
3. Cache miss → fast manual-only `pending` payload (no GPKG I/O on request thread)
   + background `warm_project_site_links` (parallel footprint read → SSE `ready`).
4. UI keeps the last ready GeoJSON while `pending` so lines do not flash away.

API: `GET /api/p/<slug>/links`, `POST …/links/warm`, `GET …/links/<a>/<b>`.

## Adding a feature

1. Identify the CLI/build function that already does the work.
2. Expose a thin `ensure_*` or `load_*` in `serve_<area>.py` with request-scoped inputs.
3. Wire one route in `serve_cli.py`; keep batching out unless the UI truly needs it.
4. Reuse build artifact paths so `peaky build` and serve share caches.

Rule: `.cursor/rules/serve-web-ui.mdc`.
