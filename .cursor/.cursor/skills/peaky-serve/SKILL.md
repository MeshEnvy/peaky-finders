---
name: peaky-serve
description: >-
  Peaky web UI (`peaky serve`): on-demand viewsheds, links, preset editing.
  Use when adding serve routes, map overlays, or HTTP APIs.
---

# Peaky serve

Read [MEMORY.md](../../MEMORY.md) first.

## Model

**Serve-only product.** `core/` owns preset + RF; `serve/` is HTTP/MapLibre shell. Work runs per user action (toggle viewshed, add site), not via batch build.

## Layout

| Module | Role |
|--------|------|
| `serve/cli.py` | Waitress runner, `--reload` |
| `serve/app.py` | Routes (pages + API) |
| `serve/viewshed.py` | On-demand splatter PNG + metadata |
| `serve/links.py` | Mutual site links |
| `serve/sites.py` | Preset site CRUD (tags-only YAML) |
| `core/preset/` | Load/validate/write `config.yaml` |

Runtime: `PEAKY_HOME`, `PEAKY_PROJECTS`. Dev: `./peaky serve`.

Rule: `.cursor/rules/serve-web-ui.mdc`.
