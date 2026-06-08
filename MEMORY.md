# peaky_finders v4 — project memory

Living snapshot of **current** architecture and repo state. **Agents: read this file before substantive work; update it in the same change set when anything below shifts.**

## Agent contract

1. **Read first** — Load MEMORY.md at the start of any task that touches presets, build, CLI, serve/web UI, bundle, mesh, viewsheds, or dev workflow. Treat it as source of truth over stale chat or assumed v3/v2 behavior.
2. **Update always** — If the task changes architecture, APIs, paths, flags, or workflows, update MEMORY.md before marking done. Remove obsolete rows; do not append without pruning.
3. **Greenfield** — Prefer breaking simplifications over compatibility. Delete old paths; invalidate caches; rename freely. No shims, migrations, dual code paths, or deprecation periods unless the user explicitly requests one narrow exception.

## Greenfield posture

| Do | Don't |
|----|-------|
| Replace old design with the cleaner one in one change set | Keep the old path "just in case" |
| Invalidate `build/` caches and on-disk formats when layout changes | Migration scripts for local artifact dirs |
| Update callers + MEMORY + tests together | Feature flags branching on old behavior |
| Remove dead code when refactoring | `@deprecated` wrappers or compat aliases |
| Break preset/CLI shapes when the new shape is simpler | Support legacy `.json` or old YAML keys |

When two designs compete, pick the **simpler present** and break callers — document impact in the commit, not in long compat layers.

## Status

| Item | State |
|------|-------|
| Repo | Greenfield — aggressive breaking changes OK |
| Domain | LoRa mesh site planning — splatter RF coverage, terrain mesh, eligible land |
| Interface | **CLI** from project dir (`config.yaml` in cwd); **`peaky serve`** progressive web UI (on-demand over shared pipeline) |
| RF engine | `splatter` submodule (PyO3 Fresnel/FSPL) |
| Build | Incremental preset DAG (`peaky build`) |
| Dev/test | Docker — `./peaky`, `./peaky-test` |
| Reference preset | `projects/nevada/config.yaml` |

## Domain model: sites vs goals

**Sites** are repeater locations — each gets a splatter RF **viewshed**. Preset key: `sites:` (slug → entry with `loc: [lat, lon]`).

| `type` | Role | Position |
|--------|------|----------|
| `installed` | Deployed repeater | **Fixed** — must not move |
| `planned` | User-committed future site (e.g. serve **Add site**) | **Fixed** — must not move |
| `suggested` | Output of `peaky build --suggest` | **May move** — re-suggest may replace with a better pick |

Build, mesh, and serve treat every site type as a coverage source. Suggest **never relocates** `installed` or `planned` sites; only `type: suggested` entries are removed/replaced (`--replace-suggested`).

**Goals** are map points where coverage is **desired** — planner **attractors**, not repeaters. No viewshed until a site is placed. Preset key: top-level `goals:` (slug → `name`, `loc: [lat, lon]`); solver sequencing via `suggest.mesh_backbone.goal_order`. Ephemeral `bridge:*` goals appear during connectivity healing. **Captured** when a repeater footprint covers the point **and** mutual RF hop is viable.

| Concept | Sites | Goals |
|---------|-------|-------|
| What | Repeaters (actual or candidate) | Coverage targets |
| Viewshed | Yes (per site) | No |
| Who writes | User (`installed`/`planned`) or suggest (`suggested`) | User in preset YAML |
| Solver use | Seeds, constraints, mesh nodes | What to capture / grow toward |

Rule: `.cursor/rules/sites-and-goals.mdc`. Skill detail: `peaky-preset`.

## CLI commands

Run from a project directory containing `config.yaml`:

```bash
../../peaky new my-region       # scaffold projects/my-region/ (from repo root)
cd projects/nevada
../../peaky build              # incremental DAG (bundle → viewsheds → mesh → KMZ)
../../peaky build --suggest    # site solver → append to preset YAML
../../peaky bundle clip aoi …  # granular Make-style targets
```

