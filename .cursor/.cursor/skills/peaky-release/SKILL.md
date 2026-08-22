---
name: peaky-release
description: >-
  Cut a Peaky Finders GitHub release (version bump, tag, binary publish).
  Use when preparing or publishing v* releases.
---

# Peaky release

Read [MEMORY.md](../../MEMORY.md) § Releases. Workflow: [`.github/workflows/release.yml`](../../.github/workflows/release.yml).

## Preconditions

1. `main` is green (`cargo test --locked`).
2. User-facing changes since last tag are reflected in README/screenshots if needed.
3. Workspace version matches the tag you are about to cut.

## Version bump

Set `version` in root [`Cargo.toml`](../../Cargo.toml) `[workspace.package]` (all crates inherit via `version.workspace = true`).

Commit the bump on `main` before tagging (or include it in the same commit as the tag points to).

## Publish

```bash
git tag v0.5.0
git push origin v0.5.0
```

Push the tag only after the version bump is on the tagged commit.

## What CI does

On `v*` tag push, GitHub Actions:

1. Builds `peaky` release binaries for `x86_64-unknown-linux-gnu`, `aarch64-apple-darwin`, `x86_64-apple-darwin`.
2. Uploads `peaky-<version>-<target>.tar.gz` assets to the GitHub Release.
3. Generates release notes.

GitHub attaches **Source code (zip)** and **Source code (tar.gz)** automatically.

Release notes are auto-generated from commits/PRs since the last tag. Commit format policy: [CONTRIBUTING.md](../../CONTRIBUTING.md) § Commit messages. Config: [`.github/release.yml`](../../.github/release.yml).

## Verify

1. Open https://github.com/MeshEnvy/peaky-finders/releases
2. Confirm three platform archives plus source archives.
3. Smoke-test one binary: `peaky serve /tmp/peaky-smoke --port 8080`

## Notes

- No Windows target in the matrix yet.
- GDB land import still requires GDAL on the host; not bundled in release binaries.
- Do not tag from unmerged branches unless explicitly doing a pre-release fork.
