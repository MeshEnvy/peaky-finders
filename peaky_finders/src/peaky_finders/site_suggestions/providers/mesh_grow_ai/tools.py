"""Mesh-grow-ai tool schemas and dispatch."""

from __future__ import annotations

import json
import time
from collections import deque
from pathlib import Path
from typing import Any

from shapely.geometry import Point

from peaky_finders.site_suggestions.candidates import SiteCandidate
from peaky_finders.site_suggestions.context import SiteSuggestionContext
from peaky_finders.site_suggestions.corridor import _haversine_m
from peaky_finders.site_suggestions.eligible_peaks_cache import load_or_build_eligible_peaks
from peaky_finders.site_suggestions.ephemeral_viewshed import (
    candidate_viewshed_workdir,
    run_ephemeral_viewshed_footprint,
)
from peaky_finders.site_suggestions.providers.mesh_backbone.completion import (
    all_backbone_sites,
    footprints_for_backbone_sites,
    hop_adjacency,
    hop_reachable_from,
    mutual_hop_neighbors,
    site_location_key,
    sites_capturing_goal,
)
from peaky_finders.site_suggestions.providers.mesh_backbone.geom import GoalPoint, goals_from_config
from peaky_finders.site_suggestions.providers.mesh_backbone.scoring import (
    build_mesh_grow_score_context,
    score_mesh_grow_trial,
)
from peaky_finders.site_suggestions.providers.mesh_grow_ai.session import AgentSession
from peaky_finders.site_suggestions.providers.mesh_grow_config import grow_goals_config_from_ctx
from peaky_finders.site_suggestions.rf_link import mutual_hop_viable, propagation_request_json, splatter_session

ToolResult = dict[str, Any]

_EMPTY_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": False}

_LAT_LON = {
    "type": "object",
    "properties": {
        "lat": {"type": "number", "description": "WGS-84 latitude"},
        "lon": {"type": "number", "description": "WGS-84 longitude"},
    },
    "required": ["lat", "lon"],
    "additionalProperties": False,
}


def _tool(name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters,
        },
    }


TOOL_SCHEMAS: list[dict[str, Any]] = [
    _tool("snapshot", "Current mesh, goals, and connectivity summary.", _EMPTY_SCHEMA),
    _tool(
        "submit_proposals",
        "End the episode; hand proposal buffer to the planner.",
        _EMPTY_SCHEMA,
    ),
    _tool("point_in_eligible", "Check whether a coordinate lies on deployable land.", _LAT_LON),
    _tool(
        "geodesic_distance_m",
        "Great-circle distance between two WGS-84 points in meters.",
        {
            "type": "object",
            "properties": {
                "lat1": {"type": "number"},
                "lon1": {"type": "number"},
                "lat2": {"type": "number"},
                "lon2": {"type": "number"},
            },
            "required": ["lat1", "lon1", "lat2", "lon2"],
            "additionalProperties": False,
        },
    ),
    _tool("elevation_m", "Terrain elevation at a point (null when unavailable).", _LAT_LON),
    _tool(
        "eligible_peaks_near",
        "Skadi peak candidates near a point on eligible land.",
        {
            "type": "object",
            "properties": {
                "lat": {"type": "number"},
                "lon": {"type": "number"},
                "radius_m": {"type": "number", "default": 750.0},
                "limit": {"type": "integer", "default": 8},
            },
            "required": ["lat", "lon"],
            "additionalProperties": False,
        },
    ),
    _tool(
        "hop_neighbors",
        "Mutual-hop neighbor slugs for an existing site.",
        {
            "type": "object",
            "properties": {"site_slug": {"type": "string"}},
            "required": ["site_slug"],
            "additionalProperties": False,
        },
    ),
    _tool(
        "hop_component",
        "All site slugs in the same hop component as the given site.",
        {
            "type": "object",
            "properties": {"site_slug": {"type": "string"}},
            "required": ["site_slug"],
            "additionalProperties": False,
        },
    ),
    _tool(
        "footprints_covering_point",
        "Existing site slugs whose footprint covers a point.",
        _LAT_LON,
    ),
    _tool(
        "goal_captured",
        "Whether a configured goal is already captured.",
        {
            "type": "object",
            "properties": {"goal_key": {"type": "string"}},
            "required": ["goal_key"],
            "additionalProperties": False,
        },
    ),
    _tool("uncaptured_goals", "Goal keys not yet captured.", _EMPTY_SCHEMA),
    _tool(
        "rf_link_mutual_viable",
        "Pin-to-pin mutual decode viability (cheap RF check, no footprint).",
        {
            "type": "object",
            "properties": {
                "lat_a": {"type": "number"},
                "lon_a": {"type": "number"},
                "lat_b": {"type": "number"},
                "lon_b": {"type": "number"},
            },
            "required": ["lat_a", "lon_a", "lat_b", "lon_b"],
            "additionalProperties": False,
        },
    ),
    _tool(
        "evaluate_site",
        "Run viewshed trial at a coordinate and return mesh-grow score metrics.",
        _LAT_LON,
    ),
    _tool(
        "propose_site",
        "Add an evaluated coordinate to the episode proposal buffer.",
        {
            "type": "object",
            "properties": {
                "lat": {"type": "number"},
                "lon": {"type": "number"},
                "note": {"type": "string"},
            },
            "required": ["lat", "lon"],
            "additionalProperties": False,
        },
    ),
]


