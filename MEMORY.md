# Peaky Finders v5 — project memory

Living snapshot of **current** architecture. **Agents: read before substantive work; update in the same change set when anything below shifts.**

## Agent contract

1. **Read first** — Load MEMORY.md before tasks touching presets, serve, RF, splatter, or CLI.
2. **Update always** — Architecture/API/path/workflow changes → update MEMORY.md before marking done.
3. **Greenfield** — v4 is read-only reference. Break freely in v5; no Python/Docker `./peaky` wrapper.
4. **Opportunistic refactor** — Touching code → extract or dedup one safe incremental improvement in the same change set (`.cursor/rules/opportunistic-refactor.mdc`).

## Status

| Item | State |
|------|-------|
| Repo | `peaky-finders-v5` — pure Rust workspace |
| v4 | Frozen reference; do not delete until v5 soak |
| Interface | `peaky serve <project>` — web UI for one project directory. Overlay: `peaky map export <project> --out-dir <static>` |
| RF engine | `splatter/` crate (in-process `Session`, library only) |
| Land | GeoJSON at runtime. GDB sources: **`peaky serve` boot** validates enabled sources, auto-refreshes missing/stale/invalid when `refresh.downloadUrl` is set, then warms preview + pipeline caches under `.peaky/cache/land/`. Successful validation is fingerprinted in `validate.json` (size + mtime + required GDB layers) so unchanged sources skip re-parse on later boots. Invalid enabled source **blocks boot** until `enabled: false` in `land.yaml`. Pass `--fast-boot` to skip boot prep (`--no-land-refresh` alias). **WGS84 required.** RF scans (link solver, finder) eligible land **clips to hop/route bbox** and keeps include + exclude R-trees (no statewide boolean dissolve). Built-in map overlay **eligible** `(include − exclude) ∩ AOI` is cached by land digest (`GET …/land/overlays/eligible/geojson`) and rebuilds only when include/exclude/AOI change. Parcel-wise (exclude rings as holes only when fully inside the parcel, clip to AOI). No statewide SMA boolean. Overlay is one GeoJSON per include source; per-parcel `i_overlay` difference/intersect (non-zero fill; geo 0.28 BooleanOps panics on this data and overlapping exclude rings punched as raw holes are invalid input for map tessellators — never do that). Fully excluded parcels are omitted. Scans subtract exclude via the R-tree. Map paint stack (bottom→top): AOI, include, overlay, exclude, eligible. Link-solver hop peaks/lines raise last (above viewsheds and sites). |
| Dev | Host: `cargo run -p peaky -- serve <project-dir>` (e.g. `peaky-nevada`). **Debug builds serve `/static/` from disk** — edit JS/CSS without `cargo rebuild`; release still uses rust-embed. `--release` for long RF only. Docker: mount project at `/project` only. Skadi + map tiles under `<project>/.peaky/cache/skadi/`. Optional `SPLAT_CACHE` override. Parallelism: `simulation.max_workers.coverage` (default 2, serve warm queue; env `PEAKY_COVERAGE_WORKERS`), `simulation.max_workers.dem` (default 4, Skadi fetch pool; env `PEAKY_DEM_FETCH_WORKERS`) |
| Auto-finder | `peaky find path` — onX KML route → min-site RF chain; cache under `.peaky/cache/finder/`; **`--watch`** live MapLibre + SSE on localhost:9847 |
| Ops | Public-land site tags + FO export live in ops: `peaky_home/scripts/tag_public_land.py`, `export_blm_fo_packet.py`. **Fleet CLI** is leaf `envybot/` (`./envybot monitor` / `onboard`); nevada YAML is the book. **Do not** put passwords or keypairs in git-tracked `config.yaml`. → `ops/initiatives/peaky-fleet-management.md` |
| Reference preset | `peaky-nevada/config.yaml` (standalone project repo) |
| Releases | [`CHANGELOG.md`](CHANGELOG.md) + `./scripts/changelog.sh`; tag `v*` → GitHub Release binaries ([`docs/change-management.md`](docs/change-management.md)). EnvyOS `releases.next` pins **0.5.0** and mirrors `peaky-<ver>-<target>.tar.gz`. |

## Workspace layout