| Command | Role |
|---------|------|
| `new` | Scaffold project: `config.yaml` + `data/` layout under `PEAKY_PROJECTS` |
| `build` | Incremental preset DAG; `--target`, `-j`, `--force`, `--dry-run`, `--suggest` |
| `bundle` | Granular: `clip`, `composite`, `eligible`, `reference`, `resolve`, `plss`, `dem` |
| `mesh` | `links`, `pairwise`, `depth`, `eligible-union` |
| `viewshed` | Single-site splatter coverage workspace |
| `kmz` | Aggregate KMZ assembly |
| `stamp` | Prerequisite stamp files for staleness edges |
| `inspect` | GDB layer/attribute listing |
| `serve` | Local web UI — Bootstrap 5 dark theme; project selector at `PEAKY_HOME`; MapLibre map with RF viewshed overlays (Street/Topo/Satellite, 3D terrain on tilt). Toolbar **Viewshed** opacity slider (0–100%) adjusts all viewshed raster layers globally. RF site-link lines show haversine distance labels at line center (km, one decimal); toggled with **Site links** checkbox. Goal markers (orange) and goal↔repeater RF link lines (toggled **Goal links**); captured goals styled darker when footprint + RF both hold. Click site → floating info panel (coords, elevation, PLSS/MLRS, description, rationale, linked sites, per-site viewshed toggle). Click goal → panel lists RF-viable / captured repeaters (no viewshed). **+ Add** dropdown → Site (repeater) or Goal (coverage target) placement mode → click map → draft marker + prefetch (viewshed + site links for sites; goal↔repeater links for goals) → create panel → save site `POST …/sites` or goal `POST …/goals`. Compass resets to north-up 2D and fits all sites/goals with 30 km padding. Per-browser map prefs (camera, basemap, site links, goal links, viewshed opacity) in `localStorage` keyed by project slug (`peaky.map.v1.<slug>`); first visit fits sites + goals. |

Entry: `peaky_finders.peaky_cli:main`. **`serve`**: stdlib `HTTPServer`, `--reload` polls `peaky_finders` + `serve_static/` (on by default via `./peaky serve`), Docker publishes `PEAKY_SERVE_PORT` (default 8080). UI in `serve_html.py`; assets at `/static/` (`app.css`, `project-map.js`) plus root favicons/manifest in `serve_static/`. Project map loads all viewsheds on open; panel toggles hide/show per site. APIs: `GET /api/p/<slug>/sites` (metadata incl. `elevation_m`, `plss`, `mlrs`, …); `POST /api/p/<slug>/sites` JSON `{name, lat, lon}` → append planned site (`serve_sites.py`), applies cached PLSS/MLRS when loc cache hit; `GET /api/p/<slug>/goals`; `POST /api/p/<slug>/goals` JSON `{name, lat, lon}` → append goal (`serve_goals.py`); `GET /api/p/<slug>/goal-links`; `GET /api/p/<slug>/goals/prefetch?lat=&lon=` → goal↔repeater link preview; `GET /api/p/<slug>/sites/prefetch?lat=&lon=` → `{plss, mlrs, links[{slug, linked, distance_km}], links_geojson}` for add-site coordinate pick; `GET /api/p/<slug>/viewsheds/prefetch?lat=&lon=` → draft overlay JSON (bounds + PNG URL); `GET …/prefetch/splat.png?lat=&lon=`; `GET /api/p/<slug>/viewsheds/<site>` (JSON bounds + PNG URL) and `GET …/splat.png`; site links `GET /api/p/<slug>/links` and `GET /api/p/<slug>/links/<a>/<b>`.

## Web UI (`peaky serve`)

Progressive, on-demand shell over the **same** preset + splatter + build artifacts as the CLI — not a forked product.

