# Changelog

User-facing release narrative for Peaky Finders. Policy: [`docs/change-management.md`](docs/change-management.md).

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions match git tags (`v4-final`, `v0.5.0`, …).

- Add user-visible work under **`## [Unreleased]`** in the same change set as the code.
- At release, promote Unreleased to `## [vX.Y.Z] - YYYY-MM-DD`, then open a fresh empty Unreleased.
- GitHub Release bodies come from this file (`./scripts/changelog.sh notes`), not commit subjects.

## [Unreleased]

### Added

- **Catalog peak RF preview** — clicking a catalog peak prefetches a viewshed raster and hop-range P2P draft links to booked sites (same placement preview APIs as seek/alternates). Deselect, close the sheet, or pick a site to clear; **Add as site** hands off to the real site mesh. Cold-cache warms paint on first click (SSE `_draft` → `_peak`).
- **Map deep links** — selecting a site or catalog peak writes `?site=` / `?peak=` plus camera (`lat`, `lon`, `z`, optional `bearing`, `pitch`) to the address bar. Back, forward, refresh, and pasted URLs restore the pin and view. Basemap, land layers, and viewshed prefs stay in localStorage.

### Fixed

- **Access paths on RF links** — selecting a link paints jeep/hike routes on both endpoints (site deselect no longer hides them).

### Changed

- **Paved snap uses interpolated samples** — paved anchors are densified every 40 m like jeep roads, so on-road snap reaches pads between sparse OSM vertices. `ACCESS_ALGO_VERSION` → **10**. Re-open a peak/site sheet to re-warm leftover pins.
- **Four access modes** — jeep and hike are independent. Pad on pavement within 40 m → no jeep, no hike. On dirt → jeep only. Paved park with a walk → hike only. Dirt park → both. Stops inventing a dirt loop when the pin is already on the street. `ACCESS_ALGO_VERSION` → **9**. Re-open a peak/site sheet to re-warm leftover pins.
- **Hike paths avoid wasted elevation** — A* contours around bumps instead of climbing then descending. Park picker tries two more along-road samples per sector and ranks by gain, then loss. `ACCESS_ALGO_VERSION` → **8**. Re-open a peak/site sheet to re-warm leftover pins.
- **Catalog DEM peaks need 20 m prominence** — same 8-neighbor floor as hop-disc. Highway berms and playa ripples are not peaks. `PEAK_ALGO_VERSION` → **5**. Existing pins stay until you re-run `peaky peaks`.
- **Park on pavement** — nearest paved highway competes with jeep-road sectors. Stay on pavement when the hike is comparable instead of detouring onto a grade-5 shoulder. `ACCESS_ALGO_VERSION` → **7**. Re-open a peak/site sheet to re-warm leftover pins.
- **Access sheet is metric** — hike/jeep/road-class lengths and elevation (gain, profile, peak title) use m/km only. No yards, feet, or miles.
- **Jeep difficulty is road class, not grade** — jeep easy/medium/difficult/extreme now follows OSM highway + tracktype (worst meaningful stretch). A 33% DEM spike on a grade-2 dirt road is no longer extreme. OSM `grade4` is difficult; only `grade5` is extreme. Hike rating stays grade-based, but average grade only counts on hikes ≥400 m.
- **Hike extreme needs scale** — a steep 200 m roadside bump caps at difficult. Extreme requires ≥400 m horizontal or ≥100 m gain in addition to steep grades. Peak sheet badge label is **hike**, not “overall”.
- **Park is the gentlest hike, not the nearest road** — eligibility still needs some jeep road within 805 m, but the park is the best of eight bearing-sector candidates within 1 mi (lowest max slope, then shortest hike). Sites use the same picker, then fall back to the nearest road only when nothing is within a hike path. `ACCESS_ALGO_VERSION` / `PEAK_ALGO_VERSION` → **4**. Re-run `peaky peaks`.
- **Full-AOI `peaky peaks` replaces catalog** — a default statewide/AOI run keeps only this-run survivors plus `deny: true` rows. Corridor, bbox, polygon, and `--stop-after` still merge. After every scan, `access/` files that are neither a peak nor a site are deleted.
- **Summit snap** — catalog placement hill-climbs the DEM to a local maximum (15 m steps, 500 m cap, land-filtered) instead of picking the highest 30 m ring sample (which could sit on a slope). `ACCESS_ALGO_VERSION` / `PEAK_ALGO_VERSION` → **3**. Re-run `peaky peaks`.

### Fixed

- **Peak pins stack under sites** — catalog peak markers (and their access lines) paint below established site pins and RF link lines. A click on an overlapping site still selects the site.
- **Peak click camera** — selecting a catalog peak no longer `fitBounds`s to the jeep/hike path (that reset pitch, bearing, and zoom). The camera stays put, same as a site click. Profile-point clicks still fly.
- **Site hike chords** — A* can detour a full hike-path (not a 500 m corridor) and may take a steep last grid step onto the pad. If the 30% cap still fails, site display retries a looser cap before drawing a fall-line. Re-open the site sheet to re-warm.
- **Site access map lines** — selecting a site paints its jeep/hike routes (and paved/park dots). The sheet **Access** button is an opt-in pin (default off) that keeps those lines after deselect. The site sheet fetch writes the same overlay the map reads. Viewport warm only hits the selected site and pinned slugs.
- **Peaks list page load** — `GET /api/p/{slug}/peaks` reads thin peak YAML only (no access-profile merge), caches `.peaky/cache/peaks/list.json`, and runs off the tokio worker so peak-panel `/access/{slug}` is not blocked. Access JSON omits unused `histogram` arrays.

