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
| Dev/test | Docker — `./peaky` (`test`, `serve`, `build`, …) |
| Reference preset | `peaky_home/projects/nevada/config.yaml` |

## Domain model: sites vs goals

All map points live under **`sites:`** (slug → entry with `name`, `loc: [lat, lon]`, `type`).

| `type` | Role | Viewshed | Position |
|--------|------|----------|----------|
| `installed` | Deployed repeater | Yes | **Fixed** |
| `planned` | User-committed future site (serve **Add site**) | Yes | **Fixed** |
| `suggested` | `peaky build --suggest` output | Yes | **May move** on re-suggest |
| `goal` | Coverage attractor (planner target) | **No** | **Fixed** |

Repeaters (`installed` / `planned` / `suggested`) are coverage **sources** for build, mesh, and serve viewsheds. **`type: goal`** entries are planner attractors only — no viewshed, not mesh link endpoints, not in manual `links`. Solver sequencing via `suggest.mesh_backbone.goal_order` (site slugs with `type: goal`). Ephemeral runtime `bridge:*` goals still appear during connectivity healing (not preset YAML). **Captured** when a repeater footprint covers the point **and** mutual RF hop is viable.

Suggest **never relocates** `installed`, `planned`, or `goal` sites; only `type: suggested` entries are removed/replaced (`--replace-suggested`). Serve **promote** changes a goal's `type` to `installed` or `planned` in place (same slug).

Top-level `goals:` is **removed** — use `sites:` with `type: goal`. Helpers: `Preset.goals` / `Preset.repeaters`, `preset_goal_sites()`, `preset_repeater_sites()`.

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
| `serve` | Local web UI — Bootstrap 5 dark theme; project selector at `PEAKY_HOME`; toolbar **gear** opens settings modal (**Simulation** \| **Modems** \| **Environments**): `$PEAKY_HOME` catalogs + global simulation defaults; on project maps simulation tab shows effective merged values with per-field override highlight/reset, gear shows `radius · px`. MapLibre map with RF viewshed overlays (Street/USGS Topo/OpenTopo/Satellite, 3D terrain on tilt). Toolbar **Sites** button opens left slide-out panel (**Sites** \| **Goals** tabs): per-row show/hide (hides marker, viewshed, and link lines touching that entity), per-site viewshed toggle, delete (YAML + manual link scrub for sites; viewshed cache kept on disk), **+ Site** / **+ Goal** add-placement buttons. Right detail panel on map/list click (coords, PLSS, links, viewshed toggle); **Edit** opens draft editor with lat/lon inputs, map crosshair pick, comma-separated paste/copy, coordinate **history** (each move ≥25 m pushes prior coords as hidden; per-row show/copy/delete to compare candidates on map + 256 px viewshed preview), live PLSS + link prefetch; **Save** PATCHes `config.yaml` and warms full viewshed; **Cancel** resets draft. Goals may promote to `installed`/`planned` sites (fields copied, goal removed). Toolbar **Viewshed** opacity slider (0–100%) global. **Site links** / **Goal links** checkboxes toggle link layers. Add flow: panel **+** → map click → draft prefetch → create panel → `POST …/sites` or `POST …/goals`. Compass resets north-up 2D and fits sites + goals (30 km padding). Per-browser map prefs in `localStorage` (`peaky.map.v1.<slug>`): camera, basemap, link toggles, opacity, hidden sites/goals, per-site viewshed visibility. |

Entry: `peaky_finders.peaky_cli:main`. **`serve`**: Waitress WSGI (`serve_app.py` routes + `serve_cli.py` runner, 8 threads, 30s channel timeout); `--reload` supervises a child (`python -m peaky_finders.serve_cli --no-reload`), waits until the port accepts connections before logging `running`, respawning on sha256 fingerprint changes under `peaky_finders` + `serve_static/` (2s grace after each restart); bind failures print port-in-use hints (`lsof`, `docker ps`). Reload dev child logs each HTTP request (`req [n] start/end` with ms + status); `--no-request-log` to disable, `--verbose` for RF helper detail too. Docker publishes `PEAKY_SERVE_PORT` (default 8080). UI in `serve_html.py`; assets at `/static/` (`app.css`, `home-settings.js`, `project-map.js`) plus root favicons/manifest in `serve_static/`. APIs: `GET/PATCH /api/home/simulation`; `GET/PATCH /api/p/<slug>/simulation` (effective + `defaults` + `overrides[]`; PATCH accepts `reset: [field paths]`); `GET/POST/PATCH/DELETE /api/home/modems[/<name>]` … `POST /api/p/<slug>/simulation` (legacy radius/raster POST) …

