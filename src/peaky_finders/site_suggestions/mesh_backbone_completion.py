"""Mesh-grow completion (goal capture + hop connectivity to seeds)."""

from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Mapping, Sequence

from shapely import make_valid
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

from peaky_finders.coverage_footprint import read_coverage_footprint
from peaky_finders.site_suggestions.context import BackboneSite, SiteSuggestionContext
from peaky_finders.site_suggestions.mesh_backbone_geom import GoalPoint, goals_from_config
from peaky_finders.sites_job import MeshBackboneStrategyConfig, SiteSuggestionStrategy


def backbone_sites_from_preset(sites: Mapping[str, object]) -> list[BackboneSite]:
    """Build backbone nodes from ``Preset.sites`` entries (``lat``/``lon`` properties)."""
    out: list[BackboneSite] = []
    for slug, ent in sites.items():
        out.append(BackboneSite(slug=str(slug), lat=float(ent.lat), lon=float(ent.lon)))
    return out


def sites_capturing_goal(
    goal: GoalPoint,
    sites: Sequence[BackboneSite],
    footprints: Mapping[str, BaseGeometry | None],
) -> set[str]:
    """Site slugs whose footprint covers the goal point."""
    pt = Point(float(goal.lon), float(goal.lat))
    out: set[str] = set()
    for site in sites:
        fp = footprints.get(site.slug)
        if fp is None or fp.is_empty:
            continue
        if fp.covers(pt):
            out.add(site.slug)
    return out


def captured_goal_keys(
    cfg: MeshBackboneStrategyConfig,
    sites: Sequence[BackboneSite],
    footprints: Mapping[str, BaseGeometry | None],
) -> set[str]:
    goals = goals_from_config(cfg)
    return {
        key
        for key, goal in goals.items()
        if sites_capturing_goal(goal, sites, footprints)
    }


def uncaptured_goal_keys(
    cfg: MeshBackboneStrategyConfig,
    sites: Sequence[BackboneSite],
    footprints: Mapping[str, BaseGeometry | None],
) -> set[str]:
    return set(cfg.goals.keys()) - captured_goal_keys(cfg, sites, footprints)


def hop_adjacency(
    sites: Sequence[BackboneSite],
    footprints: Mapping[str, BaseGeometry | None],
) -> dict[str, set[str]]:
    """Undirected mutual-footprint adjacency (each pin covered by the other's footprint)."""
    adj: dict[str, set[str]] = {s.slug: set() for s in sites}
    n = len(sites)
    for i in range(n):
        fi = footprints.get(sites[i].slug)
        if fi is None or fi.is_empty:
            continue
        pi = Point(sites[i].lon, sites[i].lat)
        for j in range(i + 1, n):
            fj = footprints.get(sites[j].slug)
            if fj is None or fj.is_empty:
                continue
            pj = Point(sites[j].lon, sites[j].lat)
            if fi.covers(pj) and fj.covers(pi):
                adj[sites[i].slug].add(sites[j].slug)
                adj[sites[j].slug].add(sites[i].slug)
    return adj


def hop_reachable_from(
    *,
    start_slugs: set[str],
    adjacency: Mapping[str, set[str]],
) -> set[str]:
    seen: set[str] = set()
    queue = deque(sorted(start_slugs))
    while queue:
        node = queue.popleft()
        if node in seen:
            continue
        seen.add(node)
        for nb in sorted(adjacency.get(node, ())):
            if nb not in seen:
                queue.append(nb)
    return seen


def mutual_hop_neighbors(
    *,
    new_lat: float,
    new_lon: float,
    new_footprint: BaseGeometry,
    sites: Sequence[BackboneSite],
    footprints: Mapping[str, BaseGeometry | None],
) -> list[str]:
    """Existing site slugs with confirmed mutual-hop to a trial placement."""
    if new_footprint is None or new_footprint.is_empty:
        return []
    fp = new_footprint if new_footprint.is_valid else make_valid(new_footprint)
    p_new = Point(float(new_lon), float(new_lat))
    out: list[str] = []
    for site in sites:
        existing = footprints.get(site.slug)
        if existing is None or existing.is_empty:
            continue
        fe = existing if existing.is_valid else make_valid(existing)
        p_site = Point(float(site.lon), float(site.lat))
        if fe.covers(p_new) and fp.covers(p_site):
            out.append(site.slug)
    return sorted(out)


def footprints_for_backbone_sites(
    plan: object,
    session_footprints: Mapping[str, BaseGeometry],
) -> dict[str, BaseGeometry | None]:
    """Seed workspace footprints plus in-session trial commits."""
    out: dict[str, BaseGeometry | None] = {}
    for ws in getattr(plan, "viewshed_workspaces", ()):
        for slug in ws.site_slugs:
            if slug in out:
                continue
            fp = read_coverage_footprint(Path(ws.splat_gpkg))
            if fp is not None and not fp.is_empty:
                fp = fp if fp.is_valid else make_valid(fp)
            out[str(slug)] = fp
    for slug, fp in session_footprints.items():
        out[str(slug)] = fp if fp.is_valid else make_valid(fp)
    return out


def all_backbone_sites(ctx: SiteSuggestionContext) -> list[BackboneSite]:
    sites = backbone_sites_from_preset(ctx.preset.sites)
    seen = {s.slug for s in sites}
    for site in ctx.session_sites:
        if site.slug not in seen:
            sites.append(site)
            seen.add(site.slug)
    return sites


def mesh_grow_planning_complete(ctx: SiteSuggestionContext) -> bool:
    """True when every goal is captured by a site hop-connected to preset seeds."""
    if ctx.cfg.strategy != SiteSuggestionStrategy.MESH_BACKBONE:
        return False
    mb = ctx.cfg.mesh_backbone
    if not mb.goals:
        return True

    sites = all_backbone_sites(ctx)
    footprints = footprints_for_backbone_sites(ctx.plan, ctx.session_footprints)
    uncaptured = uncaptured_goal_keys(mb, sites, footprints)
    if uncaptured:
        return False

    seed_slugs = set(ctx.preset.sites.keys())
    if not seed_slugs:
        return True

    adjacency = hop_adjacency(sites, footprints)
    reachable = hop_reachable_from(start_slugs=seed_slugs, adjacency=adjacency)
    goals = goals_from_config(mb)
    for key, goal in goals.items():
        captors = sites_capturing_goal(goal, sites, footprints)
        if not captors or not (captors & reachable):
            return False
    return True


# Back-compat alias used by strategy module
mesh_backbone_planning_complete = mesh_grow_planning_complete
