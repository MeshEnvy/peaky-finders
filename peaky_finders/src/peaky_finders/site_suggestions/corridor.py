"""Eligible-land corridor planning for mesh-backbone routing."""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field

import geopandas as gpd
import numpy as np
from pyproj import Transformer
from rasterio import features
from rasterio.transform import from_bounds, rowcol, xy
from shapely import make_valid
from shapely.geometry import LineString, Point, mapping
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from peaky_finders.site_suggestions.context import SiteSuggestionContext
from peaky_finders.site_suggestions.log import suggest_log, suggest_step
from peaky_finders.site_suggestions.mesh_backbone_geom import GoalPoint
from peaky_finders.site_suggestions.mesh_goals import (
    goal_point_for_key,
    is_goal_captured,
    ordered_uncaptured_goal_keys,
)
from peaky_finders.site_suggestions.mesh_grow import composite_coverage_geometry

_TO_M = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
_FROM_M = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)


@dataclass
class CorridorGrowState:
    """Session state for ``routing: corridor`` mesh-backbone grows."""

    active_goal_key: str | None = None
    corridors: list[CorridorPath] = field(default_factory=list)
    corridor_index: int = 0
    stall_count: int = 0
    blocked_goal_keys: set[str] = field(default_factory=set)
    recovery_buffer_bonus_m: float = 0.0
    corridor_plan_generation: int = 0
    goal_debug: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class CorridorPath:
    """Polyline route on eligible land from mesh attachment toward a goal."""

    goal_key: str
    line: LineString
    length_m: float
    attachment_lon: float
    attachment_lat: float
    variant: int = 0

    def line_m3857(self) -> LineString:
        g = self.line if self.line.is_valid else make_valid(self.line)
        return gpd.GeoDataFrame(geometry=[g], crs="EPSG:4326").to_crs("EPSG:3857").geometry.iloc[0]


def _footprints(ctx: SiteSuggestionContext) -> dict[str, BaseGeometry | None]:
    from peaky_finders.site_suggestions.mesh_backbone_completion import footprints_for_backbone_sites

    return footprints_for_backbone_sites(ctx.plan, ctx.session_footprints)


def _eligible_mask_grid(
    *,
    eligible_ll: BaseGeometry,
    aoi_ll: BaseGeometry,
    cell_m: float,
) -> tuple[np.ndarray, object, int, int]:
    elig = eligible_ll if eligible_ll.is_valid else make_valid(eligible_ll)
    aoi = aoi_ll if aoi_ll.is_valid else make_valid(aoi_ll)
    clip = elig.intersection(aoi)
    if clip.is_empty:
        raise ValueError("eligible land empty after AOI clip")

    gm = gpd.GeoDataFrame(geometry=[clip], crs="EPSG:4326").to_crs("EPSG:3857").geometry.iloc[0]
    minx, miny, maxx, maxy = gm.bounds
    cell = max(50.0, float(cell_m))
    cols = max(2, int(np.ceil((maxx - minx) / cell)))
    rows = max(2, int(np.ceil((maxy - miny) / cell)))
    transform = from_bounds(minx, miny, maxx, maxy, cols, rows)
    mask = features.rasterize(
        [(gm, 1)],
        out_shape=(rows, cols),
        transform=transform,
        fill=0,
        dtype=np.uint8,
    ).astype(bool)
    return mask, transform, rows, cols


def _cell_center_ll(transform, row: int, col: int) -> tuple[float, float]:
    x, y = xy(transform, row, col, offset="center")
    lon, lat = _FROM_M.transform(float(x), float(y))
    return float(lat), float(lon)


def _nearest_cell(
    *,
    transform,
    rows: int,
    cols: int,
    mask: np.ndarray,
    lon: float,
    lat: float,
) -> tuple[int, int] | None:
    x, y = _TO_M.transform(float(lon), float(lat))
    r, c = rowcol(transform, x, y)
    ri, ci = int(r), int(c)
    if 0 <= ri < rows and 0 <= ci < cols and mask[ri, ci]:
        return ri, ci
    best: tuple[int, int] | None = None
    best_d = float("inf")
    rs, cs = np.where(mask)
    for rr, cc in zip(rs, cs, strict=True):
        cx, cy = xy(transform, int(rr), int(cc), offset="center")
        d = float(np.hypot(cx - x, cy - y))
        if d < best_d:
            best_d = d
            best = (int(rr), int(cc))
    return best