## Web UI (`peaky serve`)

Progressive, on-demand shell over the **same** preset + splatter + build artifacts as the CLI — not a forked product.

| Aspect | CLI / build | Web |
|--------|-------------|-----|
| Scheduling | Batch (`peaky build`, DAG waves, `-j`) | Per HTTP request (user toggles map layer, opens project) |
| Viewsheds | All sites via build graph / `run_viewshed_batch` | `POST …/viewsheds/<site>/warm` queues background work; per-site generation supersedes stale sim params; lifecycle on `GET …/events` SSE; `GET` meta/PNG read cache only; coverage serialized (`PEAKY_SERVE_COVERAGE_CONCURRENT`, default **1**) |
| Artifacts | `<preset>/build/…` | Same paths; serve reads cache if build already ran |
| HTTP layer | — | Waitress WSGI: `serve_app.py` routes + `serve_cli.py` runner; logic in `serve_*.py` |
| Site links | Build mesh `links` KML + footprint mutual coverage | On-demand splatter `link_mutual_*` via `serve_links.py` |

Map UI prefs (camera, basemap, link toggles, opacity, hidden sites/goals, per-site viewshed visibility) persist in browser `localStorage` per slug. **Settings** gear modal (landing + project toolbar): global modem/environment CRUD; simulation tab edits global defaults on landing or **project** effective values on map (`GET/PATCH /api/p/<slug>/simulation` with `overrides[]` + `reset`); override fields styled warning with per-field **Reset**. Saves re-warm viewsheds via `window.PEAKY_MAP.reloadViewshedsForSimChange()`. SSE `hello` (incl. auto-reconnect) re-`POST` warms pending sites (idempotent reconcile — no server event replay). Agent rule: `.cursor/rules/serve-web-ui.mdc`; skill: `peaky-serve`.

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

**Two-tier YAML** (legacy `.json` unsupported):

| Tier | Path | Role |
|------|------|------|
| Global defaults | `$PEAKY_HOME/config.yaml` | `simulation`, `display`, `mesh`, `suggest` — seeded from bundled templates on first CLI/serve start |
| Project | `$PEAKY_HOME/projects/<slug>/config.yaml` | **Overrides** + always-local `land`, `sites`, `links` |

Load: `deep_merge($PEAKY_HOME/config.yaml ← project)`. Writes (`update_preset_yaml_tree`, serve PATCH): mutate project file, validate merged preset, **prune** keys equal to defaults. `load_preset` / `resolve_preset_raw` return the merged effective config. `ensure_peaky_home()` seeds missing `config.yaml`, `modems.yaml`, `environments.yaml`.

**Global RF catalogs** (not per project): `$PEAKY_HOME/modems.yaml` (`modem_presets:`) and `$PEAKY_HOME/environments.yaml` (`environment_presets:`). Project preset selects `simulation.modem` / `simulation.environment` by name. Build simulation stamp fingerprints **resolved** merged simulation + catalog fingerprint.

| Section | Contents |
|---------|----------|
| `simulation` | Active **`modem`** / **`environment`** names (catalogs in `$PEAKY_HOME`), `radius_km` (≤100), `raster_dimension` (128–4096 px square), `max_workers.splatter`, `transmitter`/`receiver` chains |
| `display` | Viewshed raster (`colormap`, `transparency`, dBm range) + `kml` styles + `kmz` layer toggles |
| `land` | Slug-keyed `layers:` with roles `aoi` / `positive` / `negative` / `reference`; vector paths under `<preset-dir>/data/` |
| `mesh` | Pairwise/depth build knobs, raster size, worker counts |
| `suggest` | Site planner (`land-grab` / `mesh-backbone`); `mesh_backbone.goal_order` sequences `type: goal` site slugs |
| `sites` | All map points — `installed` / `planned` / `suggested` (repeaters + viewsheds) and `goal` (attractors) |
| `links` | Manual mutual site pairs `[[a, b], …]` (field-verified; unioned with viewshed mutual coverage) |

CLI `peaky bundle` unchanged; artifact dir remains `<preset>/build/bundle/`. Build stamps: `land_*`, `display_kml`, `display_kmz`, `mesh` (replaces `bundle_*` stamp names).

Model: `Preset` in `sites_job.py`. Writes: `update_preset_yaml_tree` (ruamel round-trip, file lock). **`land` is optional**: missing or unconfigured (`layers` without both `aoi` and `positive`) skips bundle/eligible/suggest land phases; sites, goals, viewsheds, and RF link checks still work.

