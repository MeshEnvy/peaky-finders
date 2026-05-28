"""RF min-hop corridor planning for mesh-backbone routing."""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field

import geopandas as gpd
from pyproj import Transformer
from shapely import make_valid
from shapely.geometry import LineString, Point, mapping
from shapely.geometry.base import BaseGeometry
from shapely.ops import nearest_points

from peaky_finders.site_suggestions.context import SiteSuggestionContext
from peaky_finders.site_suggestions.log import (
    SuggestProgressTicker,
    suggest_log,
    suggest_progress,
    suggest_step,
)
from peaky_finders.site_suggestions.providers.mesh_backbone.geom import GoalPoint
from peaky_finders.site_suggestions.providers.mesh_backbone.goals import (
    is_goal_captured,
    goal_point_for_key,
    ordered_uncaptured_goal_keys,
)
from peaky_finders.site_suggestions.providers.mesh_backbone.scoring import composite_coverage_geometry
from peaky_finders.site_suggestions.rf_link import (
    ensure_dem_for_points,
    max_hop_range_m,
    mutual_hop_batches,
    rf_json_for_preset,
    splatter_session,
)

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
    """Relay-hop polyline from mesh attachment toward a goal (RF spine, not land trail)."""

    goal_key: str
    line: LineString
    length_m: float
    attachment_lon: float
    attachment_lat: float
    variant: int = 0
    hop_count: int = 0

    def line_m3857(self) -> LineString:
        g = self.line if self.line.is_valid else make_valid(self.line)
        return gpd.GeoDataFrame(geometry=[g], crs="EPSG:4326").to_crs("EPSG:3857").geometry.iloc[0]


def _node_key(lat: float, lon: float) -> tuple[int, int]:
    return (int(round(float(lat) * 1e5)), int(round(float(lon) * 1e5)))


def _prepare_eligible(eligible: BaseGeometry) -> BaseGeometry:
    return eligible if eligible.is_valid else make_valid(eligible)


def _snap_point_to_eligible(
    elig: BaseGeometry,
    *,
    lon: float,
    lat: float,
    max_snap_m: float,
) -> tuple[float, float] | None:
    pt = Point(float(lon), float(lat))
    if elig.covers(pt):
        return float(lat), float(lon)
    near, _ = nearest_points(elig, pt)
    if _haversine_m(float(lat), float(lon), float(near.y), float(near.x)) > float(max_snap_m):
        return None
    return float(near.y), float(near.x)


def _snap_eligible(
    eligible: BaseGeometry,
    *,
    lon: float,
    lat: float,
    max_snap_m: float,
) -> tuple[float, float] | None:
    snapped = _snap_point_to_eligible(
        _prepare_eligible(eligible),
        lon=lon,
        lat=lat,
        max_snap_m=max_snap_m,
    )
    if snapped is None:
        return None
    slat, slon = snapped
    return slon, slat


_SNAP_ELIGIBLE_WKB: bytes | None = None


def _init_snap_worker(eligible_wkb: bytes) -> None:
    global _SNAP_ELIGIBLE_WKB
    _SNAP_ELIGIBLE_WKB = eligible_wkb


def _snap_worker(args: tuple[float, float, float]) -> tuple[float, float] | None:
    from shapely import from_wkb

    if _SNAP_ELIGIBLE_WKB is None:
        raise RuntimeError("snap worker eligible geometry not initialized")
    lon, lat, max_snap_m = args
    elig = from_wkb(_SNAP_ELIGIBLE_WKB)
    return _snap_point_to_eligible(elig, lon=lon, lat=lat, max_snap_m=max_snap_m)


def _dedupe_snapped_nodes(snapped: Sequence[tuple[float, float] | None]) -> list[tuple[float, float]]:
    seen: set[tuple[int, int]] = set()
    out: list[tuple[float, float]] = []
    for item in snapped:
        if item is None:
            continue
        lat, lon = item
        key = _node_key(lat, lon)
        if key in seen:
            continue
        seen.add(key)
        out.append((lat, lon))
    return out


def _append_extra_relay_nodes(
    chain: Sequence[tuple[float, float]],
    extra: Sequence[tuple[float, float]],
) -> list[tuple[float, float]]:
    """Append backbone relay nodes after the attach→goal chain (not interleaved)."""
    if not extra:
        return list(chain)
    seen = {_node_key(lat, lon) for lat, lon in chain}
    out = list(chain)
    for lat, lon in extra:
        key = _node_key(float(lat), float(lon))
        if key in seen:
            continue
        seen.add(key)
        out.append((float(lat), float(lon)))
    return out


def _backbone_node_indices(
    nodes: Sequence[tuple[float, float]],
    backbone: Sequence[tuple[float, float]],
) -> list[int]:
    keys = {_node_key(float(lat), float(lon)) for lat, lon in backbone}
    return [i for i, (lat, lon) in enumerate(nodes) if _node_key(lat, lon) in keys]


def _line_arc_m(line_m, lat: float, lon: float) -> float:
    pt_m = gpd.GeoDataFrame(geometry=[Point(float(lon), float(lat))], crs="EPSG:4326").to_crs(
        "EPSG:3857"
    ).geometry.iloc[0]
    return float(line_m.project(pt_m))


