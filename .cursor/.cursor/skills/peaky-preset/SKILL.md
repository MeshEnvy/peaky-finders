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
| `land` | Project-only GDB overlay registry (`land.sources`, `land.sidebar`) — attribute filters + style-by-value v2 |

`load_preset` deep-merges global ← project. Writes prune keys equal to defaults.

## Land (v2)

Project-only (`_PROJECT_ONLY_KEYS`). GDBs live under `projects/<slug>/data/`; import **registers** path + structured layer entries (no file copy).

```yaml
land:
  sources:
    blm_sma:
      path: data/BLM_Nevada_Surface_Management_Agency.gdb
      label: BLM surface management
      layers:
        - name: Land_Status_Dis
          include:
            - field: ABBR
              values: [BLM, FS]
          label_field: NAME
          style_field: ABBR
          style:
            BLM: { color: "#f4a261", opacity: 0.55 }
            FS: { color: "#2a9d8f", opacity: 0.55 }
        - name: admu_ofc_poly
          style: { color: "#4a6cf7", opacity: 0.48 }
  sidebar:
    folders:
      - id: federal
        label: Federal lands
        sources: [blm_sma]
    unfiled_sources: []
```

- `path` — relative to project dir; must resolve under `data/` and end with `.gdb`
- `layers[]` — `LandLayerEntry`: `name`, optional `id`, `include`/`exclude` (`field` + `values`), `label_field`, `style_field`, `style` (flat `{color, opacity}` or per-value map when `style_field` set)
- `sidebar` — optional folder layout: `folders[]` (`id`, `label`, `sources[]` source ids), `unfiled_sources[]` (sources not in any folder). New imports append to `unfiled_sources`.
- Legacy `layers: [str]` + top-level `layer_styles` coerced to layer objects on load
- GeoJSON route key: `layers/<layerKey>/geojson` where `layerKey` = slug(`id`) or slug(`name`)
- Cache: `<project>/.peaky/cache/land/<source_id>/<layerKey>.geojson` + digest (GDB mtime, filters, label/style fields)

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
