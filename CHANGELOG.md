# Changelog

User-facing release narrative for Peaky Finders. Policy: [`docs/change-management.md`](docs/change-management.md).

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions match git tags (`v4-final`, `v0.5.0`, …).

- Add user-visible work under **`## [Unreleased]`** in the same change set as the code.
- At release, promote Unreleased to `## [vX.Y.Z] - YYYY-MM-DD`, then open a fresh empty Unreleased.
- GitHub Release bodies come from this file (`./scripts/changelog.sh notes`), not commit subjects.

## [Unreleased]

### Added

- **`peaky peaks`** — build an eligible-peaks catalog (`peaks.yaml`) from GNIS summits, EIP/AlertWildfire/installed site seeds, and DEM local maxima near jeep-class OSM roads. Filters: eligible land, road within 0.5 mi, DEM hike profile ≤ 0.5 mi, max slope calibrated from `old-razorback`. Operator `deny: true` rows survive regen. Copious INFO logging; OSM PBF cached under `.peaky/cache/osm/`.
- **Eligible peaks map layer** — project map always renders catalog peaks from `GET /api/p/{slug}/peaks` as Peaky logo pins below site markers. Dashed gold lines show road-to-summit access.
- **Summit snap** — `peaky peaks` moves each candidate to the highest DEM point within 500 m (disc-limited, no ridge chaining). Stores nearest road `[lat, lon]` in `peaks.yaml`.
- **Find alternates** — on a site with RF links, search other eligible placements that still mutual-P2P to the same neighbors. Dots on the map; add a chosen spot as a new site (original pin stays).
- **Eligible land overlay** — built-in land-panel toggle for seek land. Include minus exclude, clipped to the AOI. Rebuilt only when those land layers change.

### Changed

- **Goal seek progress lens** — approach-hop peak scan is hop disc ∩ closer-to-goal than start (replaces the narrow goal wedge). Existing sites in hop range always appear as candidates. Non-completers rank by goal-distance progress before RF margin. Map overlay shows the lens shape.
- **Goal seek forward-path gate** — when some candidate of this hop already has an RF path to the goal (preset sites ∪ scan peaks, closer each hop), the rest are dropped as dead ends. Unfinished corridors keep first-hop RF candidates.
- **Site `node` field** — optional `sites.<slug>.node` (ME key) is preserved on YAML load/save so a site can point at one fleet unit. Location stays on the site.
- **Sites sidebar** — full-width list rows instead of shrink-wrapped buttons. Tag chips scroll in a capped stack so the site list keeps height. Hover uses light text on a brighter wash. **In view** sits above the chips and limits both tags and sites to the current map.
- **Land sidebar** — folders, sources, and layers read as a tree. Layer rows replace the low-contrast chips. Edit and delete sit quieter than the visibility control.
- **Map UI** — Vue 3 reactive migration complete: `boot.js` + domain modules (`sites`, `land`, `seek`, `viewshed`, `links`) + Vue panels (sites, land, seek, site sheet). Store is source of truth; MapLibre stays in `map/` adapters. Debug `peaky serve` serves JS from disk. Deleted monolithic `init.js` and legacy innerHTML panel paths (~7k lines removed from `domains/app.js`).

### Removed

- **Ineligible overlay** — dropped. Eligible is include minus exclude, clipped to the AOI.

### Fixed

