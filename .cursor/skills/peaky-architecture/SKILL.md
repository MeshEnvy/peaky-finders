---
name: peaky-architecture
description: >-
  Peaky Finders v4 greenfield architecture: read MEMORY.md first, CLI-first
  preset projects, progressive web UI (`peaky serve`), splatter RF, build DAG.
  Use when onboarding, refactoring, breaking changes, or cross-cutting work —
  prefer simplification over backcompat.
---

# Peaky architecture

**Read [MEMORY.md](../../MEMORY.md) before any substantive work.** It is source of truth; update it in the same change set when you break or reshape the system.

## Stack

| Piece | Choice |
|-------|--------|
| Interface | CLI (`peaky`) from project dir with `config.yaml`; **`peaky serve`** progressive web UI (on-demand, same pipeline) |
| RF engine | `splatter` submodule (PyO3, Fresnel/FSPL) |
| Config | One preset YAML per project (`projects/<slug>/config.yaml`) |
| Build | Incremental DAG — bundle → viewsheds → mesh → KMZ |
| Dev/test | Docker — `./peaky` (`test`, `serve`, `build`, …) |

## Domain model (sites vs goals)

- **Sites** (`sites:`) — repeaters with viewsheds. `installed` / `planned` are **fixed**; `suggested` (from `--suggest`) **may move** on re-suggest.
- **Goals** (top-level `goals:`) — coverage **attractors** (desired map points); not repeaters, no viewshed until captured (footprint + RF hop).

Rule: `sites-and-goals`. Detail: MEMORY § Domain model, skill `peaky-preset`.

## Workflow

```bash
cd projects/nevada
../../peaky build           # full incremental DAG
../../peaky build --suggest   # site solver → preset YAML
```

Granular subcommands (`bundle`, `mesh`, `viewshed`, `kmz`, `stamp`, `inspect`) debug single targets without running the full graph.

**Web UI** — `peaky serve` is a thin HTTP/MapLibre shell: one unit of work per request (e.g. one site's viewshed), reusing the same helpers and `<preset>/build/` artifacts as CLI/build. See skill `peaky-serve` and rule `serve-web-ui`.

## Key modules

| Area | Modules |
|------|---------|
| CLI | `peaky_cli.py`, `build_cli.py`, `bundle_commands.py`, `mesh_commands.py` |
| Serve | `serve_cli.py`, `serve_viewshed.py` — on-demand HTTP over shared pipeline |
| Preset | `sites_job.py` — `Preset`, paths, YAML I/O |
| Build DAG | `build_graph.py`, `build_executor.py`, `build_configure.py` |
| Bundle | `bundle_clips.py`, `bundle_build.py` |
| Coverage | `cli.py` (splatter), `splat_pipeline.py` |
| Suggest | `site_suggestions/` — planner, strategies, `preset_io.py` |
| HTTP | `http_pool.py`, `skadi_dem.py`, `plss_fetch.py` |

## Greenfield

**Break freely to simplify.** No shims, migrations, dual paths, or deprecation wrappers.

- Delete old APIs and invalidate caches rather than adapt around them.
- Update callers, tests, reference preset, and MEMORY.md together.
- Prefer one clean design over preserving old CLI flags, YAML keys, or build layouts.

See `.cursor/rules/greenfield-no-backcompat.mdc`.