| Aspect | CLI / build | Web |
|--------|-------------|-----|
| Scheduling | Batch (`peaky build`, DAG waves, `-j`) | Per HTTP request (user toggles map layer, opens project) |
| Viewsheds | All sites via build graph / `run_viewshed_batch` | One site via `ensure_site_viewshed_png` → `run_viewshed_coverage` |
| Artifacts | `<preset>/build/…` | Same paths; serve reads cache if build already ran |
| HTTP layer | — | Thin: `serve_cli.py` routes + `serve_html.py` + MapLibre; logic in `serve_*.py` |
| Site links | Build mesh `links` KML + footprint mutual coverage | On-demand splatter `link_mutual_*` via `serve_links.py` |

Map UI prefs (camera, basemap, site links) persist in browser `localStorage` per slug — not in `config.yaml` (avoids multi-user clobber). Agent rule: `.cursor/rules/serve-web-ui.mdc`; skill: `peaky-serve`.

## Build DAG

```
config.yaml → load Preset → configure_preset_build → build_target_graph
  → topo_sort → wave-parallel executor (-j) → skip fresh (stamps/mtimes)
```

| Module | Role |
|--------|------|
| `build_configure.py` | Plan clips, composites, viewshed workspaces |
| `build_graph.py` | Target IDs, dependency edges, subgraph filtering |
| `build_executor.py` | Run waves in parallel; invoke bundle/mesh/viewshed/KMZ nodes |
| `build_fresh_checks.py` | Staleness: skip unless `--force` |
| `build_stamp_inputs.py` | Stamp prerequisite paths |

Outputs under `<preset-dir>/build/` (clips, bundle, viewsheds, mesh, aggregate KMZ).

## Preset schema

One YAML per project — `projects/<slug>/config.yaml` (legacy `.json` unsupported).

| Section | Contents |
|---------|----------|
| `simulation` | `provider: splatter` (Fresnel/FSPL engine), RF/modem/env presets, `max_workers.splatter` batch fan-out |
| `display` | Viewshed raster (`colormap`, `transparency`, dBm range) + `kml` styles + `kmz` layer toggles |
| `land` | GDB inputs: `inputs_root`, `reference`, `aoi`, `include`, `exclude` |
| `mesh` | Pairwise/depth build knobs, raster size, worker counts |
| `suggest` | Site planner (`land-grab` / `mesh-backbone`); `mesh_backbone.goal_order` sequences top-level `goals:` |
| `goals` | Coverage attractors — `name`, `loc: [lat, lon]`; slug namespace disjoint from `sites:` |
| `sites` | Repeaters with viewsheds — `installed` / `planned` (fixed) / `suggested` (replaceable) |
| `links` | Manual mutual site pairs `[[a, b], …]` (field-verified; unioned with viewshed mutual coverage) |

CLI `peaky bundle` unchanged; artifact dir remains `<preset>/build/bundle/`. Build stamps: `land_*`, `display_kml`, `display_kmz`, `mesh` (replaces `bundle_*` stamp names).

Model: `Preset` in `sites_job.py`. Writes: `update_preset_yaml_tree` (ruamel round-trip, file lock).

## Repo layout

```
peaky_finders/     # Python package (Poetry, src layout)
splatter/          # Git submodule — Rust/PyO3 coverage engine
projects/          # Job dirs (gitignored except config.yaml)
  nevada/          # Reference project
docker/            # Entrypoint script
Dockerfile         # dev + latest targets (GDAL, Poetry, splatter)
peaky              # Dev runner: Docker + bind-mount project dir
peaky-test         # Pytest runner in same image
```

## Artifact paths

| Path | Contents |
|------|----------|
| `<preset>/build/clips/` | GDB clip + composite cache |
| `<preset>/build/bundle/` | Resolve sidecars, eligible GPKG |
| `<preset>/build/viewsheds/` | Per-site splatter workspaces |
| `<preset>/build/mesh/` | Pairwise + depth stores |
| `<preset>/build/plss_mlrs/` | PLSS/MLRS lookup cache |