def _astar(
    mask: np.ndarray,
    transform,
    start: tuple[int, int],
    goal: tuple[int, int],
    *,
    penalty: np.ndarray | None = None,
) -> list[tuple[int, int]] | None:
    rows, cols = mask.shape
    sr, sc = start
    gr, gc = goal
    if not mask[sr, sc] or not mask[gr, gc]:
        return None

    def h(r: int, c: int) -> float:
        return float(np.hypot(r - gr, c - gc))

    open_heap: list[tuple[float, int, int]] = [(h(sr, sc), sr, sc)]
    g_score: dict[tuple[int, int], float] = {(sr, sc): 0.0}
    came_from: dict[tuple[int, int], tuple[int, int]] = {}
    closed: set[tuple[int, int]] = set()
    counter = 0

    while open_heap:
        _, r, c = heapq.heappop(open_heap)
        if (r, c) in closed:
            continue
        if (r, c) == (gr, gc):
            path = [(r, c)]
            while (r, c) in came_from:
                r, c = came_from[(r, c)]
                path.append((r, c))
            path.reverse()
            return path

        closed.add((r, c))
        base = g_score[(r, c)]
        for dr, dc in (
            (-1, 0),
            (1, 0),
            (0, -1),
            (0, 1),
            (-1, -1),
            (-1, 1),
            (1, -1),
            (1, 1),
        ):
            nr, nc = r + dr, c + dc
            if nr < 0 or nc < 0 or nr >= rows or nc >= cols:
                continue
            if not mask[nr, nc]:
                continue
            step = 1.414213562 if dr and dc else 1.0
            pen = float(penalty[nr, nc]) if penalty is not None else 0.0
            ng = base + step + pen
            nxt = (nr, nc)
            if ng >= g_score.get(nxt, float("inf")):
                continue
            g_score[nxt] = ng
            came_from[nxt] = (r, c)
            counter += 1
            heapq.heappush(open_heap, (ng + h(nr, nc), nr, nc))

    return None


def _path_cells_to_line(
    cells: list[tuple[int, int]],
    transform,
) -> LineString:
    coords_ll: list[tuple[float, float]] = []
    for r, c in cells:
        lat, lon = _cell_center_ll(transform, r, c)
        coords_ll.append((lon, lat))
    if len(coords_ll) < 2:
        if len(coords_ll) == 1:
            lon, lat = coords_ll[0]
            return LineString([(lon, lat), (lon, lat)])
        return LineString()
    line = LineString(coords_ll)
    lm = gpd.GeoDataFrame(geometry=[line], crs="EPSG:4326").to_crs("EPSG:3857").geometry.iloc[0]
    simp = lm.simplify(max(50.0, lm.length * 0.01))
    if simp.is_empty:
        simp = lm
    out = gpd.GeoDataFrame(geometry=[simp], crs="EPSG:3857").to_crs("EPSG:4326").geometry.iloc[0]
    return out if out.is_valid else make_valid(out)


def attachment_point_toward_goal(
    ctx: SiteSuggestionContext,
    *,
    goal: GoalPoint,
) -> tuple[float, float] | None:
    """Eligible point on/near coverage boundary closest to ``goal``."""
    coverage = composite_coverage_geometry(ctx)
    if coverage is None or coverage.is_empty:
        return float(goal.lat), float(goal.lon)

    cov = coverage if coverage.is_valid else make_valid(coverage)
    elig = ctx.eligible_ll if ctx.eligible_ll.is_valid else make_valid(ctx.eligible_ll)
    gx, gy = _TO_M.transform(float(goal.lon), float(goal.lat))

    cov_m = gpd.GeoDataFrame(geometry=[cov], crs="EPSG:4326").to_crs("EPSG:3857").geometry.iloc[0]
    boundary = cov_m.boundary
    if boundary is None or boundary.is_empty:
        boundary = cov_m

    if boundary.geom_type == "MultiLineString":
        parts = list(boundary.geoms)
    elif boundary.geom_type == "LineString":
        parts = [boundary]
    else:
        parts = [boundary]

    best: tuple[float, float] | None = None
    best_d = float("inf")
    for part in parts:
        if part.is_empty:
            continue
        for x, y in part.coords:
            lon, lat = _FROM_M.transform(float(x), float(y))
            pt = Point(float(lon), float(lat))
            if not elig.intersects(pt):
                continue
            d = float(np.hypot(x - gx, y - gy))
            if d < best_d:
                best_d = d
                best = (float(lon), float(lat))

    if best is not None:
        return best

    # Fallback: nearest eligible point on coverage hull interior edge via grid sample
    pt_goal = Point(float(goal.lon), float(goal.lat))
    nearest = cov.boundary.interpolate(cov.boundary.project(pt_goal)) if not cov.boundary.is_empty else cov.centroid
    if elig.intersects(nearest):
        return float(nearest.x), float(nearest.y)
    return None