def _fill_chain_rf_gaps(
    chain: list[tuple[float, float]],
    *,
    line: LineString,
    max_hop_m: float,
    eligible: BaseGeometry,
    max_snap_m: float,
    jobs: int,
    verbose: bool,
    progress_label: str,
) -> list[tuple[float, float]]:
    """Insert relay nodes when snap dedupe leaves consecutive chain points farther than RF range."""
    if len(chain) < 2:
        return chain

    max_seg_m = max(50.0, float(max_hop_m) * 0.85)
    line_m = gpd.GeoDataFrame(geometry=[line], crs="EPSG:4326").to_crs("EPSG:3857").geometry.iloc[0]
    out = list(chain)
    inserted = 0
    idx = 0
    while idx < len(out) - 1:
        lat_a, lon_a = out[idx]
        lat_b, lon_b = out[idx + 1]
        dist = _haversine_m(lat_a, lon_a, lat_b, lon_b)
        if dist <= max_seg_m:
            idx += 1
            continue

        arc_a = _line_arc_m(line_m, lat_a, lon_a)
        arc_b = _line_arc_m(line_m, lat_b, lon_b)
        span = abs(arc_b - arc_a)
        n_insert = max(1, int(dist / max_seg_m))
        step = span / (n_insert + 1) if span > 0 else 0.0
        sample_points: list[tuple[float, float]] = []
        for k in range(1, n_insert + 1):
            pt_m = line_m.interpolate(arc_a + step * k)
            lon, lat = _FROM_M.transform(float(pt_m.x), float(pt_m.y))
            sample_points.append((float(lon), float(lat)))

        gap_nodes = _snap_points_to_eligible(
            eligible,
            sample_points,
            max_snap_m=max_snap_m,
            jobs=jobs,
            verbose=False,
            progress_label=progress_label,
        )
        to_insert: list[tuple[float, float]] = []
        for node in gap_nodes:
            key = _node_key(node[0], node[1])
            if _node_key(out[idx][0], out[idx][1]) == key:
                continue
            if _node_key(out[idx + 1][0], out[idx + 1][1]) == key:
                continue
            if to_insert and _node_key(to_insert[-1][0], to_insert[-1][1]) == key:
                continue
            to_insert.append(node)

        if to_insert:
            out[idx + 1 : idx + 1] = to_insert
            inserted += len(to_insert)
            continue
        idx += 1

    if verbose and inserted:
        suggest_progress(
            verbose,
            f"{progress_label}: gap-fill inserted {inserted} relay(s) for segments > {max_seg_m / 1000.0:.0f} km",
        )
    return out


