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
| `display` | KML/overlay styling |
| `bundle` | GDB layer refs (AOI/include/exclude), mesh/suggest config |
| `sites` | Site entries: `loc`, `sees`, optional `plss`/`mlrs` |

Legacy `.json` presets are rejected.

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
