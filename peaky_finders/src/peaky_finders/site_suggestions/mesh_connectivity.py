"""Mesh healing: join disconnected hop components before normal goal grow."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import geopandas as gpd
from pyproj import Transformer
from shapely import make_valid
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from peaky_finders.site_suggestions.context import BackboneSite, SiteSuggestionContext
import peaky_finders.site_suggestions.mesh_backbone_completion as mesh_completion
from peaky_finders.site_suggestions.mesh_backbone_completion import (
    all_backbone_sites,
    hop_adjacency,
    hop_connected_components,
    mesh_connectivity_complete,
)
from peaky_finders.site_suggestions.mesh_backbone_geom import GoalPoint
from peaky_finders.sites_job import SiteSuggestionStrategy

_TO_M = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)

_CORRIDOR_TRIAL_SLUG = "__corridor_trial__"


@dataclass(frozen=True)
class ComponentGap:
    """Bridge target between two hop components (closest site-pin pair)."""

    comp_a: int
    comp_b: int
    distance_m: float
    site_a_slug: str
    site_b_slug: str
    point_a_lon: float
    point_a_lat: float
    point_b_lon: float
    point_b_lat: float


@dataclass(frozen=True)
class HealingContext:
    """Active mesh-healing pass: grow main mesh toward satellite bridge goals."""

    main_slugs: frozenset[str]
    bridge_goals: dict[str, GoalPoint]


def _to_m3857(geom: BaseGeometry) -> BaseGeometry | None:
    if geom is None or geom.is_empty:
        return None
    g = geom if geom.is_valid else make_valid(geom)
    return gpd.GeoDataFrame(geometry=[g], crs="EPSG:4326").to_crs("EPSG:3857").geometry.iloc[0]


def _component_union_m3857(
    *,
    comp_slugs: set[str],
    footprints: dict[str, BaseGeometry | None],
) -> BaseGeometry | None:
    parts: list[BaseGeometry] = []
    for slug in comp_slugs:
        fp = footprints.get(slug)
        if fp is None or fp.is_empty:
            continue
        fm = _to_m3857(fp if fp.is_valid else make_valid(fp))
        if fm is not None and not fm.is_empty:
            parts.append(fm)
    if not parts:
        return None
    union = unary_union(parts)
    if union is None or union.is_empty:
        return None
    return union if union.is_valid else make_valid(union)


def _main_component_id(
    components: list[set[str]],
    footprints: dict[str, BaseGeometry | None],
) -> int:
    best_id = 0
    best_area = -1.0
    for comp_id, slugs in enumerate(components):
        geom = _component_union_m3857(comp_slugs=slugs, footprints=footprints)
        area = float(geom.area) if geom is not None else 0.0
        if area > best_area:
            best_area = area
            best_id = int(comp_id)
    return best_id


def _closest_site_pin_gap(
    *,
    comp_a: set[str],
    comp_b: set[str],
    sites_by_slug: dict[str, BackboneSite],
) -> tuple[float, BackboneSite, BackboneSite] | None:
    best_dist = float("inf")
    best_pair: tuple[BackboneSite, BackboneSite] | None = None
    for slug_a in sorted(comp_a):
        site_a = sites_by_slug.get(slug_a)
        if site_a is None:
            continue
        ax, ay = _TO_M.transform(float(site_a.lon), float(site_a.lat))
        pa = Point(ax, ay)
        for slug_b in sorted(comp_b):
            site_b = sites_by_slug.get(slug_b)
            if site_b is None:
                continue
            bx, by = _TO_M.transform(float(site_b.lon), float(site_b.lat))
            dist_m = float(pa.distance(Point(bx, by)))
            if dist_m < best_dist:
                best_dist = dist_m
                best_pair = (site_a, site_b)
    if best_pair is None:
        return None
    return best_dist, best_pair[0], best_pair[1]


def _component_snapshot(ctx: SiteSuggestionContext) -> tuple[
    list[BackboneSite],
    dict[str, BackboneSite],
    dict[str, BaseGeometry | None],
    list[set[str]],
    int,
]:
    sites = all_backbone_sites(ctx)
    sites_by_slug = {s.slug: s for s in sites}
    footprints = mesh_completion.footprints_for_backbone_sites(ctx.plan, ctx.session_footprints)
    adj = hop_adjacency(sites, footprints)
    components = hop_connected_components(adj, [s.slug for s in sites])
    main_id = _main_component_id(components, footprints)
    return sites, sites_by_slug, footprints, components, main_id


def satellite_in_main_component(
    *,
    satellite_slug: str,
    sites: Sequence[BackboneSite],
    footprints: Mapping[str, BaseGeometry | None],
    trial_lat: float | None = None,
    trial_lon: float | None = None,
    trial_footprint: BaseGeometry | None = None,
) -> bool:
    """True when ``satellite_slug`` shares a hop component with the main mesh."""
    sites_list = list(sites)
    fps: dict[str, BaseGeometry | None] = dict(footprints)
    if trial_footprint is not None and trial_lat is not None and trial_lon is not None:
        fp = trial_footprint if trial_footprint.is_valid else make_valid(trial_footprint)
        sites_list.append(
            BackboneSite(slug=_CORRIDOR_TRIAL_SLUG, lat=float(trial_lat), lon=float(trial_lon))
        )
        fps[_CORRIDOR_TRIAL_SLUG] = fp
    if satellite_slug not in {s.slug for s in sites_list}:
        return False
    adj = hop_adjacency(sites_list, fps)
    components = hop_connected_components(adj, [s.slug for s in sites_list])
    main_id = _main_component_id(components, fps)
    return satellite_slug in components[main_id]


def mesh_healing_needed(ctx: SiteSuggestionContext) -> bool:
    """True when mesh-backbone should run a healing pass before preset goals."""
    if ctx.cfg.strategy != SiteSuggestionStrategy.MESH_BACKBONE:
        return False
    return not mesh_connectivity_complete(ctx)


def mesh_connectivity_phase(ctx: SiteSuggestionContext) -> str | None:
    """``heal`` while disconnected; ``None`` during normal goal grow."""
    if mesh_healing_needed(ctx):
        return "heal"
    return None


def healing_goals(ctx: SiteSuggestionContext) -> dict[str, GoalPoint]:
    """Ephemeral bridge goals: closest satellite pin per disconnected component."""
    if not mesh_healing_needed(ctx):
        return {}

    _sites, sites_by_slug, footprints, components, main_id = _component_snapshot(ctx)
    if len(components) < 2:
        return {}

    main_slugs = components[main_id]
    goals: dict[str, GoalPoint] = {}
    for comp_id, comp_slugs in enumerate(components):
        if comp_id == main_id:
            continue
        row = _closest_site_pin_gap(
            comp_a=main_slugs,
            comp_b=comp_slugs,
            sites_by_slug=sites_by_slug,
        )
        if row is None:
            continue
        _dist_m, site_a, site_b = row
        sat = site_b if site_b.slug in comp_slugs else site_a
        key = f"bridge:{sat.slug}"
        goals[key] = GoalPoint(key=key, lat=float(sat.lat), lon=float(sat.lon))
    return dict(sorted(goals.items()))


def uncaptured_healing_goals(ctx: SiteSuggestionContext) -> dict[str, GoalPoint]:
    """Bridge goals whose satellite site is not yet in the main hop component."""
    from peaky_finders.site_suggestions.mesh_goals import bridge_satellite_slug

    goals = healing_goals(ctx)
    if not goals:
        return {}

    sites = all_backbone_sites(ctx)
    footprints = mesh_completion.footprints_for_backbone_sites(ctx.plan, ctx.session_footprints)
    missing = {
        key
        for key in goals
        if not satellite_in_main_component(
            satellite_slug=bridge_satellite_slug(key),
            sites=sites,
            footprints=footprints,
        )
    }
    return {key: goals[key] for key in sorted(missing)}


def healing_context(ctx: SiteSuggestionContext) -> HealingContext | None:
    """Main-component scope + all bridge goals for the current healing pass."""
    if not mesh_healing_needed(ctx):
        return None
    goals = healing_goals(ctx)
    if not goals:
        return None
    _sites, _sites_by_slug, footprints, components, main_id = _component_snapshot(ctx)
    return HealingContext(
        main_slugs=frozenset(components[main_id]),
        bridge_goals=goals,
    )


def minimum_component_gap(
    ctx: SiteSuggestionContext,
    *,
    star_to_main: bool = False,
) -> ComponentGap | None:
    """Closest hop-component pair by site-pin distance (not footprint union)."""
    del star_to_main
    sites = all_backbone_sites(ctx)
    if len(sites) < 2:
        return None

    sites_by_slug = {s.slug: s for s in sites}
    footprints = mesh_completion.footprints_for_backbone_sites(ctx.plan, ctx.session_footprints)
    adj = hop_adjacency(sites, footprints)
    slugs = [s.slug for s in sites]
    components = hop_connected_components(adj, slugs)
    if len(components) < 2:
        return None

    main_id = _main_component_id(components, footprints)
    pairs = [(main_id, j) for j in range(len(components)) if j != main_id]

    best: ComponentGap | None = None
    for comp_a, comp_b in pairs:
        row = _closest_site_pin_gap(
            comp_a=components[comp_a],
            comp_b=components[comp_b],
            sites_by_slug=sites_by_slug,
        )
        if row is None:
            continue
        dist_m, site_a, site_b = row
        if best is None or dist_m < best.distance_m:
            best = ComponentGap(
                comp_a=comp_a,
                comp_b=comp_b,
                distance_m=float(dist_m),
                site_a_slug=str(site_a.slug),
                site_b_slug=str(site_b.slug),
                point_a_lon=float(site_a.lon),
                point_a_lat=float(site_a.lat),
                point_b_lon=float(site_b.lon),
                point_b_lat=float(site_b.lat),
            )
    return best


def main_footprint_slugs(ctx: SiteSuggestionContext) -> frozenset[str] | None:
    """Main-component slugs when attaching from main mesh (bridge goals or greedy heal)."""
    from peaky_finders.site_suggestions.mesh_goals import is_bridge_goal_key

    active: str | None = None
    if ctx.corridor_state is not None:
        active = ctx.corridor_state.active_goal_key
    if active is not None and is_bridge_goal_key(active):
        healing = healing_context(ctx)
        return healing.main_slugs if healing is not None else None
    if mesh_healing_needed(ctx):
        healing = healing_context(ctx)
        return healing.main_slugs if healing is not None else None
    return None
