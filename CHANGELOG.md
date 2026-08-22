# Changelog

User-facing release narrative for Peaky Finders. Policy: [`docs/change-management.md`](docs/change-management.md).

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions match git tags (`v4-final`, `v0.5.0`, …).

- Add user-visible work under **`## [Unreleased]`** in the same change set as the code.
- At release, promote Unreleased to `## [vX.Y.Z] - YYYY-MM-DD`, then open a fresh empty Unreleased.
- GitHub Release bodies come from this file (`./scripts/changelog.sh notes`), not commit subjects.

## [Unreleased]

Targets **v0.5.0** (first v5 release). Promote to `## [v0.5.0] - YYYY-MM-DD` when tagging.

### Added

- **v5 Rust workspace** — `peaky` CLI and splatter RF engine in-process; v4 Python/Docker wrapper retired (v4 tree kept as read-only reference).
- **Self-contained projects** — modem and environment presets live in project YAML; optional split files (`sites.yaml`, `land.yaml`, …) merge at load.
- **Project bootstrap** — `peaky serve <dir>` initializes an empty project when the directory has no config yet.
- **Single-project serve** — one project path per process; multi-project listing removed.
- **Land from GDB** — configured sources download and refresh to GeoJSON at boot (`refresh.downloadUrl`); invalid enabled sources block boot until disabled; `--no-land-refresh` skips network refresh. Validated sources are fingerprinted in `.peaky/cache/land/validate.json` so unchanged files skip re-parse on later boots.
- **Land layer UI** — sidebar folders, rename modal, label toggles, GeoJSON attribute filters, in-map editing.
- **Parallel workers** — `simulation.max_workers.dem` and `max_workers.coverage` tune Skadi fetch and viewshed warm pools.
- **Release CI** — tagged builds for Linux x64 and macOS (x64 + arm64) on `v*` push.
- **Auto-finder** — `peaky find path` for onX KML routes with optional `--watch` live map (included in v5 cutover).

### Changed

- **Docker image** — ships `peaky` only; mount one project at `/project`.
- **Map UI** — ESM module layout (`main.js`, `init.js`, shared `geo.js` / `api-urls.js` / `viewshed-sim.js`).
- **Splatter** — library-only; standalone splatter CLI removed.

### Fixed

- **Land display** — clip layers to project AOI before render; restore land layer edit API.
- **Land boot** — land integrity checks cache size/mtime fingerprints after the first successful parse, so repeat `peaky serve` boots skip re-reading large GeoJSON sources.

## [v4] - 2026-08-07

Last **Python/Docker** line before the v5 Rust cutover (git tag `v4-final`). Serve-only web UI over preset YAML and splatter RF; no tagged binary releases.

### Added

- **Serve web UI** — map-first project browser over `$PEAKY_HOME/projects/` presets (YAML sites, links, simulation, land).
- **Splatter RF** — Skadi DEM viewsheds, LoRa decode thresholds, site link mesh, progressive warm queue.
- **Goal seek** — backend APIs, map UI, scan progress, convert plan candidates to preset sites; Rust peak link finder for hop checks.
- **Land panel** — GeoJSON sources, eligible layers, spatial index for land queries, include/exclude layering.
- **Site editing** — CRUD, tags, map right-click/long-press add, KML and web geo import helpers.
- **Docker workflow** — `./peaky` wrapper, layered splatter and dev caches.

### Changed

- **Preset layout** — project-name directories, YAML restructuring, Nevada reference preset.
- **Links and viewsheds** — indexed cache, warm status in UI, inflight coverage deduplication.

### Fixed

- **Land sources** — accept GeoJSON paths on configured sources.
- **Serve edits** — faster site tag and save paths.