| Path | Role |
|------|------|
| `Dockerfile` | `peaky:latest` — mount project at `/project`; `gdal-bin` for `map export` |
| `cmd/peaky/` | CLI binary (`serve`, `find path`, `export`, `map export`, `freeze`, `peaks`; `serve --fast-boot`) |
| `crates/peaky-freeze/` | Official base export: bake land pipeline → `project.geojson` + distilled `config.yaml` |
| `crates/peaky-finder/` | Auto-finder: route → min-site RF chain + config patch |
| `crates/peaky-preset/` | Preset model, YAML I/O, paths, sites, home catalogs, peaks index + probe cache I/O |
| `crates/peaky-peaks/` | `peaky peaks` pipeline: OSM jeep roads, DEM hike profile, GNIS/universe → derived `index.json` |
| `crates/peaky-geo/` | GeoJSON land query, eligible land (bbox clip + include/exclude index), KML import/export, PPM polygonize |
| `crates/peaky-serve/` | Axum app, API routes, HTML, embedded static |
| `crates/peaky-map/` | `peaky map export`: fleet filter + native `splat.png` mosaic + public GeoJSON/tiles |
| `splatter/` | RF coverage engine (Skadi DEM, Fresnel/FSPL). Library only |
| `assets/static/project-map/` | Vue 3 reactive UI: `main.js` → `boot.js`, `stores/` (incl. land display helpers), `domains/` (`app.js` wiring only), `panels/` (incl. **`tag-picker.js`** — shared simple + bulk tri-state tag editor; site/peak/link sheets overlay the map), `map/` (adapters + land overlay catalog), `api/` (client, events, urls, **deep-link.js** — `?site=` / `?peak=` + camera in address bar; localStorage keeps basemap/land/viewshed prefs); debug disk-serve for `/static/` |
| `assets/finder-watch/` | Embedded MapLibre page for `peaky find path --watch` |
| `assets/templates/` | Server-rendered HTML fragments |
| `tests/fixtures/` | Golden RF/GeoJSON fixtures from v4 |

## Preset model

Same vocabulary as v4: `sites:` (slug → `name`, `loc`, optional `tags`, `height_m`, optional `node` ME key), top-level `links:`, `simulation`, `display`, `land`, **`scan:`** (peak bin size + candidate cap for link solver / alternates; Fortify returns the full in-range RF set), plus **`modem_presets`** and **`environment_presets`** (self-contained project; no `$PEAKY_HOME` inheritance). No `sites.*.type`. Tags are UI-only. Fleet bind is `sites.*.node`. Optional book files: **`nodes.yaml`** (unit → board), **`boards.yaml`** (board params). Hop/P2P and viewsheds both join them (see below).

Paths: CLI takes a project dir (or `config.yaml`). Optional slug fallback: `$PEAKY_HOME/projects/<name>/`. Split files: `sites.yaml` (**`sites:` + manual `links:`** when split), `land.yaml`, git-tracked **`peaks.yaml`** (operator overrides only: deny-by-point). Eligible catalog + access live under **`.peaky/cache/`** (gitignored): **`probe/<prefs_key>/<lat5>_<lon5>.json`** (`AccessProbe`, sole persisted access record) and **`peaks/<prefs_key>/index.json`** (derived thin rows + manifest). `read_merged_document` / `write_merged_document` peel `links` into `sites.yaml` when split. Cache root also: `skadi/` (HGT + `.map_tiles/`), `viewsheds/`, `finder/`, `land/`, **`osm/`** (Geofabrik NV PBF + `routing-v1.bin` OSM-only graph + `routing-merged.bin` OSM+GTLF jeep graph keyed by GTLF fingerprint), **`roads/`** (BLM GTLF GeoJSON per layer; 90-day refresh). Serve/peaks load merged routing cache when the fingerprint matches; otherwise OSM cache then in-memory GTLF jeep merge, then write merged. Foot layers 4–5 still build a separate trail graph. Optional `SPLAT_CACHE` overrides Skadi path. **`peaky peaks gc`** reclaims stale `probe/` and `peaks/` prefs-key dirs.

**Road sources:** `sources.osm` (Geofabrik PBF) + optional **`sources.gtlf`** (BLM Ground Transportation Linear Features MapServer layers 0–3 jeep, 4–5 foot). Config: `peaky-nevada/config.yaml` template. Inventory overlays: serve `GET …/roads/gtlf/geojson` + `…/trails/geojson`.

