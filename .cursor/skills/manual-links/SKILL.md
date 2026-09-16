---
name: manual-links
description: >-
  When and how to add manual RF link overrides in Peaky presets. Use when editing
  links:, serve map mesh gaps, or spine/corridor planning docs.
---

# Manual links

Read [MEMORY.md](../../MEMORY.md) first.

## Purpose

Force-draw a site pair on the serve map when **P2P RF does not show the link** but field reality says the hop exists.

Implementation: `peaky-serve/src/links.rs` — manual pairs skip the P2P pass, always render as strong lines (`manual: true`).

## When to add

1. Open serve map / `GET …/links` for the project.
2. Confirm both slugs exist under `sites:` with real coords.
3. The hop is real (installed repeaters, verified field hearing, or intentional RF override).
4. P2P mesh omits the pair and you need it visible for planning or ops.

## When NOT to add

- Spine order or corridor docs say nodes "should hear" each other.
- A new stake or site was just added (wait for mesh cache refresh first).
- Topology documentation or initiative planning (use site descriptions / ops notes instead).
- Declaring a planned hop because it is the "next link in the chain."

## File location

| Layout | Where `links:` lives |
|--------|----------------------|
| Split (`sites.yaml` present) | Top of [`sites.yaml`](sites.yaml) alongside `sites:` |
| Monolithic | [`config.yaml`](config.yaml) |

`peaky-preset` `read_merged_document` / `write_merged_document` route `links` through `sites.yaml` when split.

## Pair format

```yaml
links:
- - slug-a
  - slug-b
- - slug-c
  - slug-d
```

- Slugs must match keys under `sites:`.
- Order within a pair does not matter; keep pairs sorted for readable diffs.
- Both endpoints must exist before adding.

## advert_name (site-bound radios)

Book rows in `sites.yaml`:

- **`advert_name`** is the **base only** (≤16 chars). Do not embed `{lora.sh}`.
- Suffix comes from the fleet book `nodes.yaml` → `public_advert.name_suffix` (applied by envybot).
- Expand toward site `name` up to 16 chars; use geographic names on-air (e.g. **Soldier Meadows**, not internal **SLPT** tags).
- EIP tower pins without bound nodes should **not** carry `advert_name` (no radio apply target).

## Related

- Rule: [sites-and-tags](../../.cursor/rules/sites-and-tags.mdc)
- Skill: [peaky-preset](../../.cursor/skills/peaky-preset/SKILL.md)
