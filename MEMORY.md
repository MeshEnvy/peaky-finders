# peaky_finders v4 — project memory

Living snapshot of **current** architecture and repo state. **Agents: read this file before substantive work; update it in the same change set when anything below shifts.**

## Agent contract

1. **Read first** — Load MEMORY.md at the start of any task that touches presets, build, CLI, bundle, mesh, viewsheds, or dev workflow. Treat it as source of truth over stale chat or assumed v3/v2 behavior.
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
| Interface | **CLI** from project dir (`config.yaml` in cwd); **`peaky serve`** web stub (branch `web2`) |
| RF engine | `splatter` submodule (PyO3 Fresnel/FSPL) |
| Build | Incremental preset DAG (`peaky build`) |
| Dev/test | Docker — `./peaky`, `./peaky-test` |
| Reference preset | `projects/nevada/config.yaml` |

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
| `serve` | Local web UI stub (`--host`, `--port`; no `config.yaml` required) |

Entry: `peaky_finders.peaky_cli:main`. **`serve`**: stdlib `HTTPServer`, Docker publishes `PEAKY_SERVE_PORT` (default 8080).

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
| `simulation` | RF/modem/env, splatter params, `max_workers` |
| `display` | KML/overlay styling |
| `bundle` | AOI/include/exclude GDB layers, mesh/suggest knobs |
| `sites` | Installed/suggested sites (`loc`, optional `plss`/`mlrs`, `sees`) |

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
| `PEAKY_HOME` | Runtime home (default: repo root) |
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

## Active threads

- **`web2` branch** — `peaky serve` web UI stub; empty page for now

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
| `greenfield-no-backcompat` | Break freely to simplify; no shims |
| `preset-yaml-first` | Tunables in preset YAML |
| `preset-yaml-writes` | Locked ruamel round-trip writes |
| `http-fetch-pool` | Outbound HTTP throttling |
| `build-dag` | Incremental build graph |
| `peaky-test` | Docker test contract |
| `parallel-design` / `parallel-execution` | Multi-core patterns |
| `verbose-logging` | `--verbose` lifecycle |
| `commit-style` / `communication-style` | Commits and prose |

### Skills (`.cursor/skills/`)

| Skill | Topic |
|-------|-------|
| `peaky-architecture` | CLI-first overview, splatter, greenfield |
| `peaky-preset` | `config.yaml`, `Preset`, locked writes |
| `peaky-bundle` | Clips, eligible land, Skadi DEM |
| `peaky-build` | Build DAG, staleness, granular targets |
| `peaky-dev` | `./peaky`, `./peaky-test`, Docker |