### Added

- **Link sheet + Fortify** — click an RF link line to open a side sheet (endpoints, distance, strength, weaker-leg margin dB). **Fortify** scans catalog peaks in the hop lens between both sites that mutual-P2P to each end, paints relay candidates on the map, and **Add as site** keeps the peak slug. Selecting a candidate (map or sidebar list) shows its viewshed plus jeep/hike access paths and flies the map to A–relay–B. Sidebar lists ranked candidates with split position, leg distances, RF margin, and access difficulty. Alternates and Fortify are exclusive. APIs: `GET …/links/pair`, `GET …/fortify`, `GET …/fortify/scan-progress`.
- **Jeep road class** — the access sheet shows the OSM highway + tracktype that set the drive rating (and how much of the route it is), plus a one-line gloss of what that track grade means (grade3 = mixed ruts, grade4 = plan on 4WD, grade5 = specialized only). Jeep max/avg are labeled slope so they are not confused with OSM grade.
- **Summit pin rings** — each eligible-peak pin has a colored outline from the worse of hike vs jeep (green easy, blue medium, orange difficult, red extreme).
- **Sharded peaks + access** — `peaks/<slug>.yaml` + `access/<slug>.yaml` (plus `peaks/_meta.yaml` and `access/_meta.yaml`). Site and peak slugs share one namespace. `GET /api/p/{slug}/access/{place_slug}` loads profiles. Peak **Add as site** keeps the peak slug so access carries over. Leftover monolithic `peaks.yaml` is deleted on load/write (no migrate).
- **Access pathfinding meta** — `access/_meta.yaml` owns jeep/hike routing settings (`algo_version` 2, OSM highway lists, jeep cap, `hike_path_max_m` 1 mi, DEM/OSM sample spacing, place road-search radius). Serve and `peaky peaks` read it; missing file is created with defaults.
- **DEM hike pathfinding** — peak eligibility: park within **0.5 mi** crow-flies of summit; grade-capped A* path up to **1 mi** 3D; straight chord only when already grade-safe. Sites pathfind when crow-flies ≤ 1 mi, else chord (ungated display). Bump `ACCESS_ALGO_VERSION` / `PEAK_ALGO_VERSION` → re-run `peaky peaks` and re-warm access.
- **Site access toggle** — independent **Access** button beside Viewshed on the site sheet; persisted per site. Map loads jeep/hike via `GET …/access/{slug}?warm=1` when Access is on (not tied to viewshed warm).
- **`peaky export`** — write tag-filtered site points to KML for onX import (`--tag`, optional `--exclude-tag`, `-o`). Placemark names include the book slug for copy-back after field stakes. `sites.yaml` stays source of truth.
- **Sites panel Export** — download KML for sites visible in the current map viewport (same placemark format as CLI export).
- **`peaky peaks`** — build an eligible-peaks catalog from GNIS summits, EIP/AlertWildfire/installed site seeds, and DEM local maxima near jeep-class OSM roads. Filters: eligible land, road within 0.5 mi crow-flies, grade-safe DEM hike path ≤ 1 mi (A* or safe chord), max segment grade 30%. Operator `deny: true` rows survive regen. Copious INFO logging; OSM PBF cached under `.peaky/cache/osm/`.
- **Eligible peaks map layer** — project map always renders catalog peaks from `GET /api/p/{slug}/peaks` as Peaky logo pins below site markers. Jeep/hike lines draw only from stored access profiles (no crow-flies fallback); select a peak to load `access/<slug>`.
- **Summit snap** — `peaky peaks` moves each candidate to the highest **eligible** DEM point within 500 m (disc-limited, no ridge chaining). Stores nearest road `[lat, lon]` in `peaks.yaml`.
- **Find alternates** — on a site with RF links, search other eligible placements that still mutual-P2P to the same neighbors. Dots on the map; add a chosen spot as a new site (original pin stays).
- **Eligible land overlay** — built-in land-panel toggle for seek land. Include minus exclude, clipped to the AOI. Rebuilt only when those land layers change.
- **Peaks corridor clip** — `peaky peaks --corridor` / `--corridor-sites` / `--polygon` scan a geodesic strip or GeoJSON polygon instead of a rectangular bbox. Default width 100 mi.
- **Jeep access routes** — `peaky peaks` routes from the nearest paved road to the park point (OSM graph + weighted shortest path, prefer maintained roads, 20 mi cap). Stored in `peaks.yaml` as `paved_loc`, `jeep_m`, and `jeep` profile. Map: orange jeep line + paved dot (distinct from blue RF links); peak panel shows jeep stats and a grade-colored elevation profile (Paved → Park).
- **Peak hike panel** — click a peak pin for foot-hike stats and a grade-colored elevation profile (Road → Summit). Click the profile to pan the map to that spot (white/red cursor on the route).
- **Site access panel** — selecting a site shows the same hike/jeep difficulty, distances, grades, and clickable elevation profiles as the peak sheet (shared `AccessProfilesPanel`; Road → Site). Click a profile point to pan/mark the map.
- **Access / peak compute keys** — `access/<slug>.yaml` and `peaks/<slug>.yaml` store a `compute_key` fingerprint (algo version + settings). Stale or missing keys force re-warm on serve. Pathfinding settings + `algo_version` live in `access/_meta.yaml` (bump code `ACCESS_ALGO_VERSION` or edit the meta file to invalidate). Peak eligibility gates stay in `peaks/_meta.yaml`.
- **`--stop-after N`** on `peaky peaks` — stop after N qualifying peaks (serial filter; prints hike report for each).

