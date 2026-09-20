# Peak catalog: follow-up after difficulty YAML (2026-09-19)

Probe + audit inform **separate** tuning decisions. Raising hike gate to 110% segment grade does not fix basin sparsity alone.

## Recommended next changes

### 1. Align road pre-check with park search (805 m vs 1609 m)

- **Today:** `max_road_search_m` (805) gates whether *any* jeep/paved exists near summit; park picker searches out to `hike_path_max_m` (1609).
- **Effect:** Peaks in the outer annulus can have a feasible park within 1 mi but fail the 805 m road pre-check.
- **Fix:** Use `hike_path_max_m` (or a single `road_search_m`) for the jeep/paved existence check, or run park search before rejecting on road distance.

### 2. Draft/site park scoring vs catalog eligibility

- **Shipped 2026-09-19 / 2026-09-20:** Site clicks and draft pins use **ungated display routing** (`compute_place_access`): jeep from pavement along the connected OSM graph to the closest reachable node, then hike. No catalog slope/jeep gates; difficulty labels are informational only. Catalog build still uses `select_park_and_hike` + `gate_eligibility=true`.

### 3. DEM bin / seeding for ridge pins

- **Shipped 2026-09-19:** `scan.peak_bin_size_m` **500**, no site-vs-peak dedup. Remaining miss mode is 20 m prominence + 500 m summit snap (a pad that is not a local max still never enters).

## Probe: draft pin 39.922869,-117.128549 (2026-09-19)

With **110%** hike gate and stamped bands:

| Stage | Result |
|-------|--------|
| Land | eligible |
| DEM snap | 39.924274,-117.128300 elev 1636 m, **prominence 6.6 m** (below 20 m min) |
| Nearest road | jeep 126 m, paved 615 m (within 805 m pre-check) |
| `filter_candidate` | **PASS** (probe source skips prominence gate; a **dem** bin candidate would drop on **land/prominence**) |
| Stamped | hike **medium**, jeep **medium** |

So this pin is not in catalog because it never enters the DEM universe as a ≥20 m prominence peak, not because of the old 30% slope gate.

## Audit: HEAD~1 → HEAD (peaky-nevada)

~2044 removed slugs vs current catalog; coarse bucket from old thin rows: **unknown_stale 2044**, **jeep 5**. Full-AOI replace re-validated everything; most drops need per-slug `peaky peaks probe` or rescan with new gates to classify.

## After statewide rescan

Re-run difficulty counts from stamped `hike_difficulty` / `jeep_difficulty` on thin rows. Expect difficult/extreme only after `peaky peaks build` with `PEAK_ALGO_VERSION=6` and 110% hike gate.
