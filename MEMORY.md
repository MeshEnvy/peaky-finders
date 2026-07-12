# peaky_finders v4 — project memory

Living snapshot of **current** architecture and repo state. **Agents: read this file before substantive work; update it in the same change set when anything below shifts.**

## Agent contract

1. **Read first** — Load MEMORY.md before tasks touching presets, serve/web UI, RF, or dev workflow.
2. **Update always** — If the task changes architecture, APIs, paths, or workflows, update MEMORY.md before marking done.
3. **Greenfield** — Prefer breaking simplifications. Delete dead paths; no shims or dual stacks.

## Status

| Item | State |
|------|-------|
| Repo | Greenfield — break freely |
| Domain | LoRa mesh site planning — splatter RF coverage, terrain links |
| Interface | **`peaky serve`** only — progressive web UI over preset YAML |
| RF engine | `splatter` submodule (PyO3 Fresnel/FSPL) |
| Dev/test | Docker — `./peaky serve`, `./peaky test` |
| Reference preset | `peaky_home/projects/sample/config.yaml` |

## Domain model: sites

All map points under **`sites:`** (slug → `name`, `loc: [lat, lon]`, optional `tags`, `elevation_m`, …).

- **No `type` field** — rejected on load (`sites.<slug>.type is removed; use tags`).
- **No goals / repeaters split** — every site gets viewsheds and P2P link checks.
- **`tags`** — optional lowercase labels for serve UI filtering only (e.g. `installed`, `eip`); no RF semantics.

Manual mutual pairs: top-level `links: [[a, b], …]`.

Rule: `.cursor/rules/sites-and-tags.mdc`. Skill: `peaky-preset`.

## Commands

| Command | Role |
|---------|------|
| `./peaky serve` | Local web UI — MapLibre map, on-demand viewsheds/links, preset editor |
| `./peaky test` | Pytest in `peaky:dev` Docker image |

Entry: `peaky_finders.serve.cli:main` (`peaky` or `peaky serve`).

## Web UI (`peaky serve`)

Progressive shell over **`core/`** + **`serve/`** — not a batch build product.

| Layer | Modules |
|-------|---------|
| CLI / WSGI | `serve/cli.py`, `serve/app.py` |
| Preset | `core/preset/` — slim YAML (simulation, display, sites, links) |
| RF / viewshed | `core/rf/`, `core/viewshed/`, splatter |
| Links | `core/links/` — mutual footprint + RF |
| UI assets | `serve/static/` (`project-map.js`, …) |

On-demand cache under `<project>/.peaky/cache/viewsheds/` and `.peaky/cache/plss/`.

Global defaults: `$PEAKY_HOME/config.yaml`, `modems.yaml`, `environments.yaml`. Project: `$PEAKY_HOME/projects/<slug>/config.yaml`.

Map UI state (sites panel open/closed, viewport, basemap, filters) persists in `localStorage` per project (`peaky.map.v1.<slug>`).

## Repo layout

```
peaky_finders/src/peaky_finders/
  core/          # preset, RF, viewshed, links, plss, home templates
  serve/         # HTTP UI + static assets; serve/cli.py is the `peaky` entry
splatter/        # Rust/PyO3 RF engine
peaky_home/      # PEAKY_HOME (sample preset committed)
./peaky          # Docker runner: serve | test
```

## Environment

| Var | Role |
|-----|------|
| `PEAKY_HOME` | Global config + projects (default `<repo>/peaky_home`) |
| `PEAKY_PROJECTS` | Projects root (default `<PEAKY_HOME>/projects`) |
| `SPLAT_CACHE` / `PEAKY_CACHE_DIR` | Skadi DEM mirror |
| `PEAKY_DEV_IMAGE` | Docker tag (default `peaky:dev`) |

## Removed (do not reintroduce)

- Batch CLI: `build`, `bundle`, `mesh`, `viewshed`, `kmz`, `stamp`, `inspect`
- Build DAG, bundle clips, eligible land pipeline, aggregate KMZ
- `site_suggestions/`, `peaky build --suggest`, corridor / mesh-backbone planner
- `sites_job.py` fat preset (`land`, `mesh`, `suggest`, `SiteType`)
- Goals API / UI, site `type` field

## Agent context

| Skill / rule | Topic |
|--------------|-------|
| `peaky-serve` | Web UI routes and patterns |
| `peaky-preset` | `config.yaml`, tags-only sites |
| `peaky-dev` | `./peaky`, Docker |
| `peaky-architecture` | This doc + greenfield posture |
| `memory-maintenance` | Read/update MEMORY |
| `sites-and-tags` | Site YAML vocabulary |
| `peaky-test` | `./peaky test` only |