### Fixed

- **Peak access map lines** — removed crow-flies paved→park and park→summit fallbacks. Jeep/hike lines and paved/park dots paint only from stored access profiles (loaded on select / site warm). Catalog peaks still require jeep + hike at build time.
- **Access path rewrite** — loading the peaks catalog no longer drops `hike`/`jeep` polylines from `access/`. A thin reload + catalog rewrite (e.g. start of `peaky peaks`) used to leave scalar-only access files.
- **`peaky peaks` catalog merge** — default merges into existing catalog (freshen nearby rows, keep peaks outside the scan). Pass `--clean` to wipe and rebuild from this scan only (operator `deny: true` rows still survive). Progressive upserts every 10 new/freshened peaks.
- **`peaky peaks` max slope** — fixed **30% grade** (~16.7°) max segment on road-to-summit profile. Removed Old Razorback site calibration.
- **`peaky peaks` parallel filter** — summit snap + road + hike profile run on a rayon pool (default: all cores; `PEAKY_PEAKS_WORKERS=N` to cap).
- **Find alternates** — peak dots now come from the same `peaks.yaml` catalog as goal seek (shared RF lens filter), not live DEM grid/ridge scan.
- **Goal seek peaks** — candidates come from `peaks.yaml` only (hop disc ∩ closer-to-goal than start, then RF rank). No live DEM ridge scan, landing grid, or forward-path prune. Run `peaky peaks` first if the catalog is empty.
- **Goal seek progress lens** — map overlay still shows hop disc ∩ closer-to-goal than start. Preset sites in hop range remain candidates alongside catalog peaks.
- **Site `node` field** — optional `sites.<slug>.node` (ME key) is preserved on YAML load/save so a site can point at one fleet unit. Location stays on the site.
- **Sites sidebar** — full-width list rows instead of shrink-wrapped buttons. Tag chips scroll in a capped stack so the site list keeps height. Hover uses light text on a brighter wash. **In view** sits above the chips and limits both tags and sites to the current map.
- **Land sidebar** — folders, sources, and layers read as a tree. Layer rows replace the low-contrast chips. Edit and delete sit quieter than the visibility control.
- **Map UI** — Vue 3 reactive migration complete: `boot.js` + domain modules (`sites`, `land`, `seek`, `viewshed`, `links`) + Vue panels (sites, land, seek, site sheet). Store is source of truth; MapLibre stays in `map/` adapters. Debug `peaky serve` serves JS from disk. Deleted monolithic `init.js` and legacy innerHTML panel paths (~7k lines removed from `domains/app.js`).

### Removed

- **Monolithic `peaks.yaml` migrate** — greenfield only. Leftover `peaks.yaml` is scrapped (deleted), never converted into `peaks/` + `access/`.
- **Ineligible overlay** — dropped. Eligible is include minus exclude, clipped to the AOI.
- **Goal seek live peak scan** — DEM binned maxima, corridor landing grid, ridge refine, and forward-path dead-end prune are gone. Alternates still uses live scan.

### Fixed
- **Peaks summit snap** — snap now picks the highest **eligible** DEM point within 500 m, not the raw crest. Ineligible ridge tops no longer steal snap and drop the candidate. Nearby dedup keeps the higher summit; DEM seeds defer road proximity to post-snap filtering.
- **Delete site** — the Vue site sheet has Delete again. Confirm, then drop the site from YAML, map, and sidebar. The last site stays.
- **Site tag edits** — the site edit sheet can add and remove tags. Bulk-removing a tag hides sites that no longer match the sidebar filter, instead of leaving them on the map.
- **Edit draft links** — moving a site in the editor no longer draws a preview line back to its old pin. The viewshed-ready fetch was treating the move like a new site.
- **Draft P2P lines** — placing or moving a site no longer draws preview links to tag-filtered or hidden peers. Draft lines use the same map visibility as the site mesh.
- **Goal seek ranking** — candidates that RF-complete the hop to the goal sort first. Extra links into the existing local mesh beat a lonely completer. Weaker-leg margin is next. Elevation is not a score.
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

