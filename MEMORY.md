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
| Reference preset | `peaky_home/projects/sample/config.yaml` (MeshEnvy: `$PEAKY_HOME` → `ops/peaky_home`) |

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
| Background warm | `serve/coverage_queue.py`, `serve/project_warm_scheduler.py`, `serve/link_footprints.py` |
| UI assets | `serve/static/` (`project-map.js`, …) |

**Server-owned warm:** On first project touch (`GET …/links`, SSE `…/events`), the scheduler enqueues all missing site footprints at background priority (100). Client never POSTs per-site warm storms; it **bumps priority** via `POST …/warm/priorities` for viewport (10) and selection (0). Overlays and link mesh updates arrive over SSE as workers drain the shared coverage queue. Link-adjacent slugs within hop range auto-bump to priority 20.

| Warm API | Role |
|----------|------|
| `POST /api/p/<slug>/warm/priorities` | Body `{slugs, priority}` — reorder queued footprint jobs |
| `GET …/viewsheds/<site>` | Cache hit returns overlay; 404 bumps priority 0 (no blocking compute) |
| `POST …/viewsheds/<site>/warm` | Deprecated → priority bump 0 |
| `GET …/links` | Cached/partial mesh instantly; starts background warm if needed |
| `POST …/links/warm` | Starts scheduler; optional `priority_slugs` in JSON body |

Client (`project-map.js`): `syncWarmPriorities()` on load, select, and debounced `moveend`; SSE drives overlay display (no poll loops).

On-demand cache under `<project>/.peaky/cache/viewsheds/`, `.peaky/cache/plss/`, and `.peaky/cache/land/`.

## BLM export (planned — Orlando 299/POD)

MeshEnvy ops drives this from `ops/initiatives/silver-triangle-backbone.md`. Sites carry `blm-{fo}` tags; each FO gets its own SF-299 + POD. Peaky supplies **GIS and site tables**, not narrative POD prose.

### Target API

| Route | Role |
|-------|------|
| `GET /api/p/<slug>/export/fo` | Query: `tag=blm-sierra` (required), optional `include=proposed,installed`, `fill_plss=1`. Response: zip download or JSON manifest with download URLs. |
| `POST /api/p/<slug>/sites/plss/bulk` | Body: `{slugs}` or `{tag}` — CadNSDI fill missing `plss`, write preset. |

### Export bundle (`export/fo`)

```
meshenvy-blm-sierra-sites.zip
  sites.csv          # slug,name,lat,lon,plss,height_m,tags
  sites.geojson
  sites.kml
  sites.shp          # (+ .shx .dbf .prj via pyogrio)
  qa-sensitive.csv   # optional P1: sites hitting exclude layers
```

CSV + shapefile satisfy Andrea's pre-app ask and Susan's "maps and shapefiles" requirement. GeoJSON/KML for internal QA and Google Earth Attachment 2 drafts.

### ADMU_NAME → tag map (Nevada)

| `ADMU_NAME` (BLM FO boundary layer) | Site tag |
|-------------------------------------|----------|
| Sierra Front Field Office | `blm-sierra` |
| Humboldt River Field Office | `blm-humboldt` |
| Black Rock Field Office | `blm-black-rock` |
| Tuscarora Field Office | `blm-tuscarora` |
| Tonopah Field Office | `blm-tonopah` |
| Caliente Field Office | `blm-caliente` |
| Las Vegas Field Office | `blm-las-vegas` |

Auto-tag (P1): point-in-polygon against `land.sources.blm-nv-field-office-boundary-polygons` / `admu_ofc_poly`.

### Not in Peaky

SF-299 PDF, POD Word templates, bylaws/EIN attachments, rent-waiver narrative — assembled manually from `ops/docs/` precedents.

Global defaults: `$PEAKY_HOME/config.yaml`, `modems.yaml`, `environments.yaml`. Project: `$PEAKY_HOME/projects/<slug>/config.yaml`.

