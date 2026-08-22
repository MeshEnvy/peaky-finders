# Contributing

Peaky Finders is a Rust workspace with a browser UI served by `peaky serve`. Agent skills under `.cursor/skills/` cover release prep, presets, RF, and the web UI.

Read [MEMORY.md](MEMORY.md) before substantive changes.

## Commit messages

Conventional Commits for git history. **Release notes are not derived from commits** — curate [`CHANGELOG.md`](CHANGELOG.md) instead. Policy: [`docs/change-management.md`](docs/change-management.md).

### Format

Conventional Commits (required):

```text
<type>(<scope>): <description>
```

- **type:** `feat`, `fix`, `perf`, `refactor`, `docs`, `test`, `chore`, `build`, `ci`
- **scope:** area of the repo (`serve`, `preset`, `splatter`, `geo`, `finder`, `cli`, `assets`, …)
- **description:** imperative, lowercase, no trailing period, **≤ 50 characters**

Examples:

```text
feat(serve): add goal-seek map UI
fix(splatter): handle void DEM tiles
perf(seek): trim peak scan to hop disc
chore(ci): add release workflow
```

Commits may stay granular. Group user-facing impact in **`## [Unreleased]`** (skill [`.cursor/skills/peaky-changelog/SKILL.md`](.cursor/skills/peaky-changelog/SKILL.md)).

### Pull requests

Prefer PRs for non-trivial work. Squash title = conventional subject for git history only.

### Agents

On `/commit`, follow [`.cursor/skills/commit/SKILL.md`](.cursor/skills/commit/SKILL.md). On user-visible ships, update `CHANGELOG.md` in the same change set.

## Opportunistic refactor

**Mandatory.** Any edit that touches a file also extracts or dedups one safe incremental improvement in the same change set when the opportunity exists (pure helpers, shared constants, URL builders, duplicated logic). See [`.cursor/rules/opportunistic-refactor.mdc`](.cursor/rules/opportunistic-refactor.mdc).

Map UI: keep `assets/static/project-map/init.js` as boot only; new pure code goes in sibling ESM modules listed in MEMORY.md.

## Build from source

Requires Rust 1.74+ and a C toolchain.

```bash
cargo build --locked --release -p peaky
```

Binary: `target/release/peaky`

Local dev (debug build is fine for UI work; use `--release` for long RF runs):

```bash
cargo run -p peaky -- serve /path/to/project --host 127.0.0.1 --port 8080
```

Skadi mirror: `<project>/.peaky/cache/skadi`. Optional `SPLAT_CACHE` env override.

## Test

```bash
cargo test --locked
```

CI runs the same on push/PR to `main` (`.github/workflows/ci.yml`).

## Docker

```bash
docker build -t peaky:latest .
docker run --rm -p 8080:8080 \
  -v /path/to/project:/project \
  peaky:latest
```

Default image command: `peaky serve /project`. Override for `find path`, etc.

## Layout

| Path | Role |
|------|------|
| `cmd/peaky/` | CLI (`serve`, `land`, `find path`) |
| `crates/peaky-serve/` | Axum web UI and API |
| `crates/peaky-preset/` | YAML preset model and I/O |
| `crates/peaky-geo/` | Land eligibility, GeoJSON/GDB |
| `crates/peaky-finder/` | Route auto-finder |
| `splatter/` | RF viewshed engine |
| `assets/` | Embedded static UI |

### Frontend assets

Map UI lives under `assets/static/project-map/`. Production entry: `/static/project-map/main.js` (ESM). Edit `init.js` for map behavior; `home-settings.js` and `viewshed-raster.js` for the settings modal. Assets are **compile-time embedded** via `rust-embed` — run `cargo build -p peaky` (or restart `cargo run`) after JS/CSS changes.

## Releases

Publishing is tag-driven. [`CHANGELOG.md`](CHANGELOG.md) is the GitHub Release body source. Maintainer workflow: skill [`.cursor/skills/peaky-release/SKILL.md`](.cursor/skills/peaky-release/SKILL.md). Policy: [`docs/change-management.md`](docs/change-management.md).