def dispatch_tool(
    name: str,
    args: dict[str, Any],
    *,
    ctx: SiteSuggestionContext,
    session: AgentSession,
    preset_path: Path,
) -> ToolResult:
    handlers = {
        "snapshot": lambda: _snapshot(ctx, session),
        "submit_proposals": lambda: _submit_proposals(ctx, session),
        "point_in_eligible": lambda: _point_in_eligible(ctx, args),
        "geodesic_distance_m": lambda: _geodesic_distance_m(args),
        "elevation_m": lambda: _elevation_m(args),
        "eligible_peaks_near": lambda: _eligible_peaks_near(ctx, args),
        "hop_neighbors": lambda: _hop_neighbors(ctx, args),
        "hop_component": lambda: _hop_component(ctx, args),
        "footprints_covering_point": lambda: _footprints_covering_point(ctx, args),
        "goal_captured": lambda: _goal_captured(ctx, args),
        "uncaptured_goals": lambda: _uncaptured_goals(ctx),
        "rf_link_mutual_viable": lambda: _rf_link_mutual_viable(ctx, args),
        "evaluate_site": lambda: _evaluate_site(ctx, session, preset_path, args),
        "propose_site": lambda: _propose_site(ctx, session, args),
    }
    fn = handlers.get(name)
    if fn is None:
        return {"error": f"unknown tool: {name!r}"}
    return fn()


def _snapshot(ctx: SiteSuggestionContext, session: AgentSession) -> ToolResult:
    grow = grow_goals_config_from_ctx(ctx)
    goals_cfg = goals_from_config(grow)
    sites = all_backbone_sites(ctx)
    footprints = footprints_for_backbone_sites(ctx.plan, ctx.session_footprints)
    captured = {
        key
        for key, goal in goals_cfg.items()
        if sites_capturing_goal(goal, sites, footprints)
    }
    seed_slugs = set(ctx.preset.sites.keys())
    adjacency = hop_adjacency(sites, footprints)
    reachable = hop_reachable_from(start_slugs=seed_slugs, adjacency=adjacency) if seed_slugs else set()
    components = _hop_components(adjacency, [s.slug for s in sites])
    goal_rows: list[dict[str, Any]] = []
    for key, goal in goals_cfg.items():
        captors = sites_capturing_goal(goal, sites, footprints)
        goal_rows.append(
            {
                "key": key,
                "lat": goal.lat,
                "lon": goal.lon,
                "captured": key in captured,
                "reachable_from_seeds": bool(captors & reachable) if captors else False,
            }
        )

    uncaptured = [g["key"] for g in goal_rows if not g["captured"]]
    return {
        "iteration": session.iteration,
        "radius_km": float(ctx.preset.simulation.radius_km),
        "sites": [{"slug": s.slug, "lat": s.lat, "lon": s.lon} for s in sites],
        "goals": goal_rows,
        "uncaptured_goals": uncaptured,
        "hop_component_count": len(components),
    }

