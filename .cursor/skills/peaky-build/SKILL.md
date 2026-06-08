---
name: peaky-build
description: >-
  Peaky incremental build DAG: build_graph targets, executor waves, staleness
  checks, stamps, peaky build flags. Use when adding build targets, debugging
  staleness, --target subgraphs, parallelism, or mesh/viewshed/KMZ phases.
---

# Peaky build

Read [MEMORY.md](../../MEMORY.md) first. Greenfield: changing DAG targets or artifact layout invalidates old `build/` outputs — update fresh checks and MEMORY; no migration of stale caches.

## `peaky build` flags

| Flag | Effect |
|------|--------|
| `--target SCOPE` | Subgraph root: `all`, `kmz`, `bundle`, `mesh`, `viewsheds`, `viewshed/<slug>` |
| `-j N` | Parallel targets within one wave |
| `--force` | Run every targeted node (ignore staleness) |
| `--dry-run` | Print build/fresh flags only |
| `--suggest` | Run site suggestion pass → append to preset YAML |
| `--verbose` | Start/progress/end per target |

## Pipeline

1. `require_cwd_config_yaml()` + `load_preset`
2. `configure_preset_build` → `BuildConfigurePlan`
3. `build_target_graph` + `topo_sort` → waves
4. `build_executor.run_incremental_build` — skip fresh via `build_fresh_checks`

## Modules

| Module | Role |
|--------|------|
| `build_configure.py` | Plan clips, composites, viewshed workspaces |
| `build_graph.py` | Target IDs, edges, subgraph filter |
| `build_executor.py` | Wave-parallel execution |
| `build_fresh_checks.py` | Artefact/stamp freshness |
| `build_stamp_inputs.py` | Stamp prerequisite paths |
| `build_keys.py` | Content-addressed cache keys |

## Phases (high level)

| Phase | Outputs |
|-------|---------|
| Bundle clips/composites | `build/clips/`, eligible GPKG |
| Viewsheds | `build/viewsheds/` — splatter PPM/KML/PNG per site |
| Mesh | `build/mesh/` — pairwise + depth stores |
| KMZ | Aggregate document under build tree |

Splatter viewsheds are cheap (~<0.5s each) — parallelize at batch/wave level, not micro-batch single viewsheds.

## Adding a target

Graph node → executor handler → fresh check → (optional) stamp input → granular CLI parity.

See `build-dag` rule for checklist.