def plan_corridors_for_goal(
    ctx: SiteSuggestionContext,
    *,
    goal_key: str,
    k: int = 1,
    cell_m: float = 750.0,
    penalty_mask: np.ndarray | None = None,
    verbose: bool = False,
) -> list[CorridorPath]:
    """Plan up to ``k`` eligible corridors from current mesh toward ``goal_key``."""
    goal = goal_point_for_key(ctx, goal_key)
    if goal is None:
        return []

    attach = attachment_point_toward_goal(ctx, goal=goal)
    if attach is None:
        suggest_log(verbose, f"site suggest:   corridor {goal_key}: no attachment on eligible coverage")
        return []

    attach_lon, attach_lat = attach
    with suggest_step(verbose, f"corridor plan {goal_key} (k={k}, cell={cell_m:.0f} m)"):
        mask, transform, rows, cols = _eligible_mask_grid(
            eligible_ll=ctx.eligible_ll,
            aoi_ll=ctx.aoi_ll,
            cell_m=cell_m,
        )
        start = _nearest_cell(
            transform=transform,
            rows=rows,
            cols=cols,
            mask=mask,
            lon=attach_lon,
            lat=attach_lat,
        )
        end = _nearest_cell(
            transform=transform,
            rows=rows,
            cols=cols,
            mask=mask,
            lon=float(goal.lon),
            lat=float(goal.lat),
        )
        if start is None or end is None:
            suggest_log(verbose, f"site suggest:   corridor {goal_key}: no grid endpoints")
            return []

        out: list[CorridorPath] = []
        use_penalty = penalty_mask
        if use_penalty is None:
            use_penalty = np.zeros_like(mask, dtype=float)

        for variant in range(max(1, int(k))):
            cells = _astar(mask, transform, start, end, penalty=use_penalty)
            if cells is None:
                break
            line = _path_cells_to_line(cells, transform)
            if line.is_empty:
                break
            lm = line if line.is_valid else make_valid(line)
            length_m = float(
                gpd.GeoDataFrame(geometry=[lm], crs="EPSG:4326").to_crs("EPSG:3857").geometry.iloc[0].length
            )
            out.append(
                CorridorPath(
                    goal_key=goal_key,
                    line=lm,
                    length_m=length_m,
                    attachment_lon=attach_lon,
                    attachment_lat=attach_lat,
                    variant=variant,
                )
            )
            for r, c in cells:
                use_penalty[r, c] += 50.0

        suggest_log(
            verbose,
            f"site suggest:   corridor {goal_key}: {len(out)} route(s), "
            f"attach=({attach_lat:.5f}, {attach_lon:.5f}), "
            f"length={out[0].length_m / 1000.0:.1f} km" if out else f"site suggest:   corridor {goal_key}: 0 routes",
        )
    return out


def active_corridor(ctx: SiteSuggestionContext) -> CorridorPath | None:
    state = ctx.corridor_state
    if state is None or not state.corridors:
        return None
    idx = int(state.corridor_index)
    if idx < 0 or idx >= len(state.corridors):
        return None
    return state.corridors[idx]


def effective_corridor_buffer_m(ctx: SiteSuggestionContext) -> float:
    mb = ctx.cfg.mesh_backbone
    base = float(mb.corridor_buffer_m)
    bonus = float(ctx.corridor_state.recovery_buffer_bonus_m) if ctx.corridor_state else 0.0
    return base + bonus


def ensure_active_corridor(ctx: SiteSuggestionContext) -> CorridorPath | None:
    """Select active goal and plan corridor if needed."""
    mb = ctx.cfg.mesh_backbone
    if ctx.corridor_state is None:
        return None

    state = ctx.corridor_state

    if state.active_goal_key and is_goal_captured(ctx, str(state.active_goal_key)):
        from peaky_finders.site_suggestions.corridor_kml import finalize_corridor_goal

        finalize_corridor_goal(ctx, goal_key=str(state.active_goal_key), status="captured")
        state.active_goal_key = None
        state.corridors = []
        state.corridor_index = 0
        state.stall_count = 0
        state.recovery_buffer_bonus_m = 0.0

    if state.active_goal_key is None:
        pending = ordered_uncaptured_goal_keys(ctx)
        if not pending:
            return None
        state.active_goal_key = pending[0]
        state.corridors = []
        state.corridor_index = 0
        state.stall_count = 0
        state.recovery_buffer_bonus_m = 0.0

    if not state.corridors or state.corridor_index >= len(state.corridors):
        k = max(1, int(mb.corridor_k))
        state.corridors = plan_corridors_for_goal(
            ctx,
            goal_key=str(state.active_goal_key),
            k=k,
            cell_m=float(mb.corridor_grid_cell_m),
            verbose=ctx.verbose,
        )
        state.corridor_index = 0
        if state.corridors:
            from peaky_finders.site_suggestions.corridor_kml import (
                mark_active_corridor,
                record_planned_corridors,
            )

            state.corridor_plan_generation += 1
            gk = str(state.active_goal_key)
            record_planned_corridors(
                ctx,
                goal_key=gk,
                corridors=state.corridors,
                plan_generation=state.corridor_plan_generation,
            )
            mark_active_corridor(
                ctx,
                goal_key=gk,
                variant=state.corridor_index,
                plan_generation=state.corridor_plan_generation,
            )
        if not state.corridors:
            if state.active_goal_key:
                from peaky_finders.site_suggestions.corridor_kml import finalize_corridor_goal

                finalize_corridor_goal(ctx, goal_key=str(state.active_goal_key), status="blocked")
                state.blocked_goal_keys.add(str(state.active_goal_key))
                suggest_log(
                    ctx.verbose,
                    f"site suggest:   corridor: blocked goal {state.active_goal_key} (no route)",
                )
            state.active_goal_key = None
            return ensure_active_corridor(ctx)

    return active_corridor(ctx)