## Repo layout

```
peaky_finders/     # Python package (Poetry, src layout)
splatter/          # Git submodule — Rust/PyO3 coverage engine
peaky_home/        # Dev runtime home (`PEAKY_HOME` via `./peaky`; gitignored except YAML)
  config.yaml      # Global preset defaults
  modems.yaml      # RF modem catalog
  environments.yaml
  projects/
    nevada/        # Reference project
docker/            # Entrypoint script
Dockerfile         # dev + latest targets (GDAL, Poetry, splatter)
peaky              # Dev Docker runner (`test`, `serve`, `build`, …)
```

## Artifact paths

| Path | Contents |
|------|----------|
| `<preset>/build/clips/` | GDB clip + composite cache |
| `<preset>/build/bundle/` | Resolve sidecars, eligible GPKG |
| `<preset>/build/viewsheds/` | Per-site splatter workspaces |
| `<preset>/build/mesh/` | Pairwise + depth stores |
| `<preset>/build/plss/` | CadNSDI SECDIVID lookup cache |

Helpers: `resolved_preset_build_dir`, `resolved_bundle_dir`, `resolved_preset_clips_dir`, etc. in `sites_job.py`.

## Environment

| Var | Role |
|-----|------|
| `PEAKY_HOME` | `/.peaky` in Docker; default `<repo>/peaky_home` when unset and dir exists; **`config.yaml`** + **`modems.yaml`** + **`environments.yaml`** |
| `PEAKY_PROJECTS` | Project presets root (default: `<PEAKY_HOME>/projects`) |
| `PEAKY_SHARE` | Optional shared tooling root |
| `SPLAT_CACHE` | Global Skadi tile mirror (default: `<PEAKY_HOME>/splat_cache`) |
| `PEAKY_DEV_IMAGE` | Docker image tag (default: `peaky:dev`) |
| `PEAKY_CACHE_DIR` | Host Skadi cache mount for `./peaky` (default `~/.peaky/splat_cache`) |

## External HTTP

All outbound fetches throttled via `http_pool.py`:

| Pool | Backend |
|------|---------|
| `SKADI_HTTP_POOL` | AWS Skadi SRTM tiles |
| `CADNSDI_HTTP_POOL` | BLM CadNSDI PLSS (SECDIVID) |
| `ARCGIS_HTTP_POOL` | ArcGIS REST |
| `NOMINATIM_HTTP_POOL` | Geocoding (1 req/s) |

## Implemented

- `HttpPool` throttling for Skadi, CadNSDI, ArcGIS, Nominatim
- Locked preset writes: `preset_yaml_transaction` / `update_preset_yaml_tree`
- Site suggest via `peaky build --suggest` → `site_suggestions/preset_io.py`
- `./peaky` contract: mount `<repo>/peaky_home` → `/.peaky` (`PEAKY_HOME`), `$PWD` as `/project`, run CLI from project cwd
- Published **`peaky-finders`** image: mount `~/.peaky` → `/.peaky`, `PEAKY_HOME=/.peaky`; projects under `projects/`, cache under `splat_cache/`

## Invariants

- **MEMORY first**: read before work, update before done (`.cursor/rules/memory-maintenance.mdc`)
- **Greenfield**: break freely to simplify (`.cursor/rules/greenfield-no-backcompat.mdc`)
- Preset YAML first for tunables (`.cursor/rules/preset-yaml-first.mdc`)
- Tests via `./peaky test` only (`.cursor/rules/peaky-test.mdc`)
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
| `peaky-test` | `./peaky test` Docker contract |
| `parallel-design` / `parallel-execution` | Multi-core patterns |
| `verbose-logging` | `--verbose` lifecycle |
| `commit-style` | Points to skill `commit` on commit requests |
| `communication-style` | Prose style |
| `serve-web-ui` | Progressive web UI; reuse CLI/pipeline logic |

### Skills (`.cursor/skills/`)

| Skill | Topic |
|-------|-------|
| `peaky-architecture` | CLI-first overview, splatter, greenfield |
| `peaky-preset` | `config.yaml`, `Preset`, locked writes |
| `peaky-bundle` | Clips, eligible land, Skadi DEM |
| `peaky-build` | Build DAG, staleness, granular targets |
| `peaky-dev` | `./peaky`, Docker |
| `peaky-serve` | `peaky serve` on-demand web over shared pipeline |
| `commit` | Scoped session commits (`/commit`); Conventional Commits |