def _snap_points_to_eligible(
    eligible: BaseGeometry,
    points: Sequence[tuple[float, float]],
    *,
    max_snap_m: float,
    jobs: int,
    verbose: bool,
    progress_label: str,
) -> list[tuple[float, float]]:
    """Snap ``(lon, lat)`` samples to eligible land; parallel when ``jobs > 1``."""
    if not points:
        return []

    elig = _prepare_eligible(eligible)
    workers = max(1, int(jobs))
    ticker = SuggestProgressTicker(verbose, label=progress_label, interval_s=0.5)
    payload = [(float(lon), float(lat), float(max_snap_m)) for lon, lat in points]

    if workers <= 1 or len(payload) <= 1:
        ticker.maybe(f"snap {len(payload)} point(s), workers=1", force=True)
        snapped = [
            _snap_point_to_eligible(elig, lon=lon, lat=lat, max_snap_m=max_snap_m)
            for lon, lat, max_snap_m in payload
        ]
        for idx in range(1, len(payload) + 1):
            ticker.maybe(f"snap [{idx}/{len(payload)}]")
        out = _dedupe_snapped_nodes(snapped)
        ticker.done(f"{len(out)} eligible relay(s) from {len(payload)} sample(s)")
        return out

    mx = min(workers, len(payload))
    n_chunks = min(len(payload), max(mx * 4, mx))
    chunk_size = max(1, (len(payload) + n_chunks - 1) // n_chunks)
    chunks: list[list[tuple[float, float, float]]] = [
        payload[i : i + chunk_size] for i in range(0, len(payload), chunk_size)
    ]
    ticker.maybe(f"snap {len(payload)} point(s), workers={mx}, chunks={len(chunks)}", force=True)

    eligible_wkb = elig.wkb
    snapped: list[tuple[float, float] | None] = [None] * len(payload)
    chunks_done = 0
    with ProcessPoolExecutor(max_workers=mx, initializer=_init_snap_worker, initargs=(eligible_wkb,)) as pool:
        futs = {}
        for chunk_idx, chunk in enumerate(chunks):
            start = chunk_idx * chunk_size
            fut = pool.submit(_snap_worker_chunk, chunk)
            futs[fut] = start
        for fut in as_completed(futs):
            start = futs[fut]
            for offset, item in enumerate(fut.result()):
                snapped[start + offset] = item
            chunks_done += 1
            completed = min(len(payload), chunks_done * chunk_size)
            ticker.maybe(f"snap [{completed}/{len(payload)}]")

    out = _dedupe_snapped_nodes(snapped)
    ticker.done(f"{len(out)} eligible relay(s) from {len(payload)} sample(s), workers={mx}")
    return out


def _snap_worker_chunk(chunk: Sequence[tuple[float, float, float]]) -> list[tuple[float, float] | None]:
    return [_snap_worker(item) for item in chunk]


_ATTACH_GX: float | None = None
_ATTACH_GY: float | None = None
_ATTACH_PREP = None


def _init_attach_scan_worker(eligible_wkb: bytes, gx: float, gy: float) -> None:
    from shapely import from_wkb
    from shapely.prepared import prep

    global _ATTACH_GX, _ATTACH_GY, _ATTACH_PREP
    _ATTACH_GX = float(gx)
    _ATTACH_GY = float(gy)
    _ATTACH_PREP = prep(from_wkb(eligible_wkb))


def _attach_scan_chunk(coords_m: Sequence[tuple[float, float]]) -> tuple[float, float | None, float | None]:
    if _ATTACH_PREP is None or _ATTACH_GX is None or _ATTACH_GY is None:
        raise RuntimeError("attachment scan worker not initialized")
    gx, gy = _ATTACH_GX, _ATTACH_GY
    best_d2 = float("inf")
    best_lon: float | None = None
    best_lat: float | None = None
    for x, y in coords_m:
        lon, lat = _FROM_M.transform(float(x), float(y))
        pt = Point(float(lon), float(lat))
        if not _ATTACH_PREP.intersects(pt):
            continue
        d2 = (float(x) - gx) ** 2 + (float(y) - gy) ** 2
        if d2 < best_d2:
            best_d2 = d2
            best_lon = float(lon)
            best_lat = float(lat)
    return best_d2, best_lon, best_lat


def _scan_attachment_boundary_coords(
    coords_m: Sequence[tuple[float, float]],
    *,
    gx: float,
    gy: float,
    eligible: BaseGeometry,
    jobs: int,
    verbose: bool,
) -> tuple[tuple[float, float] | None, float]:
    """Return eligible boundary point closest to goal (projected meters) and its distance."""
    if not coords_m:
        return None, float("inf")

    ticker = SuggestProgressTicker(verbose, label="attachment scan", interval_s=0.5)
    workers = max(1, int(jobs))
    payload = [(float(x), float(y)) for x, y in coords_m]

    if workers <= 1 or len(payload) <= 256:
        ticker.maybe(f"scan {len(payload)} boundary coord(s), workers=1", force=True)
        from shapely.prepared import prep

        prep_elig = prep(_prepare_eligible(eligible))
        best_d2 = float("inf")
        best: tuple[float, float] | None = None
        for idx, (x, y) in enumerate(payload, start=1):
            lon, lat = _FROM_M.transform(x, y)
            pt = Point(lon, lat)
            if not prep_elig.intersects(pt):
                continue
            d2 = (x - gx) ** 2 + (y - gy) ** 2
            if d2 < best_d2:
                best_d2 = d2
                best = (lon, lat)
            ticker.maybe(f"scan [{idx}/{len(payload)}], best={(best_d2**0.5) / 1000.0:.1f} km")
        best_d = best_d2**0.5 if best is not None else float("inf")
        ticker.done(
            f"scanned {len(payload)} coord(s)"
            + (f", best={best_d / 1000.0:.1f} km" if best is not None else ", no eligible point")
        )
        return best, best_d

    mx = min(workers, len(payload))
    n_chunks = min(len(payload), max(mx * 4, mx))
    chunk_size = max(256, (len(payload) + n_chunks - 1) // n_chunks)
    chunks: list[list[tuple[float, float]]] = [
        payload[i : i + chunk_size] for i in range(0, len(payload), chunk_size)
    ]
    ticker.maybe(
        f"scan {len(payload)} boundary coord(s), workers={mx}, chunks={len(chunks)}",
        force=True,
    )

    elig_wkb = _prepare_eligible(eligible).wkb
    best_d2 = float("inf")
    best: tuple[float, float] | None = None
    chunks_done = 0
    checked = 0
    with ProcessPoolExecutor(
        max_workers=mx,
        initializer=_init_attach_scan_worker,
        initargs=(elig_wkb, gx, gy),
    ) as pool:
        futs = {pool.submit(_attach_scan_chunk, chunk): len(chunk) for chunk in chunks}
        for fut in as_completed(futs):
            chunk_d2, lon, lat = fut.result()
            checked += futs[fut]
            chunks_done += 1
            if lon is not None and lat is not None and chunk_d2 < best_d2:
                best_d2 = chunk_d2
                best = (lon, lat)
            ticker.maybe(
                f"scan [{checked}/{len(payload)}] chunk {chunks_done}/{len(chunks)}, "
                f"best={(best_d2**0.5) / 1000.0:.1f} km"
            )

    best_d = best_d2**0.5 if best is not None else float("inf")
    ticker.done(
        f"scanned {len(payload)} coord(s), workers={mx}"
        + (f", best={best_d / 1000.0:.1f} km" if best is not None else ", no eligible point")
    )
    return best, best_d


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    import math

    r = 6_378_137.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    return 2 * r * math.asin(min(1.0, a**0.5))


def _geodesic_relay_nodes(
    *,
    attach_lon: float,
    attach_lat: float,
    goal_lon: float,
    goal_lat: float,
    eligible: BaseGeometry,
    spacing_m: float,
    max_snap_m: float,
    max_hop_m: float | None = None,
    extra_nodes: Sequence[tuple[float, float]] = (),
    jobs: int = 1,
    verbose: bool = False,
    progress_label: str = "relay grid",
) -> list[tuple[float, float]]:
    """Eligible relay candidates along attachment→goal bearing (nodes only, hops may skip ineligible land)."""
    line = LineString([(float(attach_lon), float(attach_lat)), (float(goal_lon), float(goal_lat))])
    line_m = gpd.GeoDataFrame(geometry=[line], crs="EPSG:4326").to_crs("EPSG:3857").geometry.iloc[0]
    length = float(line_m.length)
    spacing = max(50.0, float(spacing_m))
    n = max(1, int(length / spacing)) if length > 0 else 0

    sample_points: list[tuple[float, float]] = [(float(attach_lon), float(attach_lat))]
    for i in range(1, n):
        pt_m = line_m.interpolate(i * spacing)
        lon, lat = _FROM_M.transform(float(pt_m.x), float(pt_m.y))
        sample_points.append((float(lon), float(lat)))
    sample_points.append((float(goal_lon), float(goal_lat)))

    if verbose:
        suggest_progress(
            verbose,
            f"{progress_label}: line {length / 1000.0:.1f} km, {n} grid sample(s) every {spacing:.0f} m, "
            f"workers={max(1, int(jobs))}",
        )

    chain_nodes = _snap_points_to_eligible(
        eligible,
        sample_points,
        max_snap_m=max_snap_m,
        jobs=jobs,
        verbose=verbose,
        progress_label=progress_label,
    )

    chain_nodes = _fill_chain_rf_gaps(
        chain_nodes,
        line=line,
        max_hop_m=float(max_hop_m) if max_hop_m is not None else max(spacing_m * 80.0, 60_000.0),
        eligible=eligible,
        max_snap_m=max_snap_m,
        jobs=jobs,
        verbose=verbose,
        progress_label=progress_label,
    )

    terminal_key = _node_key(float(goal_lat), float(goal_lon))
    if not chain_nodes or _node_key(chain_nodes[-1][0], chain_nodes[-1][1]) != terminal_key:
        wide = max(max_snap_m, spacing_m * 4.0, 5000.0)
        snapped = _snap_eligible(eligible, lon=goal_lon, lat=goal_lat, max_snap_m=wide)
        if snapped is not None:
            slon, slat = snapped
            key = _node_key(slat, slon)
            chain_nodes = [(la, lo) for la, lo in chain_nodes if _node_key(la, lo) != key]
            chain_nodes.append((slat, slon))
            if verbose:
                suggest_progress(
                    verbose,
                    f"{progress_label}: wide goal snap, {len(chain_nodes)} chain relay(s)",
                )

    nodes = _append_extra_relay_nodes(chain_nodes, extra_nodes)
    if verbose and extra_nodes:
        added = len(nodes) - len(chain_nodes)
        suggest_progress(
            verbose,
            f"{progress_label}: {len(chain_nodes)} chain relay(s), {added} backbone node(s) appended",
        )
    return nodes


def _existing_eligible_backbone_nodes(ctx: SiteSuggestionContext) -> list[tuple[float, float]]:
    from peaky_finders.site_suggestions.providers.mesh_backbone.completion import all_backbone_sites

    elig = ctx.eligible_ll if ctx.eligible_ll.is_valid else make_valid(ctx.eligible_ll)
    out: list[tuple[float, float]] = []
    for site in all_backbone_sites(ctx):
        pt = Point(float(site.lon), float(site.lat))
        if elig.intersects(pt):
            out.append((float(site.lat), float(site.lon)))
    return out


_RF_HOP_BATCH_CHUNK = 64


def _build_mutual_hop_adjacency(
    nodes: list[tuple[float, float]],
    *,
    max_hop_m: float,
    rf_json: str,
    jobs: int = 1,
    verbose: bool,
) -> dict[int, list[int]]:
    ticker = SuggestProgressTicker(verbose, label="RF adjacency", interval_s=0.5)
    pairs: list[tuple[float, float, float, float]] = []
    pair_indices: list[tuple[int, int]] = []
    for i, (lat_a, lon_a) in enumerate(nodes):
        for j in range(i + 1, len(nodes)):
            lat_b, lon_b = nodes[j]
            if _haversine_m(lat_a, lon_a, lat_b, lon_b) > max_hop_m:
                continue
            pairs.append((lat_a, lon_a, lat_b, lon_b))
            pair_indices.append((i, j))
        ticker.maybe(f"pair index {i + 1}/{len(nodes)}, {len(pairs)} pair(s) so far")

    if not pairs:
        ticker.done(f"{len(nodes)} node(s), 0 pair(s) within {max_hop_m / 1000.0:.0f} km")
        return {i: [] for i in range(len(nodes))}

    ticker.maybe(
        f"{len(nodes)} node(s), {len(pairs)} pair(s) within {max_hop_m / 1000.0:.0f} km",
        force=True,
    )
    session = splatter_session(verbose=verbose)
    ticker.maybe(f"ensuring DEM tiles for {len(nodes)} node(s)…", force=True)
    ensure_dem_for_points(
        session,
        nodes,
        buffer_m=max_hop_m * 0.05 + 5000.0,
    )
    ticker.maybe(f"evaluating {len(pairs)} mutual hop(s) in batches of {_RF_HOP_BATCH_CHUNK}…", force=True)
    viable = mutual_hop_batches(
        pairs,
        rf_json=rf_json,
        chunk_size=_RF_HOP_BATCH_CHUNK,
        jobs=jobs,
        verbose=verbose,
    )
    if len(viable) != len(pair_indices):
        raise RuntimeError(
            f"RF link batch length mismatch: {len(viable)} results for {len(pair_indices)} pair(s)"
        )

    adj: dict[int, list[int]] = {i: [] for i in range(len(nodes))}
    for (i, j), ok in zip(pair_indices, viable):
        if not ok:
            continue
        adj[i].append(j)
        adj[j].append(i)
    for i in adj:
        adj[i].sort()
    viable_count = sum(viable)
    ticker.done(
        f"{viable_count}/{len(viable)} viable hop(s), "
        f"{sum(len(v) for v in adj.values()) // 2} edge(s)"
    )
    return adj


def _min_hop_path(
    adj: dict[int, list[int]],
    *,
    start: int,
    goal: int,
    blocked_edges: set[tuple[int, int]],
) -> list[int] | None:
    if start == goal:
        return [start]
    queue: deque[int] = deque([start])
    prev: dict[int, int | None] = {start: None}
    while queue:
        node = queue.popleft()
        for nb in adj.get(node, ()):
            edge = (min(node, nb), max(node, nb))
            if edge in blocked_edges:
                continue
            if nb in prev:
                continue
            prev[nb] = node
            if nb == goal:
                path: list[int] = []
                cur: int | None = goal
                while cur is not None:
                    path.append(cur)
                    cur = prev[cur]
                path.reverse()
                return path
            queue.append(nb)
    return None


def _min_hop_path_from_starts(
    adj: dict[int, list[int]],
    *,
    starts: Sequence[int],
    goal: int,
    blocked_edges: set[tuple[int, int]],
) -> list[int] | None:
    best: list[int] | None = None
    for start in starts:
        path = _min_hop_path(adj, start=start, goal=goal, blocked_edges=blocked_edges)
        if path is None:
            continue
        if best is None or len(path) < len(best):
            best = path
    return best


def _connected_components(adj: dict[int, list[int]], node_count: int) -> list[set[int]]:
    visited = [False] * node_count
    comps: list[set[int]] = []
    for start in range(node_count):
        if visited[start]:
            continue
        comp: set[int] = set()
        queue: deque[int] = deque([start])
        visited[start] = True
        while queue:
            node = queue.popleft()
            comp.add(node)
            for nb in adj.get(node, ()):
                if not visited[nb]:
                    visited[nb] = True
                    queue.append(nb)
        comps.append(comp)
    return comps


def _log_corridor_path_failure(
    *,
    goal_key: str,
    nodes: list[tuple[float, float]],
    adj: dict[int, list[int]],
    start_indices: Sequence[int],
    goal_idx: int,
    verbose: bool,
) -> None:
    comps = _connected_components(adj, len(nodes))
    sizes = sorted((len(c) for c in comps), reverse=True)
    goal_comp = next(c for c in comps if goal_idx in c)
    start_comps = {id(c) for c in comps for s in start_indices if s in c}
    goal_reachable = any(id(goal_comp) == sc for sc in start_comps)
    start_degrees = {i: len(adj.get(i, ())) for i in start_indices}
    suggest_progress(
        verbose,
        f"corridor {goal_key}: no path — {len(comps)} RF component(s), sizes={sizes[:5]}"
        f"{'' if len(sizes) <= 5 else '…'}, "
        f"goal component={len(goal_comp)}, start↔goal connected={goal_reachable}, "
        f"start degree(s)={start_degrees}",
    )


def _path_to_line(nodes: list[tuple[float, float]], indices: list[int]) -> LineString:
    coords = [(float(nodes[i][1]), float(nodes[i][0])) for i in indices]
    if len(coords) < 2:
        if len(coords) == 1:
            lon, lat = coords[0]
            return LineString([(lon, lat), (lon, lat)])
        return LineString()
    line = LineString(coords)
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
    verbose: bool = False,
    jobs: int = 1,
) -> tuple[float, float] | None:
    """Eligible point on/near coverage boundary closest to ``goal``."""
    if verbose:
        suggest_progress(verbose, "attachment scan: building composite coverage…")
    coverage = composite_coverage_geometry(ctx, verbose=verbose)
    if coverage is None or coverage.is_empty:
        if verbose:
            suggest_progress(verbose, "attachment scan: no coverage — using goal point")
        return float(goal.lat), float(goal.lon)

    if verbose:
        suggest_progress(verbose, "attachment scan: projecting coverage boundary…")
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

    coords_m: list[tuple[float, float]] = []
    for part in parts:
        if part.is_empty:
            continue
        coords_m.extend((float(x), float(y)) for x, y in part.coords)

    if verbose:
        suggest_progress(
            verbose,
            f"attachment scan: {len(parts)} boundary part(s), {len(coords_m)} coord(s)",
        )

    best, best_d = _scan_attachment_boundary_coords(
        coords_m,
        gx=gx,
        gy=gy,
        eligible=elig,
        jobs=jobs,
        verbose=verbose,
    )

    if best is not None:
        lon, lat = best
        suggest_log(
            verbose,
            f"site suggest:   attachment at ({lat:.5f}, {lon:.5f}), {best_d / 1000.0:.1f} km from goal",
        )
        return lon, lat

    pt_goal = Point(float(goal.lon), float(goal.lat))
    nearest = cov.boundary.interpolate(cov.boundary.project(pt_goal)) if not cov.boundary.is_empty else cov.centroid
    if elig.intersects(nearest):
        if verbose:
            suggest_progress(
                verbose,
                f"attachment scan: via boundary project ({float(nearest.y):.5f}, {float(nearest.x):.5f})",
            )
        return float(nearest.x), float(nearest.y)
    if verbose:
        suggest_progress(verbose, "attachment scan: no eligible attachment on coverage boundary")
    return None


def plan_corridors_for_goal(
    ctx: SiteSuggestionContext,
    *,
    goal_key: str,
    k: int = 1,
    cell_m: float = 750.0,
    penalty_mask: object | None = None,
    plan_generation: int | None = None,
    verbose: bool = False,
) -> list[CorridorPath]:
    """Plan up to ``k`` min-hop RF corridors from current mesh toward ``goal_key``."""
    del penalty_mask  # legacy eligible-grid API; alternates use blocked RF edges

    goal = goal_point_for_key(ctx, goal_key)
    if goal is None:
        return []

    suggest_progress(verbose, f"corridor {goal_key}: find attachment on coverage boundary…")
    attach = attachment_point_toward_goal(ctx, goal=goal, verbose=verbose, jobs=ctx.jobs)
    if attach is None:
        suggest_log(verbose, f"site suggest:   corridor {goal_key}: no attachment on eligible coverage")
        if ctx.corridor_state is not None and plan_generation is not None:
            from peaky_finders.site_suggestions.corridor_kml import update_corridor_planning_state

            update_corridor_planning_state(
                ctx,
                goal_key,
                note="no attachment on eligible coverage",
                status="planning",
            )
        return []

    attach_lon, attach_lat = attach
    if ctx.corridor_state is not None and plan_generation is not None:
        from peaky_finders.site_suggestions.corridor_kml import update_corridor_planning_state

        update_corridor_planning_state(
            ctx,
            goal_key,
            attachment=(attach_lat, attach_lon),
            note="placing relay grid",
        )
    elig = ctx.eligible_ll if ctx.eligible_ll.is_valid else make_valid(ctx.eligible_ll)
    spacing_m = max(50.0, float(cell_m))
    max_snap_m = max(spacing_m, spacing_m * 0.75)
    max_hop_m = max_hop_range_m(ctx.preset)
    rf_json = rf_json_for_preset(ctx.preset)
    goal_dist_km = _haversine_m(attach_lat, attach_lon, float(goal.lat), float(goal.lon)) / 1000.0

    suggest_log(
        verbose,
        f"site suggest:   corridor {goal_key}: attachment=({attach_lat:.5f}, {attach_lon:.5f}), "
        f"goal=({float(goal.lat):.5f}, {float(goal.lon):.5f}), {goal_dist_km:.1f} km",
    )

    with suggest_step(verbose, f"corridor RF plan {goal_key} (k={k}, spacing={spacing_m:.0f} m)"):
        suggest_progress(verbose, f"corridor {goal_key}: collect backbone relay node(s)…")
        extra = _existing_eligible_backbone_nodes(ctx)
        if verbose and extra:
            suggest_progress(verbose, f"corridor {goal_key}: {len(extra)} existing backbone node(s)")
        nodes = _geodesic_relay_nodes(
            attach_lon=attach_lon,
            attach_lat=attach_lat,
            goal_lon=float(goal.lon),
            goal_lat=float(goal.lat),
            eligible=elig,
            spacing_m=spacing_m,
            max_snap_m=max_snap_m,
            max_hop_m=max_hop_m,
            extra_nodes=extra,
            jobs=ctx.jobs,
            verbose=verbose,
            progress_label=f"corridor {goal_key}",
        )
        if len(nodes) < 2:
            suggest_log(verbose, f"site suggest:   corridor {goal_key}: fewer than 2 eligible relay node(s)")
            if ctx.corridor_state is not None and plan_generation is not None:
                from peaky_finders.site_suggestions.corridor_kml import update_corridor_planning_state

                update_corridor_planning_state(
                    ctx,
                    goal_key,
                    relay_nodes=nodes,
                    note="fewer than 2 eligible relay node(s)",
                )
            return []

        terminal_key = _node_key(float(goal.lat), float(goal.lon))
        goal_idx = next(
            (i for i in range(len(nodes) - 1, -1, -1) if _node_key(nodes[i][0], nodes[i][1]) == terminal_key),
            len(nodes) - 1,
        )
        start_indices = sorted(set([0, *_backbone_node_indices(nodes, extra)]))
        start_indices = [i for i in start_indices if i != goal_idx]
        if not start_indices:
            suggest_log(
                verbose,
                f"site suggest:   corridor {goal_key}: no route start(s) distinct from goal",
            )
            return []
        suggest_log(
            verbose,
            f"site suggest:   corridor {goal_key}: {len(nodes)} relay candidate(s), "
            f"{len(start_indices)} route start(s), max_hop={max_hop_m / 1000.0:.0f} km",
        )
        if ctx.corridor_state is not None and plan_generation is not None:
            from peaky_finders.site_suggestions.corridor_kml import update_corridor_planning_state

            update_corridor_planning_state(
                ctx,
                goal_key,
                relay_nodes=nodes,
                note=f"{len(nodes)} relay candidate(s); evaluating RF hops",
            )
        suggest_progress(verbose, f"corridor {goal_key}: building RF adjacency graph…")
        adj = _build_mutual_hop_adjacency(
            nodes,
            max_hop_m=max_hop_m,
            rf_json=rf_json,
            jobs=ctx.jobs,
            verbose=verbose,
        )
        edge_count = sum(len(v) for v in adj.values()) // 2
        suggest_log(verbose, f"site suggest:   corridor {goal_key}: {edge_count} mutual RF edge(s)")
        if ctx.corridor_state is not None and plan_generation is not None:
            from peaky_finders.site_suggestions.corridor_kml import update_corridor_planning_state

            update_corridor_planning_state(
                ctx,
                goal_key,
                note=f"{edge_count} mutual RF edge(s); routing variants",
            )

        out: list[CorridorPath] = []
        blocked_edges: set[tuple[int, int]] = set()
        k_variants = max(1, int(k))

        for variant in range(k_variants):
            suggest_progress(
                verbose,
                f"corridor {goal_key}: min-hop route variant {variant + 1}/{k_variants}…",
            )
            indices = _min_hop_path_from_starts(
                adj,
                starts=start_indices,
                goal=goal_idx,
                blocked_edges=blocked_edges,
            )
            if indices is None:
                suggest_progress(verbose, f"corridor {goal_key}: variant {variant + 1} — no path")
                _log_corridor_path_failure(
                    goal_key=goal_key,
                    nodes=nodes,
                    adj=adj,
                    start_indices=start_indices,
                    goal_idx=goal_idx,
                    verbose=verbose,
                )
                break
            if len(indices) < 2:
                suggest_progress(
                    verbose,
                    f"corridor {goal_key}: variant {variant + 1} — trivial path (start=goal)",
                )
                continue
            line = _path_to_line(nodes, indices)
            if line.is_empty:
                continue
            lm = line if line.is_valid else make_valid(line)
            length_m = float(
                gpd.GeoDataFrame(geometry=[lm], crs="EPSG:4326").to_crs("EPSG:3857").geometry.iloc[0].length
            )
            if length_m <= 0.0:
                suggest_progress(
                    verbose,
                    f"corridor {goal_key}: variant {variant + 1} — degenerate route ({length_m:.1f} m)",
                )
                continue
            hops = max(0, len(indices) - 1)
            corridor = CorridorPath(
                goal_key=goal_key,
                line=lm,
                length_m=length_m,
                attachment_lon=attach_lon,
                attachment_lat=attach_lat,
                variant=variant,
                hop_count=hops,
            )
            out.append(corridor)
            if ctx.corridor_state is not None and plan_generation is not None:
                from peaky_finders.site_suggestions.corridor_kml import (
                    append_corridor_route,
                    update_corridor_planning_state,
                )

                append_corridor_route(
                    ctx,
                    goal_key=goal_key,
                    corridor=corridor,
                    plan_generation=int(plan_generation),
                    set_active=variant == 0,
                )
                update_corridor_planning_state(
                    ctx,
                    goal_key,
                    note=f"variant {variant + 1}/{k_variants}: {hops} hop(s), {length_m / 1000.0:.1f} km",
                )
            for a, b in zip(indices, indices[1:]):
                blocked_edges.add((min(a, b), max(a, b)))
            suggest_progress(
                verbose,
                f"corridor {goal_key}: variant {variant + 1} — {hops} hop(s), {length_m / 1000.0:.1f} km",
            )

        if ctx.corridor_state is not None and plan_generation is not None:
            from peaky_finders.site_suggestions.corridor_kml import update_corridor_planning_state

            update_corridor_planning_state(
                ctx,
                goal_key,
                note=f"{len(out)} route(s) planned" if out else "no RF route",
                status="active" if out else "planning",
            )

        suggest_log(
            verbose,
            f"site suggest:   corridor {goal_key}: {len(out)} route(s), "
            f"attach=({attach_lat:.5f}, {attach_lon:.5f}), "
            f"hops={out[0].hop_count}, length={out[0].length_m / 1000.0:.1f} km"
            if out
            else f"site suggest:   corridor {goal_key}: 0 routes",
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
    suggest_progress(
        ctx.verbose,
        f"corridor: active={state.active_goal_key or '?'} "
        f"routes={len(state.corridors)} variant={state.corridor_index + 1 if state.corridors else 0} "
        f"blocked={len(state.blocked_goal_keys)}",
    )

    if state.active_goal_key and is_goal_captured(ctx, str(state.active_goal_key)):
        from peaky_finders.site_suggestions.corridor_kml import finalize_corridor_goal

        suggest_progress(ctx.verbose, f"corridor: finalize captured goal {state.active_goal_key}…")
        finalize_corridor_goal(ctx, goal_key=str(state.active_goal_key), status="captured")
        suggest_progress(ctx.verbose, f"corridor: goal {state.active_goal_key} captured — clearing active route")
        state.active_goal_key = None
        state.corridors = []
        state.corridor_index = 0
        state.stall_count = 0
        state.recovery_buffer_bonus_m = 0.0

    if state.active_goal_key is None:
        suggest_progress(ctx.verbose, "corridor: pick next uncaptured goal…")
        pending = ordered_uncaptured_goal_keys(ctx)
        if not pending:
            suggest_progress(ctx.verbose, "corridor: no uncaptured goals remain")
            return None
        state.active_goal_key = pending[0]
        state.corridors = []
        state.corridor_index = 0
        state.stall_count = 0
        state.recovery_buffer_bonus_m = 0.0
        suggest_log(
            ctx.verbose,
            f"site suggest:   corridor: activate goal {state.active_goal_key} "
            f"({len(pending)} uncaptured, {len(state.blocked_goal_keys)} blocked)",
        )
        from peaky_finders.site_suggestions.corridor_kml import init_corridor_goal_kml

        suggest_progress(ctx.verbose, f"corridor: init debug kml for {state.active_goal_key}…")
        init_corridor_goal_kml(ctx, str(state.active_goal_key))

    if not state.corridors or state.corridor_index >= len(state.corridors):
        k = max(1, int(mb.corridor_k))
        cell_m = float(mb.corridor_grid_cell_m)
        suggest_log(
            ctx.verbose,
            f"site suggest:   corridor: plan routes for {state.active_goal_key} (k={k})",
        )
        state.corridor_plan_generation += 1
        plan_generation = state.corridor_plan_generation
        state.corridors = plan_corridors_for_goal(
            ctx,
            goal_key=str(state.active_goal_key),
            k=k,
            cell_m=cell_m,
            plan_generation=plan_generation,
            verbose=ctx.verbose,
        )
        state.corridor_index = 0
        if state.corridors:
            from peaky_finders.site_suggestions.corridor_kml import mark_active_corridor

            gk = str(state.active_goal_key)
            mark_active_corridor(
                ctx,
                goal_key=gk,
                variant=state.corridor_index,
                plan_generation=plan_generation,
            )
        if not state.corridors:
            if state.active_goal_key:
                from peaky_finders.site_suggestions.corridor_kml import finalize_corridor_goal

                suggest_progress(ctx.verbose, f"corridor: finalize blocked goal {state.active_goal_key}…")
                finalize_corridor_goal(ctx, goal_key=str(state.active_goal_key), status="blocked")
                state.blocked_goal_keys.add(str(state.active_goal_key))
                suggest_log(
                    ctx.verbose,
                    f"site suggest:   corridor: blocked goal {state.active_goal_key} (no RF route)",
                )
            blocked_key = str(state.active_goal_key) if state.active_goal_key else "?"
            state.active_goal_key = None
            suggest_progress(ctx.verbose, f"corridor: retry with next goal after blocking {blocked_key}…")
            return ensure_active_corridor(ctx)
    else:
        corridor = active_corridor(ctx)
        suggest_log(
            ctx.verbose,
            f"site suggest:   corridor: reuse route variant {state.corridor_index + 1}/"
            f"{len(state.corridors)} for {state.active_goal_key}"
            + (f" ({corridor.hop_count} hops, {corridor.length_m / 1000.0:.1f} km)" if corridor else ""),
        )

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
    from peaky_finders.site_suggestions.providers.mesh_backbone.completion import (
        all_backbone_sites,
        captured_goal_keys,
        footprints_for_backbone_sites,
        hop_adjacency,
        hop_reachable_from,
        mesh_connectivity_complete,
        sites_capturing_goal,
        uncaptured_goal_keys,
    )
    from peaky_finders.site_suggestions.providers.mesh_backbone.geom import goals_from_config

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
            "hop_count": corridor.hop_count,
        },
        "geometry": mapping(corridor.line),
    }