def corridor_stall_recovery(ctx: SiteSuggestionContext) -> bool:
    """Advance recovery state on stall; return True if a new corridor is ready."""
    mb = ctx.cfg.mesh_backbone
    state = ctx.corridor_state
    if state is None or state.active_goal_key is None:
        return False

    state.stall_count += 1
    stalls = max(1, int(mb.stall_rounds))

    if state.stall_count < stalls:
        state.recovery_buffer_bonus_m += float(mb.corridor_buffer_m)
        suggest_log(
            ctx.verbose,
            f"site suggest:   corridor stall {state.stall_count}/{stalls} "
            f"for {state.active_goal_key} (widen buffer +{mb.corridor_buffer_m:.0f} m)",
        )
        return True

    state.corridor_index += 1
    state.stall_count = 0
    state.recovery_buffer_bonus_m = 0.0

    if state.corridor_index < len(state.corridors):
        from peaky_finders.site_suggestions.corridor_kml import mark_active_corridor

        gk = str(state.active_goal_key)
        mark_active_corridor(
            ctx,
            goal_key=gk,
            variant=state.corridor_index,
            plan_generation=state.corridor_plan_generation,
        )
        suggest_log(
            ctx.verbose,
            f"site suggest:   corridor: try alternate path #{state.corridor_index + 1} "
            f"for {state.active_goal_key}",
        )
        return True

    if state.active_goal_key:
        from peaky_finders.site_suggestions.corridor_kml import finalize_corridor_goal

        finalize_corridor_goal(ctx, goal_key=str(state.active_goal_key), status="blocked")
        state.blocked_goal_keys.add(str(state.active_goal_key))
        print(
            f"site suggest: corridor: exhausted routes for goal {state.active_goal_key}",
            flush=True,
        )
    state.active_goal_key = None
    state.corridors = []
    state.corridor_index = 0
    ensure_active_corridor(ctx)
    return active_corridor(ctx) is not None


def corridor_grow_planning_complete(ctx: SiteSuggestionContext) -> bool:
    """True when all non-blocked goals are captured and hop-connected to seeds."""
    from peaky_finders.site_suggestions.mesh_backbone_completion import (
        all_backbone_sites,
        captured_goal_keys,
        footprints_for_backbone_sites,
        hop_adjacency,
        hop_reachable_from,
        mesh_connectivity_complete,
        sites_capturing_goal,
        uncaptured_goal_keys,
    )
    from peaky_finders.site_suggestions.mesh_backbone_geom import goals_from_config

    if not mesh_connectivity_complete(ctx):
        return False

    mb = ctx.cfg.mesh_backbone
    blocked = ctx.corridor_state.blocked_goal_keys if ctx.corridor_state else set()
    required = set(mb.goals.keys()) - blocked
    if not required:
        return True

    sites = all_backbone_sites(ctx)
    footprints = footprints_for_backbone_sites(ctx.plan, ctx.session_footprints)
    uncaptured = uncaptured_goal_keys(mb, sites, footprints) - blocked
    if uncaptured:
        return False

    seed_slugs = set(ctx.preset.sites.keys())
    if not seed_slugs:
        return True

    adjacency = hop_adjacency(sites, footprints)
    reachable = hop_reachable_from(start_slugs=seed_slugs, adjacency=adjacency)
    goals = goals_from_config(mb)
    for key in required:
        goal = goals[key]
        captors = sites_capturing_goal(goal, sites, footprints)
        if not captors or not (captors & reachable):
            return False
    return True


def corridor_line_geojson(corridor: CorridorPath) -> dict:
    return {
        "type": "Feature",
        "properties": {
            "goal_key": corridor.goal_key,
            "length_m": corridor.length_m,
            "variant": corridor.variant,
        },
        "geometry": mapping(corridor.line),
    }