Entity sidebar: **Sites | Land** tabs (`entityPanelTab` in `localStorage`). Sites panel: **+ Site** (manual add) and **Import** (KML/KMZ Point placemarks). **Bulk tag** adds/removes tags on sites currently listed in the sidebar (`POST …/sites/tags/bulk`); scope follows sidebar filters (**In view**, tag intersect/union). Open/closed state persists per project in `localStorage` (`peaky.map.v1.<slug>`). Sidebar **In view** toggle filters the site list to sites whose coords project inside the map canvas (client-side `map.project`, not geographic bounds — accurate with pitch; persists in map state). Row actions: eye (site visibility), droplet (viewshed), trash (delete). Multi-select tag filters with **intersect/union** mode (default **intersect** = AND); site rows always show all tags, with active filter tags highlighted. Import flow: file → `POST …/sites/import/preview` → in-modal MapLibre preview + scrollable point list with per-row **Import** toggle, **Select all** / **Clear all**, and **In view** filter; points within **100 m** of an existing site default skipped (gray on map); tags → `POST …/sites/import` with filtered `points[]` (`serve/kml_import.py`, `import_sites_to_preset`).

Land panel (v2): informational GDB overlays from `projects/<slug>/data/**/*.gdb` — no upload. **Import** modal: pick GDB, preview layers (full GDB — not AOI-clipped), per-layer **Attributes** (`role`, `labelField`, category exclude checkboxes, `styleField`, colors). Registers `land.sources.<id>.layers[]` (`name`, optional `role: aoi|include|exclude`, attribute `include`/`exclude`, `label_field`, `style_field`, `style`). **`role: aoi`** layers union to uber-AOI; serve GeoJSON for other layers clips to that boundary (lazy on `GET …/geojson`). Preview cache never clips; serve cache digest includes `aoi_digest`. AOI change purges clipped serve files; client reloads visible layers with per-row spinners. `include`/`exclude` roles reserved. Sidebar: user **folders** contain whole **sources** (layer config stays in `sources` only); **+ Folder**, collapse/rename/delete, folder eye, source drag-reorder/move; **Unfiled** for sources outside folders. Per-layer eye, label toggle, AOI badge, filter chips, legend.

| Sites API | Role |
|-----------|------|
| `GET/POST/PATCH/DELETE /api/p/<slug>/sites` | List / add / edit / delete |
| `POST …/sites/tags/bulk` | Merge tags on existing sites (`slugs`, `add_tags`, `remove_tags`) |
| `POST …/sites/import/preview` | Parse KML/KMZ; return `{points, skipped}` |
| `POST …/sites/import` | Write sites with shared `tags` |

| Land API | Role |
|-----------|------|
| `GET /api/p/<slug>/land` | List sources + layers; `sidebar`; `aoiDigest` |
| `GET …/land/data-gdbs` | GDB paths under `data/` |
| `POST …/land/import/preview` | Layer list + bbox for modal (`path`) |
| `GET …/land/import/preview/fields` | Layer attribute fields + row count |
| `GET …/land/import/preview/values` | Distinct field values (+ counts) |
| `POST …/land/import` | Register source (`path`, `layers[]` objects, optional `label`/`id`) |
| `PATCH …/land/sidebar` | Replace folder layout (`folders[]`, `unfiledSources[]`) |
| `PATCH …/land/sources/<id>` | Update `layers[]` / `label` |
| `DELETE …/land/sources/<id>` | Remove source + invalidate cache dir |
| `GET/POST …/land/preview/geojson` | Modal preview (`path`, `layer`; POST accepts filters/label/style) |
| `GET …/land/sources/<id>/layers/<layerKey>/geojson` | Lazy clipped serve GeoJSON; `X-Peaky-Digest` header |

## Repo layout

```
peaky_finders/src/peaky_finders/
  core/          # preset, RF, viewshed, links, plss, home templates
  serve/         # HTTP UI + static assets; serve/cli.py is the `peaky` entry
splatter/        # Rust/PyO3 RF engine
peaky_home/      # legacy local PEAKY_HOME (gitignored); MeshEnvy uses ../../ops/peaky_home
./peaky          # Docker runner: serve | test (auto-detects ops/peaky_home)
```

## Environment

| Var | Role |
|-----|------|
| `PEAKY_HOME` | Global config + projects (default `../../ops/peaky_home` when present, else `<repo>/peaky_home`) |
| `PEAKY_PROJECTS` | Projects root (default `<PEAKY_HOME>/projects`) |
| `PEAKY_SERVE_THREADS` | Waitress thread pool (default `16`) |
| `PEAKY_SERVE_COVERAGE_CONCURRENT` | Coverage queue worker count (default `1`) |
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
