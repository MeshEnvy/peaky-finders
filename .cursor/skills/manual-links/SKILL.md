---
name: manual-links
description: >-
  When and how to add manual RF link overrides in Peaky presets. Use when editing
  links/<a>/<b>.json, serve map mesh gaps, or spine/corridor planning docs.
---

# Manual links

Read [MEMORY.md](../../MEMORY.md) first.

## Purpose

Force-draw a site pair on the serve map when **P2P RF does not show the link** but field reality says the hop exists.

Implementation: `peaky-serve/src/links.rs` — manual pairs skip P2P, render teal (`manual: true` on the pair file and in mesh GeoJSON).

Computed and manual hops share **`links/<a>/<b>.json`**. RF rows get margins and `kind`; manual overrides add **`manual: true`** (sticky across RF refresh).

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

Git-tracked **`links/<a>/<b>.json`** next to `sites.yaml` / `config.yaml` (canonical slug order: `a <= b`).

## Pair format

Edit or create the pair file (camelCase matches serve output):

```json
{
  "a": "slug-a",
  "b": "slug-b",
  "fingerprint": "",
  "linksModel": "p2p-v8",
  "forwardMarginDb": null,
  "reverseMarginDb": null,
  "kind": "none",
  "distanceKm": 0,
  "at": "2026-10-14",
  "manual": true,
  "fieldAt": "2026-09-09"
}
```

- Slugs must match keys under `sites:`.
- **`manual: true`** is required for a field override. Clearing it requires setting `"manual": false` before the next RF write (refresh alone will not clear it).
- Optional **`fieldAt`** is operator metadata (field verification date).
- Serve may fill fingerprint, margins, and `kind` on warm or link-sheet query; manual metadata is preserved.

Library helper: `write_manual_link_override` in `peaky-preset` (finder patch uses this).

## advert_name (site-bound radios)

Book rows in `sites.yaml`:

- **`advert_name`** is the **base only** (≤16 chars). Do not embed `{lora.sh}`.
- Suffix comes from the fleet book `nodes.yaml` → `public_advert.name_suffix` (applied by envybot).
- Expand toward site `name` up to 16 chars; use geographic names on-air (e.g. **Soldier Meadows**, not internal **SLPT** tags).
- EIP tower pins without bound nodes should **not** carry `advert_name` (no radio apply target).

## Related

- Rule: [sites-and-tags](../../.cursor/rules/sites-and-tags.mdc)
- Skill: [peaky-preset](../../.cursor/skills/peaky-preset/SKILL.md)
