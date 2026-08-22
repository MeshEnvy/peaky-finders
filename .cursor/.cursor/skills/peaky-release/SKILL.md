---
name: peaky-release
description: >-
  Cut a Peaky Finders GitHub release (version bump, tag, binary publish).
  Use when preparing or publishing v* releases.
---

# Peaky release

Read [MEMORY.md](../../MEMORY.md) § Releases. Policy: [`docs/change-management.md`](../../docs/change-management.md). Workflow: [`.github/workflows/release.yml`](../../.github/workflows/release.yml).

## Preconditions

1. `main` is green (`cargo test --locked`).
2. User-facing changes since last tag are in [`CHANGELOG.md`](../../CHANGELOG.md) (not only in commits).
3. Workspace `version` in root [`Cargo.toml`](../../Cargo.toml) matches the tag you are about to cut.

## Changelog promote

1. Move **`## [Unreleased]`** content to **`## [vX.Y.Z] - YYYY-MM-DD`** (optional one-line summary under the heading).
2. Insert a fresh empty **`## [Unreleased]`** at the top.
3. Verify:

```bash
./scripts/changelog.sh check vX.Y.Z
./scripts/changelog.sh notes vX.Y.Z   # preview GitHub body
```

Skill for day-to-day entries: **`peaky-changelog`**.

## Version bump

Set `version` in root [`Cargo.toml`](../../Cargo.toml) `[workspace.package]` (all crates inherit via `version.workspace = true`).

Commit changelog promote + version bump on `main` before tagging.

## Publish

```bash
git tag v0.5.0
git push origin v0.5.0
```

Push the tag only after the promoted changelog and version bump are on the tagged commit.

## What CI does

On `v*` tag push, GitHub Actions:

1. Builds `peaky` release binaries for `x86_64-unknown-linux-gnu`, `aarch64-apple-darwin`, `x86_64-apple-darwin`.
2. Runs `./scripts/changelog.sh check` and publishes that section as the release body.
3. Uploads `peaky-<version>-<target>.tar.gz` assets.

GitHub attaches **Source code (zip)** and **Source code (tar.gz)** automatically.

## Verify

1. Open https://github.com/MeshEnvy/peaky-finders/releases
2. Confirm release notes match `CHANGELOG.md` for that version (not a commit list).
3. Confirm three platform archives plus source archives.
4. Smoke-test one binary: `peaky serve /tmp/peaky-smoke --port 8080`

## Notes

- No Windows target in the matrix yet.
- GDB land import still requires GDAL on the host; not bundled in release binaries.
- Do not tag from unmerged branches unless explicitly doing a pre-release fork.