def _submit_proposals(ctx: SiteSuggestionContext, session: AgentSession) -> ToolResult:
    if session.submitted:
        return session.submit_result or {"error": "already submitted"}
    accepted: list[dict[str, float]] = []
    rejected: list[dict[str, str]] = []
    seen_keys: set[tuple[int, int]] = set()
    cap = int(ctx.cfg.mesh_grow_ai.max_candidates_per_round)
    for prop in session.proposals:
        lat = float(prop["lat"])
        lon = float(prop["lon"])
        key = site_location_key(lat, lon)
        if key in seen_keys:
            rejected.append({"lat": lat, "lon": lon, "reason": "duplicate proposal"})
            continue
        seen_keys.add(key)
        if len(accepted) >= cap:
            rejected.append({"lat": lat, "lon": lon, "reason": "max_candidates_per_round exceeded"})
            continue
        accepted.append({"lat": lat, "lon": lon})
    result = {"accepted": accepted, "rejected": rejected, "count": len(accepted)}
    session.submitted = True
    session.submit_result = result
    return result


def _point_in_eligible(ctx: SiteSuggestionContext, args: dict[str, Any]) -> ToolResult:
    lat = float(args["lat"])
    lon = float(args["lon"])
    pt = Point(lon, lat)
    return {"eligible": bool(ctx.eligible_ll.covers(pt))}


def _geodesic_distance_m(args: dict[str, Any]) -> ToolResult:
    dist = _haversine_m(
        float(args["lat1"]),
        float(args["lon1"]),
        float(args["lat2"]),
        float(args["lon2"]),
    )
    return {"distance_m": float(dist)}


def _elevation_m(args: dict[str, Any]) -> ToolResult:
    del args
    return {"elevation_m": None}


def _eligible_peaks_near(ctx: SiteSuggestionContext, args: dict[str, Any]) -> ToolResult:
    lat = float(args["lat"])
    lon = float(args["lon"])
    radius_m = float(args.get("radius_m", 750.0))
    limit = int(args.get("limit", 8))
    peaks_llz, _ = load_or_build_eligible_peaks(
        suggest_root=ctx.suggest_root,
        eligible_ll=ctx.eligible_ll,
        eligible_sha=ctx.eligible_sha,
        dem_mirror_root=ctx.dem_mirror_root,
        bin_size_m=150.0,
        verbose=False,
    )
    ranked: list[tuple[float, float, float, float]] = []
    for plat, plon, pelev in peaks_llz:
        d = _haversine_m(lat, lon, plat, plon)
        if d <= radius_m:
            ranked.append((d, plat, plon, pelev))
    ranked.sort(key=lambda row: (-row[3], row[0]))
    out = [
        {"lat": plat, "lon": plon, "elev_m": pelev}
        for _, plat, plon, pelev in ranked[: max(0, limit)]
    ]
    return {"peaks": out}


def _hop_neighbors(ctx: SiteSuggestionContext, args: dict[str, Any]) -> ToolResult:
    slug = str(args["site_slug"])
    sites = all_backbone_sites(ctx)
    footprints = footprints_for_backbone_sites(ctx.plan, ctx.session_footprints)
    adjacency = hop_adjacency(sites, footprints)
    return {"neighbors": sorted(adjacency.get(slug, ()))}


def _hop_component(ctx: SiteSuggestionContext, args: dict[str, Any]) -> ToolResult:
    slug = str(args["site_slug"])
    sites = all_backbone_sites(ctx)
    footprints = footprints_for_backbone_sites(ctx.plan, ctx.session_footprints)
    adjacency = hop_adjacency(sites, footprints)
    for comp in _hop_components(adjacency, [s.slug for s in sites]):
        if slug in comp:
            return {"members": sorted(comp)}
    return {"members": [slug] if slug else []}


def _hop_components(adjacency: dict[str, set[str]], slugs: list[str]) -> list[set[str]]:
    from peaky_finders.site_suggestions.providers.mesh_backbone.completion import hop_connected_components

    return hop_connected_components(adjacency, slugs)


