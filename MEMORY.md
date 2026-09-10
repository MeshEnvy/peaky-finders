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
| Interface | `peaky serve <project>` — web UI for one project directory |
| RF engine | `splatter/` crate (in-process `Session`, library only) |
| Land | GeoJSON at runtime. GDB sources: **`peaky serve` boot** validates enabled sources, auto-refreshes missing/stale/invalid when `refresh.downloadUrl` is set, then warms preview + pipeline caches under `.peaky/cache/land/`. Successful validation is fingerprinted in `validate.json` (size + mtime + required GDB layers) so unchanged sources skip re-parse on later boots. Invalid enabled source **blocks boot** until `enabled: false` in `land.yaml`. Pass `--fast-boot` to skip boot prep (`--no-land-refresh` alias). **WGS84 required.** Seek/finder eligible land **clips to hop/route bbox** and keeps include + exclude R-trees (no statewide boolean dissolve). Built-in map overlay **eligible** `(include − exclude) ∩ AOI` is cached by land digest (`GET …/land/overlays/eligible/geojson`) and rebuilds only when include/exclude/AOI change. Parcel-wise (exclude rings as holes only when fully inside the parcel, clip to AOI). No statewide SMA boolean. Overlay is one GeoJSON per include source; per-parcel `i_overlay` difference/intersect (non-zero fill; geo 0.28 BooleanOps panics on this data and overlapping exclude rings punched as raw holes are invalid input for map tessellators — never do that). Fully excluded parcels are omitted. Seek still subtracts exclude via the R-tree. Map paint stack (bottom→top): AOI, include, overlay, exclude, eligible. Seek goal wedge raises last (above viewsheds and sites). |
| Dev | Host: `cargo run -p peaky -- serve <project-dir>` (e.g. `peaky-nevada`). **Debug builds serve `/static/` from disk** — edit JS/CSS without `cargo rebuild`; release still uses rust-embed. `--release` for long RF only. Docker: mount project at `/project` only. Skadi + map tiles under `<project>/.peaky/cache/skadi/`. Optional `SPLAT_CACHE` override. Parallelism: `simulation.max_workers.coverage` (default 2, serve warm queue; env `PEAKY_COVERAGE_WORKERS`), `simulation.max_workers.dem` (default 4, Skadi fetch pool; env `PEAKY_DEM_FETCH_WORKERS`) |
| Auto-finder | `peaky find path` — onX KML route → min-site RF chain; cache under `.peaky/cache/finder/`; **`--watch`** live MapLibre + SSE on localhost:9847 |
| Ops | Public-land site tags + FO export live in ops: `peaky_home/scripts/tag_public_land.py`, `export_blm_fo_packet.py`. **Fleet CLI** is leaf `envybot/` (`./envybot monitor` / `onboard`); nevada YAML is the book. **Do not** put passwords or keypairs in git-tracked `config.yaml`. → `ops/initiatives/peaky-fleet-management.md` |
| Reference preset | `peaky-nevada/config.yaml` (standalone project repo) |
| Releases | [`CHANGELOG.md`](CHANGELOG.md) + `./scripts/changelog.sh`; tag `v*` → GitHub Release binaries ([`docs/change-management.md`](docs/change-management.md)). EnvyOS `releases.next` pins **0.5.0** and mirrors `peaky-<ver>-<target>.tar.gz`. |

## Workspace layout

| Path | Role |
|------|------|
| `Dockerfile` | `peaky:latest` — mount project at `/project` |
| `cmd/peaky/` | CLI binary (`serve`, `find path`, `freeze`; `serve --fast-boot`) |
| `crates/peaky-freeze/` | Official base export: bake land pipeline → `project.geojson` + distilled `config.yaml` |
| `crates/peaky-finder/` | Auto-finder: route → min-site RF chain + config patch |
| `crates/peaky-preset/` | Preset model, YAML I/O, paths, sites, home catalogs |
| `crates/peaky-geo/` | GeoJSON land query, eligible land (bbox clip + include/exclude index), KML import, PPM polygonize |
| `crates/peaky-serve/` | Axum app, API routes, HTML, embedded static |
| `splatter/` | RF coverage engine (Skadi DEM, Fresnel/FSPL). Library only |
| `assets/static/project-map/` | Vue 3 reactive UI: `main.js` → `boot.js`, `stores/` (incl. land display helpers), `domains/` (`app.js` wiring only), `panels/`, `map/` (adapters + land overlay catalog), `api/` (client, events, urls); debug disk-serve for `/static/` |
| `assets/finder-watch/` | Embedded MapLibre page for `peaky find path --watch` |
| `assets/templates/` | Server-rendered HTML fragments |
| `tests/fixtures/` | Golden RF/GeoJSON fixtures from v4 |

## Preset model

