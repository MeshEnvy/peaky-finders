---
name: peaky-preset
description: >-
  Peaky preset YAML: Preset model, config.yaml schema, path resolution,
  locked ruamel writes, site suggest append/remove. Use when editing presets,
  extending Preset fields, build --suggest, or PLSS enrichment.
---

# Peaky preset

Read [MEMORY.md](../../MEMORY.md) first. Greenfield: break preset schema cleanly — update reference `config.yaml`, callers, and MEMORY together; no legacy key support.

## Layout

- Global defaults: `$PEAKY_HOME/config.yaml` (bundled template; see `peaky_preset_defaults.py`, `ensure_peaky_home()`)
- Reference project: `peaky_home/projects/nevada/config.yaml` (overrides + `land` / `sites` / `links` only)
- Global RF catalogs: `$PEAKY_HOME/modems.yaml`, `$PEAKY_HOME/environments.yaml` (see `peaky_profiles.py`)
- Run CLI from project dir: `require_cwd_config_yaml()` → `./config.yaml`
- Resolve by slug: `resolve_preset_yaml_arg("nevada")` → `projects/nevada/config.yaml`

## Global preset defaults (`peaky_preset_defaults.py`)

| File | Contents |
|------|----------|
| `$PEAKY_HOME/config.yaml` | Default `simulation`, `display`, `mesh`, `suggest` |
| `$PEAKY_HOME/projects/<slug>/config.yaml` | Overrides only (plus `land`, `sites`, `links`) |

`load_preset` merges defaults ← project. `update_preset_yaml_tree` prunes default-equal keys after writes.

## Global RF profiles (`peaky_profiles.py`)

| File | Contents |
|------|----------|
| `$PEAKY_HOME/modems.yaml` | `modem_presets:` — frequency, SF, BW, CR, power, sensitivity |
| `$PEAKY_HOME/environments.yaml` | `environment_presets:` — clutter, Fresnel, `coverage_pessimism_db`, situation/time %, climate, ground params |

Bundled templates in `peaky_finders/templates/` (`bundled_templates.py`) seed missing `$PEAKY_HOME` files on first use (`config.yaml`, `modems.yaml`, `environments.yaml`). **`tests/fixtures/peaky_home/`** is only the isolated test `PEAKY_HOME` (generic `fixture-modem` / `fixture-desert` catalogs) — not shipped templates. Project preset holds **`simulation.modem`** / **`simulation.environment`** names only (+ optional inline overrides). Propagation mapping: `preset_mapping.preset_to_request()`.

## Schema (`Preset` in `sites_job.py`)

| Section | Purpose |
|---------|---------|
| `simulation` | Active `modem` / `environment` names, `radius_km` (≤100), `raster_dimension`, `transmitter`/`receiver`, `max_workers` |
| `display` | Viewshed raster + `display.kml` / `display.kmz` presentation |
| `land` | Slug-keyed `layers:` — roles `aoi`, `positive`, `negative`, `reference`; paths under `<preset-dir>/data/` |
| `mesh` | Pairwise/depth analysis knobs |
| `suggest` | Site planner for `build --suggest`; `mesh_backbone.goal_order` sequences `type: goal` site slugs |
| `sites` | All map points — repeaters (`installed`/`planned`/`suggested`) and goals (`type: goal`); see **Sites vs goals** |
| `links` | Manual mutual site pairs `[[a, b], …]` |

Legacy `.json` presets are rejected.

## Sites vs goals

All map points under **`sites:`**. **`type: goal`** = coverage attractor (no viewshed). Other types = repeaters with viewsheds. Optional **`tags: [str, …]`** (lowercase labels) for serve UI filtering.

### Site types (`SiteType`)

| `type` | Role | Position |
|--------|------|----------|
| `installed` | Deployed repeater | Fixed — must not move |
| `planned` | User-committed future site | Fixed — must not move |
| `suggested` | `build --suggest` output | May move — `--replace-suggested` drops prior suggestions |
| `goal` | Coverage attractor | Fixed — must not move |

Suggest **never relocates** `installed`, `planned`, or `goal` sites. Only `type: suggested` entries are removed/replaced on re-suggest.

### Goals (`type: goal`)

- User-defined in `sites:` with `type: goal`, `name`, `loc: [lat, lon]`.
- Solver sequencing via `suggest.mesh_backbone.goal_order` (references those slugs).
- Ephemeral runtime `bridge:*` goals appear when healing disconnected mesh components.
- A goal is **captured** when a repeater footprint covers the point **and** mutual RF hop is viable.
- Serve promote: PATCH goal → change `type` to `installed` or `planned` (same slug).

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
- PLSS enrichment: `plss_fetch.py`

Never bypass with direct `yaml.dump`.

## Extending schema

1. Add field to Pydantic model in `sites_job.py`
2. Document in reference `projects/nevada/config.yaml` with `#` rationale
3. Update MEMORY.md in same change set