- **Goal seek empty corridor** — approach hops no longer drop every peak when leftover sites near the goal can RF it but nothing in this hop can. First-hop RF candidates stay so you can pioneer a new corridor.
- **Delete site** — the Vue site sheet has Delete again. Confirm, then drop the site from YAML, map, and sidebar. The last site stays.
- **Site tag edits** — the site edit sheet can add and remove tags. Bulk-removing a tag hides sites that no longer match the sidebar filter, instead of leaving them on the map.
- **Edit draft links** — moving a site in the editor no longer draws a preview line back to its old pin. The viewshed-ready fetch was treating the move like a new site.
- **Draft P2P lines** — placing or moving a site no longer draws preview links to tag-filtered or hidden peers. Draft lines use the same map visibility as the site mesh.
- **Goal seek ranking** — candidates that RF-complete the hop to the goal sort first. Extra links into the existing local mesh beat a lonely completer. Weaker-leg margin is next. Elevation is not a score.
- **Goal seek landing** — when the goal is already in hop range but start-to-goal RF fails, seek grids every cell that is still in hop range of both ends (no angle clip), then refines around completers. Approach hops (goal out of range) still use local-max peaks and distance.
- **Goal seek Recalculate** — the hop scan button stayed disabled after start and goal were set. A false `disabled` binding on `wa-button` is treated as disabled, so Recalculate is a native button that arms whenever a start and goal exist. Clicking it no longer overflows the stack (local `setSeekScanning` had been calling itself).
- **Goal seek wedge** — the green hop wedge now paints above viewsheds, land, links, and site markers.
- **Eligible overlay** — real per-parcel boolean difference (i_overlay engine) instead of punching raw exclude rings as holes. Overlapping wilderness/ACEC/WSA rings made invalid polygons, which is what MapLibre rendered as statewide bars and sliver triangles. Full six-state build now takes ~10 s with the cutouts intact.
- **Seek convert links** — converting a goal-seek hop now seeds the site mesh from the same P2P check and keeps the rest of the chain visible. The hop stays a real RF link instead of disappearing behind a tag filter or a stale mesh.
- **Goal seek hang** — eligible land no longer dissolves statewide SMA. Seek clips parcels to the hop wedge and subtracts exclude via an R-tree, so Kingman-scale scans leave "Building eligible land" in seconds.
- **Land folders** — folder and source rows use the same eye and label controls. Every source lists its layers. Include layers no longer hardcode "Public land"; names come from the layer id or filters.
- **Land visibility** — source and folder eye toggles now show or hide every child layer on the map, including layers that were never loaded.
- **Land stack** — exclude (blocked land) paints above include, even when include finishes loading later or sits first in the sidebar.
- **Seek scan spinner** — the scan pin stays on the start site instead of sliding diagonally while RF is computing.
- **Stale seek plan** — deleting a converted hop no longer bricks project load. A plan that names a missing site is dropped.
- **Stuck warm pins** — viewshed pins no longer sit on `warm…` after a cache hit, a failed job, or a quality upgrade.

## [v0.5.0] - 2026-08-22

First v5 release: Rust workspace, self-contained projects, land from GDB, release CI.

### Added

- **v5 Rust workspace** — `peaky` CLI and splatter RF engine in-process; v4 Python/Docker wrapper retired (v4 tree kept as read-only reference).
- **Self-contained projects** — modem and environment presets live in project YAML; optional split files (`sites.yaml`, `land.yaml`, …) merge at load.
- **Project bootstrap** — `peaky serve <dir>` initializes an empty project when the directory has no config yet.
- **Single-project serve** — one project path per process; multi-project listing removed.
- **Land from GDB** — configured sources download and refresh to GeoJSON at boot (`refresh.downloadUrl`); invalid enabled sources block boot until disabled; `--fast-boot` skips validation, refresh, and cache warm (`--no-land-refresh` alias). Validated sources are fingerprinted in `.peaky/cache/land/validate.json` so unchanged files skip re-parse on later boots.
- **Land layer UI** — sidebar folders, rename modal, label toggles, GeoJSON attribute filters, in-map editing.
- **Parallel workers** — `simulation.max_workers.dem` and `max_workers.coverage` tune Skadi fetch and viewshed warm pools.
- **Release CI** — tagged builds for Linux x64 and macOS (x64 + arm64) on `v*` push.
- **Auto-finder** — `peaky find path` for onX KML routes with optional `--watch` live map (included in v5 cutover).
- **Official base export** — `peaky freeze <project>` writes a compact publishable directory (`config.yaml`, `project.geojson`, `freeze.json`) with land filters baked in and source attributes stripped; optional `--include-sites`.

### Changed

- **Docker image** — ships `peaky` only; mount one project at `/project`.
- **Map UI** — ESM module layout (`main.js`, `init.js`, shared `geo.js` / `api-urls.js` / `viewshed-sim.js`).
- **Splatter** — library-only; standalone splatter CLI removed.

### Fixed

- **Land display** — clip layers to project AOI before render; restore land layer edit API.
- **Land boot** — land integrity checks cache size/mtime fingerprints after the first successful parse, so repeat `peaky serve` boots skip re-reading large GeoJSON sources.
- **Land map load** — fetch only visible land layers on map refresh; cache AOI-clipped and filtered GeoJSON under `.peaky/cache/land/pipeline/` so repeat page loads skip re-parsing large sources.
- **Land source editor** — single-column modal (map above rules), one dialog scroll, accordion rules; filter values use a grid instead of nested scroll panes.
- **Land boot** — after integrity validation, warm import-preview caches (AOI-clipped GeoJSON, field lists, field value indexes) then pipeline artifacts for enabled layers; boot loops log per-layer progress at INFO (`[done/total]`).

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

[Unreleased]: https://github.com/MeshEnvy/peaky-finders/compare/v0.5.0...HEAD
[v0.5.0]: https://github.com/MeshEnvy/peaky-finders/releases/tag/v0.5.0
[v4]: https://github.com/MeshEnvy/peaky-finders/releases/tag/v4-final