def _footprints_covering_point(ctx: SiteSuggestionContext, args: dict[str, Any]) -> ToolResult:
    lat = float(args["lat"])
    lon = float(args["lon"])
    pt = Point(lon, lat)
    sites = all_backbone_sites(ctx)
    footprints = footprints_for_backbone_sites(ctx.plan, ctx.session_footprints)
    slugs: list[str] = []
    for site in sites:
        fp = footprints.get(site.slug)
        if fp is not None and not fp.is_empty and fp.covers(pt):
            slugs.append(site.slug)
    return {"site_slugs": sorted(slugs)}


def _goal_captured(ctx: SiteSuggestionContext, args: dict[str, Any]) -> ToolResult:
    from peaky_finders.site_suggestions.providers.mesh_backbone.goals import is_goal_captured

    key = str(args["goal_key"])
    return {"captured": bool(is_goal_captured(ctx, key))}


def _uncaptured_goals(ctx: SiteSuggestionContext) -> ToolResult:
    grow = grow_goals_config_from_ctx(ctx)
    goals_cfg = goals_from_config(grow)
    sites = all_backbone_sites(ctx)
    footprints = footprints_for_backbone_sites(ctx.plan, ctx.session_footprints)
    captured = {
        key
        for key, goal in goals_cfg.items()
        if sites_capturing_goal(goal, sites, footprints)
    }
    keys = sorted(set(goals_cfg.keys()) - captured)
    return {"keys": keys}


def _rf_link_mutual_viable(ctx: SiteSuggestionContext, args: dict[str, Any]) -> ToolResult:
    rf_json = propagation_request_json(ctx.preset)
    session = splatter_session(verbose=False)
    viable = mutual_hop_viable(
        session,
        lat_a=float(args["lat_a"]),
        lon_a=float(args["lon_a"]),
        lat_b=float(args["lat_b"]),
        lon_b=float(args["lon_b"]),
        rf_json=rf_json,
    )
    return {"viable": bool(viable)}


def _evaluate_site(
    ctx: SiteSuggestionContext,
    session: AgentSession,
    preset_path: Path,
    args: dict[str, Any],
) -> ToolResult:
    ai = ctx.cfg.mesh_grow_ai
    if session.viewshed_evals_used >= int(ai.max_viewshed_evals_per_episode):
        return {"error": "max_viewshed_evals_per_episode exceeded"}

    lat = float(args["lat"])
    lon = float(args["lon"])
    loc_key = site_location_key(lat, lon)
    if loc_key in session.evaluated:
        return session.evaluated[loc_key]

    pt = Point(lon, lat)
    if not ctx.eligible_ll.covers(pt):
        return {"error": "point not on eligible land"}

    t0 = time.monotonic()
    viewshed_root = Path(ctx.plan.viewsheds_root)
    workdir = candidate_viewshed_workdir(
        preset=ctx.preset,
        viewshed_root=viewshed_root,
        lat=lat,
        lon=lon,
    )
    footprint = run_ephemeral_viewshed_footprint(
        preset=ctx.preset,
        preset_path=preset_path,
        lat=lat,
        lon=lon,
        workdir=workdir,
        verbose=ctx.verbose,
    )
    session.viewshed_evals_used += 1
    elapsed = time.monotonic() - t0

    score_ctx = build_mesh_grow_score_context(ctx)
    if score_ctx is None or footprint is None or footprint.is_empty:
        result: ToolResult = {
            "connected": False,
            "mutual_hop_neighbor_slugs": [],
            "mutual_hop_count": 0,
            "footprint_area_m2": 0.0,
            "goals_captured_by_footprint": [],
            "goal_distance_delta_m": {},
            "total_positive_goal_delta_m": 0.0,
            "grid_marginal_gain_cells": 0,
            "hop_path_len_to_nearest_seed": None,
            "viewshed_elapsed_s": round(elapsed, 3),
        }
        session.evaluated[loc_key] = result
        return result

    score = score_mesh_grow_trial(
        score_ctx=score_ctx,
        lat=lat,
        lon=lon,
        trial_footprint=footprint,
    )
    if score is None:
        result = {
            "connected": False,
            "mutual_hop_neighbor_slugs": [],
            "mutual_hop_count": 0,
            "footprint_area_m2": 0.0,
            "goals_captured_by_footprint": [],
            "goal_distance_delta_m": {},
            "total_positive_goal_delta_m": 0.0,
            "grid_marginal_gain_cells": int(ctx.grid.marginal_gain_cells(footprint, goal_depth=1)),
            "hop_path_len_to_nearest_seed": None,
            "viewshed_elapsed_s": round(elapsed, 3),
        }
    else:
        hop_len = _hop_path_len_to_nearest_seed(ctx, score.hop_neighbors)
        result = {
            "connected": True,
            "mutual_hop_neighbor_slugs": list(score.hop_neighbors),
            "mutual_hop_count": len(score.hop_neighbors),
            "footprint_area_m2": float(score.footprint_area_m2),
            "goals_captured_by_footprint": list(score.captured_goal_keys),
            "goal_distance_delta_m": dict(score.delta_by_goal_m),
            "total_positive_goal_delta_m": float(
                sum(max(0.0, d) for d in score.delta_by_goal_m.values())
            ),
            "grid_marginal_gain_cells": int(ctx.grid.marginal_gain_cells(footprint, goal_depth=1)),
            "hop_path_len_to_nearest_seed": hop_len,
            "viewshed_elapsed_s": round(elapsed, 3),
        }
    session.evaluated[loc_key] = result
    return result


