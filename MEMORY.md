# Peaky Finders v5 — project memory

Living snapshot of **current** architecture. **Agents: read before substantive work; update in the same change set when anything below shifts.**

## Agent contract

1. **Read first** — Load MEMORY.md before tasks touching presets, serve, RF, splatter, or CLI.
2. **Update always** — Architecture/API/path/workflow changes → update MEMORY.md before marking done.
3. **Greenfield** — v4 is read-only reference. Break freely in v5; no Python/Docker `./peaky` wrapper.

## Status

| Item | State |
|------|-------|
| Repo | `peaky-finders-v5` — pure Rust workspace |
| v4 | Frozen reference; do not delete until v5 soak |
| Interface | `peaky serve` — progressive web UI over preset YAML |
| RF engine | `splatter/` crate (in-process `Session`, no PyO3) |
| Land | GeoJSON-only at runtime (no GDB/GDAL). GDB preset paths resolve via `data/*.geojson` fallbacks or `.peaky/cache/land/` exports. **WGS84 required.** |
| Dev | `cargo build`, `cargo test`, `cargo run -p peaky -- serve` (dev = fast incremental). Use `--release` for long RF runs only; release uses LTO and rebuilds slowly. Finder RF phases use rayon; DEM fetch pool `PEAKY_DEM_FETCH_WORKERS` (default 8) |
| Auto-finder | `peaky find path` — onX KML route → min-site RF chain; cache under `.peaky/cache/finder/`; **`--watch`** live MapLibre + SSE on localhost:9847 |
| Ops | Public-land site tags + FO export live in ops: `peaky_home/scripts/tag_public_land.py`, `export_blm_fo_packet.py`. **Fleet-tool direction (ops, 08-14, speculative):** nevada YAML is the canonical site/fleet list; later creds + telemetry history may live next to the preset. **Do not** put passwords or keypairs in git-tracked `config.yaml`. → `ops/initiatives/peaky-fleet-management.md` |
| Reference preset | `$PEAKY_HOME/projects/nevada/config.yaml` (default: `ops/peaky_home`) |

## Workspace layout

| Path | Role |
|------|------|
| `cmd/peaky/` | CLI binary (`serve`, `find path`) |
| `crates/peaky-finder/` | Auto-finder: route → min-site RF chain + config patch |
| `crates/peaky-preset/` | Preset model, YAML I/O, paths, sites, home catalogs |
| `crates/peaky-geo/` | GeoJSON land query, eligible land, KML import, PPM polygonize |
| `crates/peaky-serve/` | Axum app, API routes, HTML, embedded static |
| `splatter/` | RF coverage engine (Skadi DEM, Fresnel/FSPL) |
| `assets/static/project-map/` | ESM modules: `main.js`, `legacy.js`, `constants.js`, `geo.js`, `viewshed-raster.js`, `land/`, `seek.js`, `viewsheds.js`, `links.js`, …; `app.css`, favicons (rust-embed) |
| `assets/finder-watch/` | Embedded MapLibre page for `peaky find path --watch` |
| `assets/templates/` | Server-rendered HTML fragments |
| `tests/fixtures/` | Golden RF/GeoJSON fixtures from v4 |

## Preset model

Same vocabulary as v4: `sites:` (slug → `name`, `loc`, optional `tags`, `height_m`), top-level `links:`, `simulation`, `display`, `land`, `seek`. No `sites.*.type`. Tags are UI-only.

Paths: `PEAKY_HOME` → projects under `projects/<slug>/config.yaml`. Cache: `<preset-dir>/.peaky/cache/viewsheds/`; finder cache: `<preset-dir>/.peaky/cache/finder/`. **PLSS:** not in Peaky — ops `tag_public_land.py` (CadNSDI → preset YAML); export runs it as prep.

**YAML writes:** serve site edits patch the on-disk YAML tree (`insert_preset_site`, `patch_preset_site`, …) so unrelated sections keep their order. Full `save_preset` re-serializes the typed preset and should be reserved for whole-document updates.

**Viewshed quality:** `simulation.viewshed_quality` (1–5, default 3). Q1 = 128 px always; Q5 = DEM-native for radius (`ceil(radius_m / 30)`, clamp 128–4096); Q2–4 = evenly spaced rungs on the doubling ladder 128→Q5. Resolved pixel count is in cache digests (not stored in YAML). Changing quality or radius invalidates viewshed cache entries.

**Viewshed warm:** Progressive ladder (128→target px) per site. Always-on stderr: `[peaky] viewshed {slug} [1/N] generating 128px…` / `done (Ns)` / `progressive warm complete`. Cache hits are `--verbose` only. Map pins show a bar + label (`2/5 · 256px`) under each loading site from SSE `ladder_step`/`ladder_total`/`raster_dimension`.

## Auto-finder (`peaky find path`)