**Eligible peaks (`peaky peaks build`):** **`probe()`** / **`resolve_probe()`** is the single access calculator for build, serve, draft pins, and **`peaky peaks probe lat,lon`** (prints `AccessProbe` YAML to stdout; `--explain` scores candidates; **`--corridor`** lists PASS/DROP/DEDUP). Returns **`recommended`** route (preference-ranked via **`scan.peaks.preference`**, ungated) + optional full **`budget_route`** when a different park fits segment caps; **`eligible`** = in-budget route exists. Budget legs report `{used,cap,over_by}` on recommended. Precomputed **`PavedReachTree`** (unconstrained + budgeted) from all paved anchors once per routing load. Probe disk cache: `.peaky/cache/probe/<prefs_key>/<lat5>_<lon5>.json`. Build writes `.peaky/cache/peaks/<prefs_key>/index.json` (manifest + thin rows; denied points excluded). Pathfinding settings come from **`Preset::resolved_access_meta()`** (config); **`ACCESS_ALGO_VERSION` 16**, index schema **`PEAK_ALGO_VERSION` 10**. Build keeps candidates iff `probe.eligible`; overrides in **`peaks.yaml`**. Config: **`scan.peaks`** (prominence, **500 m** bins, dedup **300 m**, snap, **`difficulty`**, jeep/hike caps). Full-AOI scan replaces index; `--clean` ignores existing index for this prefs key; corridor merges. Map paints recommended + ghost alternate (`budget_route`). Orange jeep / tan trail / green bushwhack. **`GET …/peaks`** returns `missing_catalog: true` when no index; **`GET …/access/{slug}?warm=1`** resolves by slug or lat/lon via probe cache. Parallel filter (`PEAKY_PEAKS_WORKERS`). Re-run build after algo bump.

**YAML writes:** serve site edits patch the on-disk YAML tree (`insert_preset_site`, `patch_preset_site`, …) so unrelated sections keep their order. Full `save_preset` re-serializes the typed preset and should be reserved for whole-document updates.

**Viewshed quality:** `simulation.viewshed_quality` (1–5, default 3). Q1 = 128 px always; Q5 = DEM-native for radius (`ceil(radius_m / 30)`, clamp 128–4096); Q2–4 = evenly spaced rungs on the doubling ladder 128→Q5. Resolved pixel count is in cache digests (not stored in YAML). Changing quality or radius invalidates viewshed cache entries.

**Per-board hop radius:** optional **`boards.yaml`** `boards.<id>.radius_km`. Serve, finder, fortify, alternates, and `peaky map export` join `sites.*.node` → `nodes.yaml` `board` → `boards.yaml`. A pair is in hop range when distance ≤ **min(endpoint radii)**; draft pins use min(project, peer). Peaks and unbound sites use project `simulation.radius_km`. Shared RF JSON still uses project radius (mixed RAK/T096 batches). Manual `links:` rows always draw. Board ids must match canonical `nodes.yaml` values (no alias support). Missing book files fall back to project radius. `simulation.boards` is removed.

**Viewshed warm:** Progressive ladder (128→target px) per site. Always-on stderr: `[peaky] viewshed {slug} [1/N] generating 128px…` / `done (Ns)` / `progressive warm complete`. Cache hits are `--verbose` only. Map pins show a bar + label (`2/5 · 256px`) under each loading site from SSE `ladder_step`/`ladder_total`/`raster_dimension`. Pins spin only while a load is in flight; a cached overlay is republished on viewport bump; target PNG without a manifest is regenerated. **Access** is independent of viewshed warm: select a site (or pin **Access**) then `GET …/access/{slug}?warm=1`. Viewport `ensureAccessForVisibleSites` only warms the selected site and pinned slugs. Serve caches the OSM routing graph in-process (bbox + single-flight) and on disk (`.peaky/cache/osm/routing-v1.bin` OSM-only; `routing-merged.bin` after GTLF jeep merge, keyed by PBF + GTLF fingerprint + bbox + highway lists). Prefetch after listen; SSE `osm` events drive the draft-pin roads bar (`Loading OSM graph` / `Merging BLM roads N%`). Place-access HTTP (including `_draft`) reuses the graph.

## Site export (`peaky export` + map Export)

**Book → onX clipboard.** `sites.yaml` is source of truth; onX holds field stake coords until copy-back.

**CLI** — tag-filtered corridor/stake lists:

```bash
peaky export <project> --tag reno-vegas [--exclude-tag optional] [-o out.kmz]
```

Loads merged preset, keeps sites whose tags match any `--tag` (OR), drops any with `--exclude-tag`, sorts A→Z by site name (slug tie-break). Resolves each site’s viewshed PNG + hike/jeep access (same warm paths as serve; cache hits reused). Default output `{first-tag}.kmz`. Map and CLI KMZ use the same folder order.

**Map UI** — Sites sidebar lists all scoped sites (viewport when **In view** is on; canvas projection, including pitched); tag chips drive map visibility via `sites.hidden`, not list filtering. Per-site eye toggles invert effective visibility: `manualHidden` forces hide, `manualShown` force-shows past active tag chips; tag sync fills in the rest. Any tag-chip or intersect/union change clears those overrides so visibility follows the filter again. Hidden sites drop pin, viewshed, RF links, and jeep/hike access lines together. Peaks tab has the same **In view** checkbox (shared `filterByViewport`; Land tab unchanged). **Peak click preview** — catalog peak, Fortify relay, and link-solver hop clicks paint viewshed + jeep/hike access + RF legs (draft mesh for catalog peaks; scan legs for Fortify/solver). **Bulk tag** applies only to map-visible sites in the current sidebar scope (same hide rules as Export). **Export** (beside Import): POST visible slugs to `…/sites/export/kmz`; server returns KMZ for map-visible sites in the viewport (respects hide state; ignores sidebar In view). Filename `{project}-viewport.kmz`. Large exports wait on viewshed/access resolution.

Both exporters write **KMZ** (`doc.kml` + `files/`): one folder per site with placemark **pin** (tags in `ExtendedData`), **viewshed** GroundOverlay, **hike** / **jeep** LineStrings when ≥2 profile points. Staked coords are copied manually from onX into the book after the trip (no auto write-back).

## Auto-finder (`peaky find path`)

| Input | Effect |
|-------|--------|
| `--route` onX KML LineString | Ordered waypoints (lon,lat → internal lat,lon; 50 m dedupe; optional `--simplify-m`) |
| `--project` | Project dir or `config.yaml` (slug under `PEAKY_HOME/projects/` still works) |
| `--allow-tag` (default `installed`) | Existing preset sites eligible for reuse |
| `--name-prefix` + `--tag` | New peak sites written to `config.yaml` |
| `--dry-run` | Print diff; no YAML write |
| `--watch` | Live map at `http://127.0.0.1:9847/` — SSE hello first; hillshade after hello; progressive **gaps**, **search wedge/focus**, **chain_partial** + **viewshed** overlay per hop, `link_check` flashes; DEM/mask/peaks on demand; shared Skadi `Session` with finder (3D terrarium reuses loaded HGT); Ctrl+C to exit |

**Objective:** minimize distinct sites on a contiguous mutual-RF chain where each waypoint is in one-way decode viewshed of some chain site; tie-break on coarse decode bitmask union gain.

**Solver (cache schema v8):** on-demand **coverage-guided wedge search** — no corridor-wide DEM preload, no full hop-disc peak scan, no eager peak graph. Phase 0 **gap overview** on eligible land (hard spans first; cached under `.peaky/cache/finder/gaps/` keyed by waypoints + eligible-land digest + hop — **stable digest**, not invalidated by solver schema bumps). From `Pa` toward goal: **outer rings → inner**; DEM scan only the goal wedge (±2.5° first), widen until a peak is **inside Pa's one-way viewshed** (antenna AGL). **Goal waypoint stays locked** until some chain site covers it; hops past the waypoint are allowed only if they cover it. Prefer goal-covering peaks; in-viewshed relays only short of the waypoint. Then mutual RF. Peak `height_m` is antenna AGL (`None` = preset default) — never Skadi elev.

**Hop-disc and catalog DEM peaks:** 8-neighbor local maxima with ≥20 m prominence, then (hop-disc) Web-Mercator binning. Not highest-cell-per-bin (that littered flat valleys with fake peaks).

**Cache telemetry:** stderr hit/miss per op (`peaks`, `cover`, `link`, `bitmask`, `gap_overview`, `run`) unless `--quiet`; summary + `runs/{digest}/ledger.json`. `CACHE_SCHEMA_VERSION=8` (cover/link/run only; v8 = P2P at native DEM step). Gap keys use `digest_hex_stable` and reclaim prior `gaps/*.json` by identity / legacy segment-count match so solver schema bumps do not re-walk land.

