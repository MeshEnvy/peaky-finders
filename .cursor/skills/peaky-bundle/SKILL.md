---
name: peaky-bundle
description: >-
  Peaky bundle pipeline: GDB clip layers, composites, eligible land GPKG,
  resolve sidecars, Skadi DEM prefetch. Use when working on bundle targets,
  GDB inputs, eligible land filtering, or DEM tile fetch.
---

# Peaky bundle

Read [MEMORY.md](../../MEMORY.md) for artifact paths.

## CLI targets

```bash
peaky bundle clip aoi <layer>
peaky bundle composite aoi|include|exclude
peaky bundle eligible
peaky bundle resolve
peaky bundle plss
peaky bundle dem [--tile N37W117]
```

DAG nodes in `build_graph.py` mirror these; keep granular commands consistent with executor wiring.

## Modules

| Module | Role |
|--------|------|
| `bundle_clips.py` | Clip GDB layers, build composites, eligible workspace |
| `bundle_build.py` | Eligible land-use GDF, bundle resolve, DEM bulk prefetch |
| `skadi_dem.py` | AWS Skadi SRTM tile fetch + mirror |
| `plss_mlrs_fetch.py` | BLM CadNSDI PLSS/MLRS lookup (via `CADNSDI_HTTP_POOL`) |
| `webmap_arcgis.py` | ArcGIS REST helpers (via `ARCGIS_HTTP_POOL`) |

## Data flow

```
land.* GDB paths (preset) → clip layers → composites → eligible GPKG
  → resolve.json sidecars → viewshed/mesh inputs
```

GDB paths resolve under `<preset-dir>/<land.inputs_root>` unless absolute.

## Skadi DEM

- Global mirror: `SPLAT_CACHE` or `<peaky_home>/splat_cache`
- Fetches via `SKADI_HTTP_POOL`; parallel prefetch with worker caps
- `./peaky` mounts host cache at `/.peaky/splat_cache`

## HTTP

All outbound fetches through `http_pool.py` — see `http-fetch-pool` rule.
