---
name: peaky-changelog
description: >-
  Maintain CHANGELOG.md Unreleased entries for user-facing Peaky changes.
  Use when shipping features, fixes, or breaking changes; before release cut.
---

# Peaky changelog

Policy: [`docs/change-management.md`](../../docs/change-management.md). Canonical file: [`CHANGELOG.md`](../../CHANGELOG.md).

## When to write

Add a bullet under **`## [Unreleased]`** in the **same change set** as user-visible code when you:

- Add or remove CLI flags, serve routes, or map UI behavior
- Change preset schema or project layout operators depend on
- Fix incorrect RF, land, viewshed, or finder behavior users would notice
- Ship breaking greenfield changes (delete old paths; note migration)

Skip changelog lines for refactors, tests, CI, docs-only, and agent hygiene unless operators care.

## How to write

- Keep a Changelog sections: `### Added`, `### Changed`, `### Fixed`, `### Removed`.
- **Bold lead phrase** then em dash and detail: `- **Land from GDB** — …`
- Group related commits into **one bullet** (the release is not a commit dump).
- Plain language for preset/serve users, not crate names, unless the crate is the product surface.

## Release cut (maintainers)

1. When ready to ship, promote **`## [Unreleased]`** to **`## [vX.Y.Z] - YYYY-MM-DD`** (optional one-line summary under the heading). Until then, all pending work stays under Unreleased only.
2. Insert a fresh empty **`## [Unreleased]`** at the top.
3. Run `./scripts/changelog.sh check vX.Y.Z`.
4. Tag per skill **`peaky-release`**.

Do not rely on commit subjects for GitHub Release text.
