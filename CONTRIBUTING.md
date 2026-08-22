# Contributing

Peaky Finders is a Rust workspace with a browser UI served by `peaky serve`. Agent skills under `.cursor/skills/` cover release prep, presets, RF, and the web UI.

Read [MEMORY.md](MEMORY.md) before substantive changes.

## Commit messages

Commit subjects become GitHub Release notes. Write them for humans reading the changelog, not for yourself in the moment.

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

### Release-facing vs internal

| Type | Release audience | Write the subject as… |
|------|------------------|------------------------|
| `feat` | Users | A new capability ("add land layer import") |
| `fix` | Users | What was broken ("repair viewshed cache key") |
| `perf` | Users | What got faster ("speed up eligible-land mask") |
| `refactor`, `test`, `chore`, `build`, `ci`, `docs` | Maintainers | Fine-grained; may appear under **Other changes** |

Avoid vague subjects (`wip`, `update stuff`, `fix bug`). Avoid past tense (`fixed`, `added`).

### Pull requests

Prefer PRs for non-trivial work. Set the **PR title** to the same conventional subject (squash merge keeps one clean line per PR in the release). GitHub credits the PR author in generated notes.

Optional PR labels map to release sections (see [`.github/release.yml`](.github/release.yml)): `feature`, `fix`, `bug`, `performance`. Use label `skip-changelog` to omit noise from the release body.

### Agents

On `/commit`, follow [`.cursor/skills/commit/SKILL.md`](.cursor/skills/commit/SKILL.md).

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

## Releases

Publishing is tag-driven. Maintainer workflow: skill [`.cursor/skills/peaky-release/SKILL.md`](.cursor/skills/peaky-release/SKILL.md).
