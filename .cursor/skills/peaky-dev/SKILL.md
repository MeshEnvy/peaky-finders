---
name: peaky-dev
description: >-
  Peaky Docker dev workflow: ./peaky serve and ./peaky test in peaky:dev image.
  Use when running locally or debugging Docker/test issues.
---

# Peaky dev

Read [MEMORY.md](../../MEMORY.md) for env vars.

## Commands

```bash
./peaky serve              # web UI (--reload by default)
./peaky serve --no-reload
./peaky test               # pytest in container
./peaky test tests/serve
./peaky test --build       # rebuild image
```

Never `poetry run pytest` on the macOS host.

## Mounts

| Host | Container |
|------|-----------|
| `peaky_home/` | `/.peaky` (`PEAKY_HOME`) |
| `peaky_finders/` | `/app/peaky_finders` |
| `PEAKY_CACHE_DIR` | `/.peaky/splat_cache` |

Serve publishes `PEAKY_SERVE_PORT` (default 8080).