Helpers: `resolved_preset_build_dir`, `resolved_bundle_dir`, `resolved_preset_clips_dir`, etc. in `sites_job.py`.

## Environment

| Var | Role |
|-----|------|
| `PEAKY_HOME` | `~/.peaky` (`peaky serve`); repo root (CLI via `./peaky`) |
| `PEAKY_PROJECTS` | Project presets root (default: `<PEAKY_HOME>/projects`) |
| `PEAKY_SHARE` | Optional shared tooling root |
| `SPLAT_CACHE` | Global Skadi tile mirror (default: `<peaky_home>/splat_cache`) |
| `PEAKY_DEV_IMAGE` | Docker image tag (default: `peaky:dev`) |
| `PEAKY_CACHE_DIR` | Host Skadi cache mount for `./peaky` / `./peaky-test` |

## External HTTP

All outbound fetches throttled via `http_pool.py`:

| Pool | Backend |
|------|---------|
| `SKADI_HTTP_POOL` | AWS Skadi SRTM tiles |
| `CADNSDI_HTTP_POOL` | BLM CadNSDI PLSS/MLRS |
| `ARCGIS_HTTP_POOL` | ArcGIS REST |
| `NOMINATIM_HTTP_POOL` | Geocoding (1 req/s) |

## Implemented

- `HttpPool` throttling for Skadi, CadNSDI, ArcGIS, Nominatim
- Locked preset writes: `preset_yaml_transaction` / `update_preset_yaml_tree`
- Site suggest via `peaky build --suggest` → `site_suggestions/preset_io.py`
- `./peaky` contract: mount `$PWD` as `/project`, run CLI from project cwd
- Published **`peaky-finders`** image: mount `~/.peaky` → `/.peaky`, `PEAKY_HOME=/.peaky`; projects under `projects/`, cache under `splat_cache/`

## Invariants

- **MEMORY first**: read before work, update before done (`.cursor/rules/memory-maintenance.mdc`)
- **Greenfield**: break freely to simplify (`.cursor/rules/greenfield-no-backcompat.mdc`)
- Preset YAML first for tunables (`.cursor/rules/preset-yaml-first.mdc`)
- Tests via `./peaky-test` only (`.cursor/rules/peaky-test.mdc`)
- Parallelism designed in: `-j`, preset `max_workers`, `ThreadPoolExecutor` waves
- Verbose mode: start/progress/end operation tracing (`--verbose`)

## Agent context

### Rules (`.cursor/rules/`)

| Rule | Topic |
|------|-------|
| `memory-maintenance` | Read MEMORY before work; update before done |
| `sites-and-goals` | Sites (repeaters + viewsheds) vs goals (coverage attractors) |
| `greenfield-no-backcompat` | Break freely to simplify; no shims |
| `preset-yaml-first` | Tunables in preset YAML |
| `preset-yaml-writes` | Locked ruamel round-trip writes |
| `http-fetch-pool` | Outbound HTTP throttling |
| `build-dag` | Incremental build graph |
| `peaky-test` | Docker test contract |
| `parallel-design` / `parallel-execution` | Multi-core patterns |
| `verbose-logging` | `--verbose` lifecycle |
| `commit-style` / `communication-style` | Commits and prose |
| `serve-web-ui` | Progressive web UI; reuse CLI/pipeline logic |

### Skills (`.cursor/skills/`)

| Skill | Topic |
|-------|-------|
| `peaky-architecture` | CLI-first overview, splatter, greenfield |
| `peaky-preset` | `config.yaml`, `Preset`, locked writes |
| `peaky-bundle` | Clips, eligible land, Skadi DEM |
| `peaky-build` | Build DAG, staleness, granular targets |
| `peaky-dev` | `./peaky`, `./peaky-test`, Docker |
| `peaky-serve` | `peaky serve` on-demand web over shared pipeline |
