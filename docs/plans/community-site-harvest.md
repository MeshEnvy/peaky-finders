# Community site harvest (backlog)

**Status:** backlog — parked 2026-09-12. Enterprise initiative: [`ops/initiatives/peaky-community-harvest.md`](../../../ops/initiatives/peaky-community-harvest.md).

## Goal

Harvest community MeshCore repeater adverts from CoreScope into a **`community.yaml` sidecar** so Peaky serve can show them as **hint links** to backbone sites. Silver Triangle closure stays on ME/partner hops we control.

## First source

- **https://map.nvme.sh** — CoreScope; paginate `GET /api/nodes?role=repeater`
- ~190 repeaters; ~171 geolocated in NV AOI; ~18 at `0,0` (drop)

## Implementation slices (when pulled)

1. **`peaky harvest corescope`** — AOI clip, dedupe, write `community.yaml` only
2. **Preset merge** — tag `community`; skip in warm queue; P2P links community ↔ backbone-class only
3. **Serve UI** — distinct pin + dashed hint lines; tag filter toggle
4. **First pull** — sanity-check before reno-elko desk pass

## Out of scope

- `peaky find path --allow-tag community`
- Public coverage overlay / meshenvy.org
- Writing harvest rows into `sites.yaml`

See ops initiative for full doctrine table and missing information.