| Input | Effect |
|-------|--------|
| `--route` onX KML LineString | Ordered waypoints (lon,lat → internal lat,lon; 50 m dedupe; optional `--simplify-m`) |
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
preset → CovRequest JSON (rf_json) → Session::link_eval / link_mutual_viable / link_mutual_batch
         → propagate::evaluate_link / evaluate_mutual_link_viable
```

**Terrain sample step:** P2P / cover / seek links sample at **native DEM spacing** (~30 m Skadi). Viewshed rasters use `radius_m / raster_dimension` for display ray step; `raster_dimension` derives from `viewshed_quality` + radius (see Preset model).

| Consumer | v4 (do not copy) | v5 |
|----------|------------------|-----|
| Goal seek (sites, goal, peaks) | splatter P2P | Same |
| Linkable binned peaks | `Session::linkable_binned_peaks` | Same |
| Site link mesh (`GET …/links`) | viewshed footprint cover | **P2P RF** via `Session::link_strength_batch` |

**Viewsheds are not link truth.** Footprint polygons and PNG overlays are display/cache only. They must not gate whether two sites are linked. Optional: derive a visual "coverage overlap" hint from footprints, but `linked` / `rf_viable` / seek candidacy come from P2P only.

**Single serve module:** implement in `peaky-serve/src/rf.rs` (preset→`rf_json`, pair/batch queries, shared by `links.rs` and `seek.rs`). No duplicate RF logic in route handlers.

**Mutual = strong, one-way = weak** (map line styling). Out of hop range or no decode = not linked.

## Elevation sources

| Use | Dataset | Notes |
|-----|---------|-------|
| RF, viewsheds, seek, site height | Skadi SRTM mirror (`$SPLAT_CACHE`, `.hgt.gz`) | Single analysis DEM; parallel fetch pool `PEAKY_DEM_FETCH_WORKERS` (default 8). Retries: `PEAKY_SKADI_FETCH_RETRIES` (5), `PEAKY_SKADI_FETCH_TIMEOUT_SECS` (180), `PEAKY_SKADI_FETCH_CONNECT_TIMEOUT_SECS` (30) |
| Skadi basemap + 3D terrain mesh | Same Skadi mirror, rendered to hillshade/terrarium PNGs | Must match on-disk HGT before tile serves |
| USGS Topo / satellite / street basemaps | External tile APIs (MapLibre) | **Visualization only** — separate rasters from analysis DEM |

**Topo ↔ Skadi trust:** In practice, Skadi SRTM elevations align almost exactly with USGS Topo contours/shading where both are visible. When reviewing candidate sites on topo (or satellite/street), the parallel Skadi DEM used for placement and viewsheds is likely nearly identical at that location even though the basemap pixels come from a different provider.

**Links disk cache:** `links/mesh.json` carries `links_model: "p2p-v5"` (DEM-native P2P step); fingerprint includes radius, modem/env/heights, sites, manual links — not viewshed quality/px. Older model tags rejected on read.

## Serve (implemented vs stub)

| Area | State |
|------|-------|
| Landing, project pages, sites CRUD, KML import | Implemented |
| Viewshed PNG on demand (`splatter::Session` + cache) | Implemented |
| Home modem/environment catalogs | Implemented |
| SSE `/events` | Implemented (hello + keepalive; publish on warm TBD) |
| Links mesh, warm scheduler | Implemented (P2P mesh, warm queue, SSE) |
| Goal seek (`/seek/candidates`, scan-progress, plan, convert-to-sites) | Implemented (P2P via `seek.rs` + `Session::linkable_binned_peaks`). Start `<select>` matches entity-panel visibility (tag filter **or** post-add bypass) and refreshes on site add/delete |
| Land list + layer GeoJSON | Implemented |
| Skadi map tiles (`/api/dem/hillshade`, `/api/dem/terrarium`) | Implemented — PNG cache `$SPLAT_CACHE/.map_tiles/v3/`; render only when all required HGT on disk (503 until ready); hillshade uses padded HGT ring; **AOI HGT prefetch on project page load** (background); map tile prefetch capped (`PEAKY_DEM_MAP_QUEUE_CAP`, default 128) |

## Ops (outside Peaky)

BLM tagging/export are ops scripts under `ops/peaky_home/scripts/` — Peaky has no BLM-specific CLI. Nevada SMA/FO GeoJSON: `projects/nevada/data/blm-*.geojson`.

## Active threads

- **Ops (not this week's v5 work):** fleet-management / secrets-near-preset — `ops/initiatives/peaky-fleet-management.md`. No schema for creds until a non-git store is picked.
- Seek eligible-land WKB cache + geo boolean safety (v4 `union.wkb` parity)
- Integration tests (digest parity vs fixtures)
- Finder `--watch` boot: progressive SSE replay, deferred hillshade, heavy-fetch queue (rebuild + hard refresh)
- Watch map: `viewshed` SSE + `/viewshed/{digest}/splat.png`; chain hop triggers progressive splat warm (serve pipeline)