Same vocabulary as v4: `sites:` (slug → `name`, `loc`, optional `tags`, `height_m`, optional `node` ME key), top-level `links:`, `simulation`, `display`, `land`, `seek`, plus **`modem_presets`** and **`environment_presets`** (self-contained project; no `$PEAKY_HOME` inheritance). No `sites.*.type`. Tags are UI-only. Fleet bind is `sites.*.node`; nodes.yaml is not read for RF.

Paths: CLI takes a project dir (or `config.yaml`). Optional slug fallback: `$PEAKY_HOME/projects/<name>/`. Cache root: `<project>/.peaky/cache/` — `skadi/` (HGT + `.map_tiles/`), `viewsheds/`, `finder/`, `land/`. Optional `SPLAT_CACHE` overrides Skadi path. **PLSS:** not in Peaky — ops `tag_public_land.py` (CadNSDI → preset YAML); export runs it as prep.

**YAML writes:** serve site edits patch the on-disk YAML tree (`insert_preset_site`, `patch_preset_site`, …) so unrelated sections keep their order. Full `save_preset` re-serializes the typed preset and should be reserved for whole-document updates. A `seek.plan` that names a missing site is dropped on load and when that site is deleted.

**Viewshed quality:** `simulation.viewshed_quality` (1–5, default 3). Q1 = 128 px always; Q5 = DEM-native for radius (`ceil(radius_m / 30)`, clamp 128–4096); Q2–4 = evenly spaced rungs on the doubling ladder 128→Q5. Resolved pixel count is in cache digests (not stored in YAML). Changing quality or radius invalidates viewshed cache entries.

**Viewshed warm:** Progressive ladder (128→target px) per site. Always-on stderr: `[peaky] viewshed {slug} [1/N] generating 128px…` / `done (Ns)` / `progressive warm complete`. Cache hits are `--verbose` only. Map pins show a bar + label (`2/5 · 256px`) under each loading site from SSE `ladder_step`/`ladder_total`/`raster_dimension`. Pins spin only while a load is in flight; a cached overlay is republished on viewport bump; target PNG without a manifest is regenerated.

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

**Hop-disc peaks:** 8-neighbor local maxima with ≥20 m prominence, then Web-Mercator binning. Not highest-cell-per-bin (that littered flat valleys with fake peaks).

**Cache telemetry:** stderr hit/miss per op (`peaks`, `cover`, `link`, `bitmask`, `gap_overview`, `run`) unless `--quiet`; summary + `runs/{digest}/ledger.json`. `CACHE_SCHEMA_VERSION=8` (cover/link/run only; v8 = P2P at native DEM step). Gap keys use `digest_hex_stable` and reclaim prior `gaps/*.json` by identity / legacy segment-count match so solver schema bumps do not re-walk land.

**Eligible DEM masks:** per Skadi tile `.elmk` under `.peaky/cache/land/eligible/{digest}/dem_masks/` (format v2). Built by **polygon scanline burn** (R-tree → intersecting parcels → edge + even-odd fill onto 3601² grid), not per-cell point-in-polygon. Lookup is O(1) bit test; voids applied when forming the usable grid. Watch UI: on each DEM bbox ensure, finder loads/builds `.elmk` and publishes merged `dem_tiles` + `mask_tile` (HTTP `/mask` for samples).

## RF link model (canonical)

**One physics path for all hop/link decisions.** Goal seek, site-pair confirmation, linkable binned peaks, and the site link mesh must all call the same splatter P2P stack:

```
preset → CovRequest JSON (rf_json)
         → Session::seek_repeater_link_batch / site_mesh_pair_strengths
         → propagate::evaluate_mutual_site_link_strength
```

**Terrain sample step:** P2P / cover / seek links sample at **native DEM spacing** (~30 m Skadi). Viewshed rasters use `radius_m / raster_dimension` for display ray step; `raster_dimension` derives from `viewshed_quality` + radius (see Preset model).

| Consumer | v4 (do not copy) | v5 |
|----------|------------------|-----|
| Goal seek (sites, goal, peaks) | splatter P2P | `seek_repeater_link_batch` |
| Linkable binned peaks | `Session::linkable_binned_peaks` | Same (`evaluate_mutual_site_link_strength`) |
| Site link mesh (`GET …/links`) | viewshed footprint cover | **P2P RF** via `site_mesh_pair_strengths` |
| Convert plan → sites | — | Same P2P; seeds mesh for created slugs; keep path sites visible |

**Viewsheds are not link truth.** Footprint polygons and PNG overlays are display/cache only. They must not gate whether two sites are linked. Optional: derive a visual "coverage overlap" hint from footprints, but `linked` / `rf_viable` / seek candidacy come from P2P only.

**Single serve module:** implement in `peaky-serve/src/rf.rs` (preset→`rf_json`, pair/batch queries, shared by `links.rs` and `seek.rs`). No duplicate RF logic in route handlers.

**Mutual = strong, one-way = weak** (map line styling). Out of hop range or no decode = not linked.

