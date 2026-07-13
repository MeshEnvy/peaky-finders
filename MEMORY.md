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

All map points under **`sites:`** (slug → `name`, `loc: [lat, lon]`, optional `tags`, `height_m`, …).

- **`height_m`** — optional antenna AGL (m); overrides `simulation.transmitter.height_m` for that site's viewshed TX height.
- **`elevation_m`** — removed; terrain at `loc` comes from Skadi DEM in splatter.

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
| Preset | `core/preset/` — slim YAML (simulation, display, sites, links, land) |
| RF / viewshed | `core/rf/`, `core/viewshed/`, splatter |
| Links | `core/links/` — mutual footprint + RF |
| UI assets | `serve/static/` (`project-map.js`, …) |

On-demand cache under `<project>/.peaky/cache/viewsheds/`, `.peaky/cache/plss/`, and `.peaky/cache/land/`.

Global defaults: `$PEAKY_HOME/config.yaml`, `modems.yaml`, `environments.yaml`. Project: `$PEAKY_HOME/projects/<slug>/config.yaml`.

Entity sidebar: **Sites | Land** tabs (`entityPanelTab` in `localStorage`). Sites panel: **+ Site** (manual add) and **Import** (KML/KMZ Point placemarks). **Bulk tag** adds/removes tags on sites currently listed in the sidebar (`POST …/sites/tags/bulk`); scope follows sidebar filters (**In view**, tag intersect/union). Open/closed state persists per project in `localStorage` (`peaky.map.v1.<slug>`). Sidebar **In view** toggle filters the site list to sites whose coords project inside the map canvas (client-side `map.project`, not geographic bounds — accurate with pitch; persists in map state). Row actions: eye (site visibility), droplet (viewshed), trash (delete). Multi-select tag filters with **intersect/union** mode (default **intersect** = AND); site rows always show all tags, with active filter tags highlighted. Import flow: file → `POST …/sites/import/preview` → in-modal MapLibre preview + scrollable point list with per-row **Import** toggle, **Select all** / **Clear all**, and **In view** filter; points within **100 m** of an existing site default skipped (gray on map); tags → `POST …/sites/import` with filtered `points[]` (`serve/kml_import.py`, `import_sites_to_preset`).

Land panel (v2): informational GDB overlays from `projects/<slug>/data/**/*.gdb` — no upload, no AOI/eligible pipeline. **Import** modal: pick GDB, preview layers, per-layer **Attributes** panel (`labelField`, per-category **exclude** checkboxes on distinct values, `styleField`, per-value colors). Registers structured `land.sources.<id>.layers[]` entries (`name`, optional `include`/`exclude`, `label_field`, `style_field`, flat or per-value `style`) in preset. GeoJSON features carry `properties.label` and `properties.style_key`; main map uses MapLibre `match` on `style_key` when styled. Sidebar: friendly titles from optional source `label` or GDB filename (strips trailing numeric ids); one row per single-layer source (eye + label + edit + delete); multi-layer sources group under a header. Filter include/exclude as meta chips; styled layers show legend swatches. Per-layer eye toggles (`landVisible` in map state); per-layer label toggles (`landLabelsVisible`, default on when `label_field` set). Cache digest includes filters + label/style fields (`serve/land.py`, `serve/land_import.py`).

| Sites API | Role |
|-----------|------|
| `GET/POST/PATCH/DELETE /api/p/<slug>/sites` | List / add / edit / delete |
| `POST …/sites/tags/bulk` | Merge tags on existing sites (`slugs`, `add_tags`, `remove_tags`) |
| `POST …/sites/import/preview` | Parse KML/KMZ; return `{points, skipped}` |
| `POST …/sites/import` | Write sites with shared `tags` |

| Land API | Role |
|----------|------|
| `GET /api/p/<slug>/land` | List registered sources + layer entries (`key`, filters, style) |
| `GET …/land/data-gdbs` | GDB paths under `data/` |
| `POST …/land/import/preview` | Layer list + bbox for modal (`path`) |
| `GET …/land/import/preview/fields` | Layer attribute fields + row count |
| `GET …/land/import/preview/values` | Distinct field values (+ counts) |
| `POST …/land/import` | Register source (`path`, `layers[]` objects, optional `label`/`id`) |
| `PATCH …/land/sources/<id>` | Update `layers[]` / `label` |
| `DELETE …/land/sources/<id>` | Remove source + invalidate cache dir |
| `GET/POST …/land/preview/geojson` | Modal preview (`path`, `layer`; POST accepts filters/label/style) |
| `GET …/land/sources/<id>/layers/<layerKey>/geojson` | Cached WGS84 FeatureCollection with attrs |

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
- Build DAG, bundle clips, **eligible land pipeline** (AOI ∩ include − exclude), aggregate KMZ
- `site_suggestions/`, `peaky build --suggest`, corridor / mesh-backbone planner
- `sites_job.py` fat preset (`mesh`, `suggest`, `SiteType`)
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
