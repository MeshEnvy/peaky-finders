---
name: peaky-architecture
description: >-
  Peaky Finders v4: serve-only web UI over preset YAML, splatter RF, greenfield.
  Use when onboarding or cross-cutting refactors — no batch CLI or build DAG.
---

# Peaky architecture

**Read [MEMORY.md](../../MEMORY.md) first.**

## Stack

| Piece | Choice |
|-------|--------|
| Interface | **`peaky serve`** — MapLibre web UI, on-demand RF |
| RF engine | `splatter/` crate (`Session`, in-process) |
| Config | `$PEAKY_HOME/projects/<slug>/config.yaml` |
| Code | workspace crates + `cmd/peaky` |
| Dev/test | `cargo run -p peaky -- serve`, `cargo test`. Image: repo-root `Dockerfile` |

## Sites

One `sites:` map — `name`, `loc`, optional `tags`. No `type`, no goals split.

Rule: `sites-and-tags`. Detail: MEMORY, skill `peaky-preset`.

## Removed

Batch CLI (`build`, `bundle`, `mesh`, …), build DAG, `site_suggestions/`, `sites_job` fat preset.

See `.cursor/rules/greenfield-no-backcompat.mdc`.
