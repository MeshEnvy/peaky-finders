# Change management policy

Peaky has one user-facing changelog. Commits stay granular for bisect; release notes are curated in [`CHANGELOG.md`](../CHANGELOG.md).

## Two layers

| Layer | Purpose |
|-------|---------|
| **Git commits** | Conventional Commits for history, review, bisect. Fine-grained is fine. |
| **`CHANGELOG.md`** | User-facing bullets grouped by release. One line can cover many commits. |

## Rules per change set

- User-visible behavior change → bullet under **`## [Unreleased]`** in the same commit/PR as the code.
- Internal-only work (`refactor`, `test`, `chore`, `ci`, agent hygiene) → no changelog entry unless operators care.
- Breaking greenfield changes → **`### Changed`** or **`### Removed`** with a short migration note.

Keep a Changelog sections: `### Added`, `### Changed`, `### Fixed`, `### Removed`. Omit empty sections.

## Release cut

1. `cargo test --locked` green on `main`.
2. Workspace `version` in root `Cargo.toml` matches the tag (`v0.5.0` → `0.5.0`).
3. Promote **`## [Unreleased]`** → **`## [vX.Y.Z] - YYYY-MM-DD`** (one-line summary under the heading if helpful). Do not create a version section before the tag exists.
4. Open a fresh empty **`## [Unreleased]`** at the top.
5. `./scripts/changelog.sh check vX.Y.Z` passes.
6. Tag and push: `git tag vX.Y.Z && git push origin vX.Y.Z`.

CI publishes the `## [vX.Y.Z]` body to GitHub Releases. It does **not** auto-generate from commits.

EnvyOS distro publish copies the pinned tag section into the distro GitHub Release notes (`./envyos changelog check` requires that heading).

Skill: [`.cursor/skills/peaky-release/SKILL.md`](../.cursor/skills/peaky-release/SKILL.md). Day-to-day entries: [`.cursor/skills/peaky-changelog/SKILL.md`](../.cursor/skills/peaky-changelog/SKILL.md).

## Enforcement

| When | Gate |
|------|------|
| Tag push (`release.yml`) | `./scripts/changelog.sh check "${GITHUB_REF_NAME}"` |
| Manual pre-tag | `./scripts/changelog.sh check vX.Y.Z` and `./scripts/changelog.sh notes vX.Y.Z` |

Missing or empty version section fails the release workflow.