## Elevation sources

| Use | Dataset | Notes |
|-----|---------|-------|
| RF, viewsheds, seek, site height | Skadi SRTM mirror (`<project>/.peaky/cache/skadi`, `.hgt.gz`) | Single analysis DEM; fetch pool from `simulation.max_workers.dem` (default 4). Retries: `PEAKY_SKADI_FETCH_RETRIES` (5), `PEAKY_SKADI_FETCH_TIMEOUT_SECS` (180), `PEAKY_SKADI_FETCH_CONNECT_TIMEOUT_SECS` (30) |
| Skadi basemap + 3D terrain mesh | Same Skadi mirror, rendered to hillshade/terrarium PNGs | Must match on-disk HGT before tile serves |
| USGS Topo / satellite / street basemaps | External tile APIs (MapLibre) | **Visualization only** — separate rasters from analysis DEM |

**Topo ↔ Skadi trust:** In practice, Skadi SRTM elevations align almost exactly with USGS Topo contours/shading where both are visible. When reviewing candidate sites on topo (or satellite/street), the parallel Skadi DEM used for placement and viewsheds is likely nearly identical at that location even though the basemap pixels come from a different provider.

**Links disk cache:** `links/mesh.json` carries `links_model: "p2p-v5"` (DEM-native P2P step); fingerprint includes radius, modem/env/heights, sites, manual links — not viewshed quality/px. Older model tags rejected on read.

## Serve (implemented vs stub)

| Area | State |
|------|-------|
| Project page at `/`, sites CRUD, KML import | Implemented (no project listing) |
| Viewshed PNG on demand (`splatter::Session` + cache) | Implemented |
| Home modem/environment catalogs | Implemented |
| SSE `/events` | Implemented (hello + keepalive; publish on warm TBD) |
| Links mesh, warm scheduler | Implemented (P2P mesh, warm queue, SSE) |
| Goal seek (`/seek/candidates`, scan-progress, plan, convert-to-sites) | Implemented (P2P via `seek.rs` + `seek_path.rs` + `seek_repeater_link_margins`). Two generators, one ranker: **approach** (goal out of hop range) = local-max peaks in progress lens (hop disc ∩ closer to goal than start); **landing** (goal in hop range, start has no RF) = elevation-blind grid over start-disc ∩ goal-disc (RF lens), coarse then refine around completers, spatial diversify. Site candidates use the same progress lens from `from`. **Forward-path gate:** non-completers need an RF path to goal via preset sites ∪ scan peaks with strictly decreasing goal distance each hop (`seek_path.rs` DP). Rank: completes goal → goal progress (non-completers) → extra P2P into start-hop-disc sites → weaker-leg margin → forward reach. Elevation is not a score. Convert seeds site-mesh pairs for new slugs and bypasses tag-filter on the whole path |
| Site alternates (`/alternates`, `/alternates/scan-progress`) | Implemented — N-anchor RF lens (hop-disc ∩), grid + ridge bins, mutual P2P to all linked peers; site sheet **Find alternates** + **Add as site** |
| Land list + layer GeoJSON | Implemented (plus built-in eligible overlay GeoJSON, digest-cached) |
| Skadi map tiles (`/api/dem/hillshade`, `/api/dem/terrarium`) | Implemented — PNG cache `<project>/.peaky/cache/skadi/.map_tiles/v3/`; render only when all required HGT on disk (503 until ready); hillshade uses padded HGT ring; **AOI HGT prefetch on project page load** (background); map tile prefetch capped (`PEAKY_DEM_MAP_QUEUE_CAP`, default 128) |

## Ops (outside Peaky)

BLM tagging/export are ops scripts under `ops/peaky_home/scripts/` — Peaky has no BLM-specific CLI. Land sources: `config.yaml` → `land.sources`; refresh via `peaky land refresh`.

## Active threads

- **v0.6.0 (planned):** greenfield Tauri app — delete `peaky-serve`/Axum/rust-embed at cutover; `peaky-core` + `apps/peaky/` (invoke, events, `peaky://` tiles); CLI → `find`/`freeze` only; desktop first, mobile later. Plan: [`docs/plans/v0.6.0-tauri-greenfield.md`](docs/plans/v0.6.0-tauri-greenfield.md).
- **Ops (not this week's v5 work):** fleet-management / secrets-near-preset — `ops/initiatives/peaky-fleet-management.md`. No schema for creds until a non-git store is picked.
- Eligible-land layer envelope cache so seek can skip non-overlapping SMA files without a parse
- Integration tests (digest parity vs fixtures)
- Finder `--watch` boot: progressive SSE replay, deferred hillshade, heavy-fetch queue (rebuild + hard refresh)
- Watch map: `viewshed` SSE + `/viewshed/{digest}/splat.png`; chain hop triggers progressive splat warm (serve pipeline)
