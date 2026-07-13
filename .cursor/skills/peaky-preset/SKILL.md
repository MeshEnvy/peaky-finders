---
name: peaky-preset
description: >-
  Peaky preset YAML: slim Preset model, config.yaml schema, locked ruamel writes.
  Use when editing presets, site tags, or serve PATCH handlers.
---

# Peaky preset

Read [MEMORY.md](../../MEMORY.md) first.

## Layout

- Global: `$PEAKY_HOME/config.yaml`, `modems.yaml`, `environments.yaml` (`core/home/`)
- Project: `$PEAKY_HOME/projects/<slug>/config.yaml`
- Reference: `peaky_home/projects/sample/config.yaml`
- Model / I/O: `core/preset/` (`Preset`, `load_preset`, `update_preset_yaml_tree`)

## Schema (serve-only slim preset)

| Section | Purpose |
|---------|---------|
| `simulation` | `modem` / `environment` names, `radius_km`, `raster_dimension`, chains |
| `display` | Viewshed colormap / transparency |
| `sites` | `name`, `loc`, optional `tags`, `height_m`, metadata — **no `type`** |
| `links` | Manual pairs `[[a, b], …]` |
| `land` | Project-only GDB overlay registry (`land.sources`) — informational v1 |

`load_preset` deep-merges global ← project. Writes prune keys equal to defaults.

## Land (v1)

Project-only (`_PROJECT_ONLY_KEYS`). GDBs live under `projects/<slug>/data/`; import **registers** path + layer list (no file copy).

```yaml
land:
  sources:
    blm_field_offices:
      path: data/BLM_NV_Field_Office_Boundary_Polygons.gdb
      layers: [admu_ofc_poly]
      label: BLM field offices   # optional
      layer_styles:
        admu_ofc_poly:
          color: "#4a6cf7"
          opacity: 0.48
```

- `path` — relative to project dir; must resolve under `data/` and end with `.gdb`
- `layer_styles` — optional per-layer `{color, opacity}` (defaults: `#4a6cf7` / `0.48`)
- Cache: `<project>/.peaky/cache/land/<source_id>/<layer>.geojson` + `manifest.json` (digest from GDB mtime + layer name)

## Sites

Optional `tags: [lowercase, …]` — UI filter labels only. Reject `sites.*.type` on load.

Rule: `sites-and-tags`.

## Path helpers (`core/preset/paths.py`)

```python
resolved_preset_cache_dir(preset_path)  # <preset>/.peaky/cache
resolved_viewshed_root(preset_path)     # …/viewsheds
resolved_viewshed_root(preset_path)
peaky_home()
peaky_projects_dir()
```