**Eligible DEM masks:** per Skadi tile `.elmk` under `.peaky/cache/land/eligible/{digest}/dem_masks/` (format v2). Built by **polygon scanline burn** (R-tree → intersecting parcels → edge + even-odd fill onto 3601² grid), not per-cell point-in-polygon. Lookup is O(1) bit test; voids applied when forming the usable grid. Watch UI: on each DEM bbox ensure, finder loads/builds `.elmk` and publishes merged `dem_tiles` + `mask_tile` (HTTP `/mask` for samples).

## RF link model (canonical)

**One physics path for all hop/link decisions.** Link solver, Fortify, alternates, site-pair confirmation, linkable binned peaks, and the site link mesh must all call the same splatter P2P stack:

```
preset → CovRequest JSON (rf_json)
         → Session::seek_repeater_link_batch / site_mesh_pair_strengths
         → propagate::evaluate_mutual_site_link_strength
```

**Terrain sample step:** P2P / cover / seek links sample at **native DEM spacing** (~30 m Skadi). Viewshed rasters use `radius_m / raster_dimension` for display ray step; `raster_dimension` derives from `viewshed_quality` + radius (see Preset model).

| Consumer | v4 (do not copy) | v5 |
|----------|------------------|-----|
| Link solver / Fortify / alternates (site ↔ peak hops) | splatter P2P | `seek_repeater_link_batch` / `seek_repeater_link_margins` |
| Linkable binned peaks | `Session::linkable_binned_peaks` | Same (`evaluate_mutual_site_link_strength`) |
| Site link mesh (`GET …/links`) | viewshed footprint cover | **P2P RF** via `site_mesh_pair_strengths` |
| Link solver accept → sites | — | Same P2P; seeds mesh for created slugs; keep path sites visible |

**Viewsheds are not link truth.** Footprint polygons and PNG overlays are display/cache only. They must not gate whether two sites are linked. Optional: derive a visual "coverage overlap" hint from footprints, but `linked` / `rf_viable` / scan candidacy come from P2P only.

**Single serve module:** implement in `peaky-serve/src/rf.rs` (preset→`rf_json`, pair/batch queries, shared by `links.rs`, `link_solver.rs`, `fortify.rs`, `alternates.rs`). No duplicate RF logic in route handlers.

**Mutual decode required everywhere** — `mutual_rf_viable` / `mutual_site_link_viable`: both directions must decode (finite weaker-leg margin). One-way, out of hop range, or no decode = not linked; nothing renders as a link. Canonical helper in `peaky-serve/src/rf.rs`; splatter implements `propagate::mutual_site_link_viable`.

**Link sheet + Fortify + Link solver (map UI):** Click a drawn site link (`LINKS_LAYER`) → link side sheet with endpoint names, `distance_km`, mesh `strength`, and `GET …/links/pair?a=&b=` weaker-leg margin dB. **Fortify** (`GET …/fortify?a=&b=` + `…/fortify/scan-progress`) scans **every** catalog peak in `hop(A) ∩ hop(B)` (no corridor, along-track, site-dedup, or `scan.max_candidates` cap) and returns only peaks where **both legs are `mutual_rf_viable`**. Rows carry per-leg margins; ranked by weaker-leg margin. Purple dots + A–C–B lines when selected; **Add as site** reuses peak slug. **Link solver** (`GET …/link-solver?a=&b=&min_routes=` + scan-progress, like, accept) finds multi-hop peak chains between two sites; floating Vue panel, hop peak dots, selected route lines with margin labels, preview viewsheds (`_linksolver_{i}`), **More like this**, **Load more**, **Accept** seeds hop sites + mesh links. **Alternates**, **Fortify**, and **Link solver** clear each other when started. Solvers: `peaky-serve/src/fortify.rs`, `link_solver.rs`.

## Elevation sources

