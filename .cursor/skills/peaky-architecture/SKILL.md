---
name: peaky-architecture
description: >-
  Peaky Finders v4 greenfield architecture: read MEMORY.md first, CLI-first
  preset projects, splatter RF, build DAG. Use when onboarding, refactoring,
  breaking changes, or cross-cutting work — prefer simplification over backcompat.
---

# Peaky architecture

**Read [MEMORY.md](../../MEMORY.md) before any substantive work.** It is source of truth; update it in the same change set when you break or reshape the system.

## Stack

| Piece | Choice |
|-------|--------|
| Interface | CLI (`peaky`) from project dir with `config.yaml` |
| RF engine | `splatter` submodule (PyO3, Fresnel/FSPL) |
| Config | One preset YAML per project (`projects/<slug>/config.yaml`) |
| Build | Incremental DAG — bundle → viewsheds → mesh → KMZ |
| Dev/test | Docker — `./peaky`, `./peaky-test` |

## Workflow

```bash
cd projects/nevada
../../peaky build           # full incremental DAG
../../peaky build --suggest   # site solver → preset YAML
```

Granular subcommands (`bundle`, `mesh`, `viewshed`, `kmz`, `stamp`, `inspect`) debug single targets without running the full graph.

## Key modules

| Area | Modules |
|------|---------|
| CLI | `peaky_cli.py`, `build_cli.py`, `bundle_commands.py`, `mesh_commands.py` |
| Preset | `sites_job.py` — `Preset`, paths, YAML I/O |
| Build DAG | `build_graph.py`, `build_executor.py`, `build_configure.py` |
| Bundle | `bundle_clips.py`, `bundle_build.py` |
| Coverage | `cli.py` (splatter), `splat_pipeline.py` |
| Suggest | `site_suggestions/` — planner, strategies, `preset_io.py` |
| HTTP | `http_pool.py`, `skadi_dem.py`, `plss_mlrs_fetch.py` |

## Greenfield

**Break freely to simplify.** No shims, migrations, dual paths, or deprecation wrappers.

- Delete old APIs and invalidate caches rather than adapt around them.
- Update callers, tests, reference preset, and MEMORY.md together.
- Prefer one clean design over preserving old CLI flags, YAML keys, or build layouts.

See `.cursor/rules/greenfield-no-backcompat.mdc`.
