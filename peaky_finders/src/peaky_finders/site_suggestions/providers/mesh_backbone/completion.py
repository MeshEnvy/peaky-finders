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
from peaky_finders.site_suggestions.providers.mesh_backbone.geom import GoalPoint, goals_from_preset
from peaky_finders.site_suggestions.rf_link import (
    ensure_dem_for_points,
    max_hop_range_m,
    mutual_hop_batch,
    rf_json_for_preset,
    splatter_session,
)
from peaky_finders.sites_job import Preset, SiteEntry, SiteSuggestionStrategy, SiteType


def backbone_sites_from_preset(sites: Mapping[str, object]) -> list[BackboneSite]:
    """Build backbone nodes from ``Preset.sites`` entries (``lat``/``lon`` properties)."""
    out: list[BackboneSite] = []
    for slug, ent in sites.items():
        out.append(BackboneSite(slug=str(slug), lat=float(ent.lat), lon=float(ent.lon)))
    return out


def sites_capturing_goal_footprint(
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


def _rf_viable_goal_site_pairs(
    preset: Preset,
    goals: Mapping[str, SiteEntry],
    sites: Sequence[BackboneSite],
    footprint_captors: Mapping[str, set[str]],
    *,
    verbose: bool = False,
) -> set[tuple[str, str]]:
    """Goal/site slug pairs with mutual RF hop where footprint already covers the goal."""
    pairs: list[tuple[float, float, float, float]] = []
    pair_keys: list[tuple[str, str]] = []
    max_hop_m = max_hop_range_m(preset)
    for key, goal in goals.items():
        gp = GoalPoint(key=str(key), lat=float(goal.lat), lon=float(goal.lon))
        for slug in footprint_captors.get(key, ()):
            site = next((s for s in sites if s.slug == slug), None)
            if site is None:
                continue
            pairs.append((float(goal.lat), float(goal.lon), float(site.lat), float(site.lon)))
            pair_keys.append((key, slug))
    if not pairs:
        return set()
    rf_json = rf_json_for_preset(preset)
    points = [(lat_a, lon_a) for lat_a, lon_a, _, _ in pairs]
    points.extend((lat_b, lon_b) for _, _, lat_b, lon_b in pairs)
    session = splatter_session(verbose=verbose)
    ensure_dem_for_points(
        session,
        points,
        buffer_m=max_hop_m * 0.05 + 5000.0,
    )
    viable = mutual_hop_batch(session, pairs, rf_json=rf_json)
    return {pair_keys[i] for i, ok in enumerate(viable) if ok}


def sites_capturing_goal(
    goal: GoalPoint,
    sites: Sequence[BackboneSite],
    footprints: Mapping[str, BaseGeometry | None],
    *,
    preset: Preset,
    rf_pairs: set[tuple[str, str]] | None = None,
    verbose: bool = False,
) -> set[str]:
    """Site slugs that capture a goal (footprint covers point and mutual RF link)."""
    footprint_slugs = sites_capturing_goal_footprint(goal, sites, footprints)
    if not footprint_slugs:
        return set()
    if rf_pairs is None:
        goals_map = {goal.key: preset.goals[goal.key]} if goal.key in preset.goals else {}
        if not goals_map:
            return set()
        rf_pairs = _rf_viable_goal_site_pairs(
            preset,
            goals_map,
            sites,
            {goal.key: footprint_slugs},
            verbose=verbose,
        )
    return {slug for gk, slug in rf_pairs if gk == goal.key and slug in footprint_slugs}


def captured_goal_keys(
    goals: Mapping[str, SiteEntry],
    sites: Sequence[BackboneSite],
    footprints: Mapping[str, BaseGeometry | None],
    preset: Preset,
    *,
    verbose: bool = False,
) -> set[str]:
    goal_points = goals_from_preset(goals)
    if not goal_points:
        return set()
    footprint_captors = {
        key: sites_capturing_goal_footprint(gp, sites, footprints)
        for key, gp in goal_points.items()
    }
    rf_pairs = _rf_viable_goal_site_pairs(
        preset, goals, sites, footprint_captors, verbose=verbose
    )
    return {
        key
        for key, gp in goal_points.items()
        if sites_capturing_goal(
            gp,
            sites,
            footprints,
            preset=preset,
            rf_pairs=rf_pairs,
        )
    }


def uncaptured_goal_keys(
    goals: Mapping[str, SiteEntry],
    sites: Sequence[BackboneSite],
    footprints: Mapping[str, BaseGeometry | None],
    preset: Preset,
    *,
    verbose: bool = False,
) -> set[str]:
    return set(goals.keys()) - captured_goal_keys(
        goals, sites, footprints, preset, verbose=verbose
    )


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


def hop_connected_components(
    adjacency: Mapping[str, set[str]],
    slugs: Sequence[str],
) -> list[set[str]]:
    """Connected components of the hop graph (sorted slug sets)."""
    remaining = set(slugs)
    components: list[set[str]] = []
    while remaining:
        start = min(remaining)
        seen: set[str] = set()
        queue = deque([start])
        while queue:
            node = queue.popleft()
            if node in seen:
                continue
            seen.add(node)
            for nb in sorted(adjacency.get(node, ())):
                if nb not in seen:
                    queue.append(nb)
        components.append(seen)
        remaining -= seen
    return sorted(components, key=lambda comp: min(comp) if comp else "")


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


def site_location_key(lat: float, lon: float) -> tuple[int, int]:
    """Stable location key for duplicate site detection (≈1 m)."""
    return (int(round(float(lat) * 1e5)), int(round(float(lon) * 1e5)))


def all_backbone_sites(ctx: SiteSuggestionContext) -> list[BackboneSite]:
    sites = backbone_sites_from_preset(ctx.preset.repeaters)
    seen = {s.slug for s in sites}
    for site in ctx.session_sites:
        if site.slug not in seen:
            sites.append(site)
            seen.add(site.slug)
    return sites


def anchor_slugs(preset: Preset) -> set[str]:
    """Installed preset site slugs (excludes suggested)."""
    return {
        str(slug)
        for slug, ent in preset.sites.items()
        if ent.type == SiteType.INSTALLED
    }


def mesh_connectivity_complete(ctx: SiteSuggestionContext) -> bool:
    """True when all backbone sites form a single hop component."""
    if ctx.cfg.strategy != SiteSuggestionStrategy.MESH_BACKBONE:
        return True
    sites = all_backbone_sites(ctx)
    if len(sites) <= 1:
        return True
    footprints = footprints_for_backbone_sites(ctx.plan, ctx.session_footprints)
    adjacency = hop_adjacency(sites, footprints)
    components = hop_connected_components(adjacency, [s.slug for s in sites])
    return len(components) <= 1


def mesh_grow_goals_complete(ctx: SiteSuggestionContext) -> bool:
    """True when every preset goal is captured (connectivity ignored)."""
    if ctx.cfg.strategy != SiteSuggestionStrategy.MESH_BACKBONE:
        return False
    goals = ctx.preset.goals
    if not goals:
        return True
    sites = all_backbone_sites(ctx)
    footprints = footprints_for_backbone_sites(ctx.plan, ctx.session_footprints)
    return not uncaptured_goal_keys(
        goals, sites, footprints, ctx.preset, verbose=ctx.verbose
    )


def mesh_grow_planning_complete(ctx: SiteSuggestionContext) -> bool:
    """True when every goal is captured by a site hop-connected to preset seeds."""
    if ctx.cfg.strategy != SiteSuggestionStrategy.MESH_BACKBONE:
        return False
    goals = ctx.preset.goals
    if not goals:
        return True

    if not mesh_connectivity_complete(ctx):
        return False

    sites = all_backbone_sites(ctx)
    footprints = footprints_for_backbone_sites(ctx.plan, ctx.session_footprints)
    uncaptured = uncaptured_goal_keys(
        goals, sites, footprints, ctx.preset, verbose=ctx.verbose
    )
    if uncaptured:
        return False

    seed_slugs = set(ctx.preset.repeaters.keys())
    if not seed_slugs:
        return True

    adjacency = hop_adjacency(sites, footprints)
    reachable = hop_reachable_from(start_slugs=seed_slugs, adjacency=adjacency)
    goal_points = goals_from_preset(goals)
    for key, goal in goal_points.items():
        captors = sites_capturing_goal(
            goal,
            sites,
            footprints,
            preset=ctx.preset,
            verbose=ctx.verbose,
        )
        if not captors or not (captors & reachable):
            return False
    return True


# Back-compat alias used by strategy module
mesh_backbone_planning_complete = mesh_grow_planning_complete