def _hop_path_len_to_nearest_seed(ctx: SiteSuggestionContext, neighbor_slugs: tuple[str, ...]) -> int | None:
    if not neighbor_slugs:
        return None
    seed_slugs = set(ctx.preset.sites.keys())
    sites = all_backbone_sites(ctx)
    footprints = footprints_for_backbone_sites(ctx.plan, ctx.session_footprints)
    adjacency = hop_adjacency(sites, footprints)
    best: int | None = None
    for start in neighbor_slugs:
        dist = _bfs_hop_distance(start, seed_slugs, adjacency)
        if dist is not None and (best is None or dist < best):
            best = dist
    return best


def _bfs_hop_distance(start: str, targets: set[str], adjacency: dict[str, set[str]]) -> int | None:
    if start in targets:
        return 0
    seen = {start}
    queue: deque[tuple[str, int]] = deque([(start, 0)])
    while queue:
        node, depth = queue.popleft()
        for nb in sorted(adjacency.get(node, ())):
            if nb in seen:
                continue
            if nb in targets:
                return depth + 1
            seen.add(nb)
            queue.append((nb, depth + 1))
    return None


def _propose_site(ctx: SiteSuggestionContext, session: AgentSession, args: dict[str, Any]) -> ToolResult:
    lat = float(args["lat"])
    lon = float(args["lon"])
    loc_key = site_location_key(lat, lon)
    if loc_key not in session.evaluated:
        return {"error": "requires prior evaluate_site at same coordinates"}
    if "error" in session.evaluated[loc_key]:
        return {"error": "cannot propose failed evaluation"}

    pt = Point(lon, lat)
    if not ctx.eligible_ll.covers(pt):
        return {"error": "point not on eligible land"}

    sites = all_backbone_sites(ctx)
    for site in sites:
        if site_location_key(site.lat, site.lon) == loc_key:
            return {"error": "duplicate of existing site"}

    for prop in session.proposals:
        if site_location_key(float(prop["lat"]), float(prop["lon"])) == loc_key:
            return {"error": "duplicate proposal"}

    entry: dict[str, Any] = {"lat": lat, "lon": lon}
    if "note" in args:
        entry["note"] = str(args["note"])
    session.proposals.append(entry)
    return {"ok": True}


def proposals_to_candidates(session: AgentSession) -> list[SiteCandidate]:
    if not session.submit_result:
        return []
    out: list[SiteCandidate] = []
    for item in session.submit_result.get("accepted", ()):
        out.append(
            SiteCandidate(
                lat=float(item["lat"]),
                lon=float(item["lon"]),
                elev_m=None,
                strategy="mesh-grow-ai",
            )
        )
    return out


def truncate_tool_result_for_stream(result: ToolResult, *, max_chars: int = 4000) -> ToolResult:
    text = json.dumps(result, default=str)
    if len(text) <= max_chars:
        return result
    return {"summary": text[: max_chars - 3] + "..."}
