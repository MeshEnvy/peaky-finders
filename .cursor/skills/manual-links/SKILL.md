---
name: manual-links
description: >-
  When and how to add manual RF link overrides in Peaky presets. Use when editing
  links.json, serve map mesh gaps, or spine/corridor planning docs.
---

# Manual links

Read [MEMORY.md](../../MEMORY.md) first.

## Purpose

Force-draw a site pair on the serve map when **P2P RF does not show the link** but field reality says the hop exists.

Implementation: `peaky-serve/src/links.rs` — manual pairs skip P2P, render teal (`manual: true`).

Computed RF (blue two-way, red one-way) lives in git-tracked **`links/<a>/<b>.json`**, not in `links.json`.

## When to add

1. Open serve map / `GET …/links` for the project.
2. Confirm both slugs exist under `sites:` with real coords.
3. The hop is real (installed repeaters, verified field hearing, or intentional RF override).
4. P2P mesh omits the pair and you need it visible for planning or ops.

## When NOT to add

- Spine order or corridor docs say nodes "should hear" each other.
- A new stake or site was just added (wait for mesh warm / per-pair cache refresh first).
- Topology documentation or initiative planning (use site descriptions / ops notes instead).
- Declaring a planned hop because it is the "next link in the chain."

## File location

| Layout | Manual overrides |
|--------|------------------|
| Split book | **`links.json`** next to `sites.yaml` |
| Monolithic | `links:` in **`config.yaml`** |

`peaky-preset` merges `links.json` when split. Saves write manual rows to **`links.json`**.

## Pair format (`links.json`)

```json
{
  "links": [
    ["slug-a", "slug-b"],
    { "a": "slug-c", "b": "slug-d", "field_at": "2026-09-09" }
  ]
}
```

- Slugs must match keys under `sites:`.
- Order within a pair does not matter; keep pairs sorted for readable diffs.
- Both endpoints must exist before adding.
- Unknown keys on record rows fail preset load (greenfield).

## advert_name (site-bound radios)

Book rows in `sites.yaml`:

- **`advert_name`** is the **base only** (≤16 chars). Do not embed `{lora.sh}`.
- Suffix comes from the fleet book `nodes.yaml` → `public_advert.name_suffix` (applied by envybot).
- Expand toward site `name` up to 16 chars; use geographic names on-air (e.g. **Soldier Meadows**, not internal **SLPT** tags).
- EIP tower pins without bound nodes should **not** carry `advert_name` (no radio apply target).

## Related

- Rule: [sites-and-tags](../../.cursor/rules/sites-and-tags.mdc)
- Skill: [peaky-preset](../../.cursor/skills/peaky-preset/SKILL.md)