| Use | Dataset | Notes |
|-----|---------|-------|
| RF, viewsheds, link solver, site height | Skadi SRTM mirror (`<project>/.peaky/cache/skadi`, `.hgt.gz`) | Single analysis DEM; fetch pool from `simulation.max_workers.dem` (default 4). Retries: `PEAKY_SKADI_FETCH_RETRIES` (5), `PEAKY_SKADI_FETCH_TIMEOUT_SECS` (180), `PEAKY_SKADI_FETCH_CONNECT_TIMEOUT_SECS` (30) |
| Skadi basemap + 3D terrain mesh | Same Skadi mirror, rendered to hillshade/terrarium PNGs | Must match on-disk HGT before tile serves |
| USGS Topo / satellite / street basemaps | External tile APIs (MapLibre) | **Visualization only** — separate rasters from analysis DEM |

**Topo ↔ Skadi trust:** In practice, Skadi SRTM elevations align almost exactly with USGS Topo contours/shading where both are visible. When reviewing candidate sites on topo (or satellite/street), the parallel Skadi DEM used for placement and viewsheds is likely nearly identical at that location even though the basemap pixels come from a different provider.

**Links disk cache:** `links/mesh.json` carries `links_model: "p2p-v7"` (mutual-decode-only links); fingerprint includes radius, modem/env/heights, sites, manual links — not viewshed quality/px. Older model tags rejected on read.

## Serve (implemented vs stub)

| Area | State |
|------|-------|
| Project page at `/`, sites CRUD, KML import | Implemented (no project listing). Site sheet **Elevation** is Skadi AMSL via `GET …/elev?lat=&lon=` (not YAML; `height_m` stays antenna AGL) |
| Viewshed PNG on demand (`splatter::Session` + cache) | Implemented |
| Home modem/environment catalogs | Implemented |
| SSE `/events` | Implemented (hello + keepalive; publish on warm TBD) |
| Links mesh, warm scheduler | Implemented (P2P mesh, warm queue, SSE) |
| Link solver (`/link-solver`, scan-progress, like, accept) | Implemented — multi-hop peak routes between two preset sites (`link_solver.rs`); P2P margins via `seek_repeater_link_margins`; ranked routes with bottleneck margin, distance, peak chain, access difficulty; accept creates hop sites from catalog peaks and seeds mesh links |
| Site alternates (`/alternates`, `/alternates/scan-progress`) | Implemented — same peak candidate pool as Fortify (peaks index in the shared N-anchor RF lens), plus preset sites in lens; mutual P2P to all linked peers; site sheet **Find alternates** + **Add as site** |
| Land list + layer GeoJSON | Implemented (plus built-in eligible overlay GeoJSON, digest-cached) |
| Skadi map tiles (`/api/dem/hillshade`, `/api/dem/terrarium`) + point elev (`GET …/elev`) | Implemented — PNG cache `<project>/.peaky/cache/skadi/.map_tiles/v3/`; render only when all required HGT on disk (503 until ready); hillshade uses padded HGT ring; **AOI HGT prefetch on project page load** (background); map tile prefetch capped (`PEAKY_DEM_MAP_QUEUE_CAP`, default 128). Point elev ensures the HGT tile then bilinear-samples AMSL |

## Ops (outside Peaky)

BLM tagging/export are ops scripts under `ops/peaky_home/scripts/` — Peaky has no BLM-specific CLI. Land sources: `config.yaml` → `land.sources`; refresh via `peaky land refresh`.

## Active threads

- **v0.6.0 (planned):** greenfield Tauri app — delete `peaky-serve`/Axum/rust-embed at cutover; `peaky-core` + `apps/peaky/` (invoke, events, `peaky://` tiles); CLI → `find`/`freeze` only; desktop first, mobile later. Plan: [`docs/plans/v0.6.0-tauri-greenfield.md`](docs/plans/v0.6.0-tauri-greenfield.md).
- **Ops (not this week's v5 work):** fleet-management / secrets-near-preset — `ops/initiatives/peaky-fleet-management.md`. No schema for creds until a non-git store is picked.
- **Community harvest (backlog 09-12):** CoreScope → `community.yaml` overlay; hint links only, not ST backbone — `docs/plans/community-site-harvest.md`, `ops/initiatives/peaky-community-harvest.md`.
- Eligible-land layer envelope cache so RF scans can skip non-overlapping SMA files without a parse
- Integration tests (digest parity vs fixtures)
- Finder `--watch` boot: progressive SSE replay, deferred hillshade, heavy-fetch queue (rebuild + hard refresh)
- Watch map: `viewshed` SSE + `/viewshed/{digest}/splat.png`; chain hop triggers progressive splat warm (serve pipeline)
