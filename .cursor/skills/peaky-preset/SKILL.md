---
name: peaky-preset
description: >-
  Peaky preset YAML: Preset model, config.yaml schema, path resolution,
  locked ruamel writes, site suggest append/remove. Use when editing presets,
  extending Preset fields, build --suggest, or PLSS/MLRS enrichment.
---

# Peaky preset

Read [MEMORY.md](../../MEMORY.md) first. Greenfield: break preset schema cleanly — update reference `config.yaml`, callers, and MEMORY together; no legacy key support.

## Layout

- Reference: `projects/nevada/config.yaml`
- Run CLI from project dir: `require_cwd_config_yaml()` → `./config.yaml`
- Resolve by slug: `resolve_preset_yaml_arg("nevada")` → `projects/nevada/config.yaml`

## Schema (`Preset` in `sites_job.py`)

| Section | Purpose |
|---------|---------|
| `simulation` | RF modem/env presets, splatter params, `max_workers` |
| `display` | Viewshed raster + `display.kml` / `display.kmz` presentation |
| `land` | GDB layer refs: `reference`, `aoi`, `include`, `exclude` |
| `mesh` | Pairwise/depth analysis knobs |
| `suggest` | Site planner for `build --suggest`; `mesh_backbone.goals` = coverage attractors |
| `sites` | Repeaters with viewsheds — see **Sites vs goals** below |
| `links` | Manual mutual site pairs `[[a, b], …]` |

Legacy `.json` presets are rejected.

## Sites vs goals

**Sites** (`sites:`) are **repeaters** — each has a splatter viewshed. **Goals** (`suggest.mesh_backbone.goals`) are **coverage attractors** — map points where coverage is desired; no viewshed until a site captures them.

### Site types (`SiteType`)

| `type` | Role | Position |
|--------|------|----------|
| `installed` | Deployed repeater | Fixed — must not move |
| `planned` | User-committed future site | Fixed — must not move |
| `suggested` | `build --suggest` output | May move — `--replace-suggested` drops prior suggestions |

Suggest **never relocates** `installed` or `planned` sites. Only `type: suggested` entries are removed/replaced on re-suggest.

### Goals

- User-defined `loc: [lat, lon]` under `suggest.mesh_backbone.goals` (slug keys).
- Planner attractors for mesh-backbone / land-grab strategies.
- Ephemeral `bridge:*` goals appear when healing disconnected mesh components.
- A goal is “captured” when an existing site’s viewshed covers the point (or hop-connectivity rules for bridge goals).

## Path helpers

```python
resolved_preset_build_dir(preset_path)   # <preset-dir>/build
resolved_bundle_dir(preset_path=...)     # build/bundle
resolved_preset_clips_dir(preset_path)   # build/clips
resolved_skadi_mirror_dir()              # SPLAT_CACHE / ~/.peaky/splat_cache
```

## Writes (locked)

- `update_preset_yaml_tree(path, mutator)` — read, mutate, validate, atomic write
- `preset_yaml_transaction(path)` — hold lock for multi-step R-M-W
- Site suggest: `site_suggestions/preset_io.py` (append/remove suggested sites)
- PLSS enrichment: `plss_mlrs_fetch.py`

Never bypass with direct `yaml.dump`.

## Extending schema

1. Add field to Pydantic model in `sites_job.py`
2. Document in reference `projects/nevada/config.yaml` with `#` rationale
3. Update MEMORY.md in same change set
