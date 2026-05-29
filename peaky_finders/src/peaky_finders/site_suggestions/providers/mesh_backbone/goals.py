"""Unified preset + bridge goals for mesh-backbone grow."""

from __future__ import annotations

from peaky_finders.site_suggestions.context import SiteSuggestionContext
from peaky_finders.site_suggestions.providers.mesh_backbone.completion import (
    all_backbone_sites,
    captured_goal_keys,
    footprints_for_backbone_sites,
    mesh_connectivity_complete,
    uncaptured_goal_keys,
)
from peaky_finders.site_suggestions.providers.mesh_backbone.geom import GoalPoint, goals_from_config
from peaky_finders.site_suggestions.providers.mesh_grow_config import grow_goals_config
from peaky_finders.site_suggestions.mesh_connectivity import (
    healing_goals,
    mesh_connectivity_complete,
    uncaptured_healing_goals,
)
from peaky_finders.sites_job import BundleSiteSuggestionsConfig, SiteSuggestionStrategy

LAND_GRAB_COVERAGE_GOAL_KEY = "__land_grab_coverage__"

BRIDGE_GOAL_PREFIX = "bridge:"


def is_bridge_goal_key(key: str) -> bool:
    return key.startswith(BRIDGE_GOAL_PREFIX)


def bridge_satellite_slug(goal_key: str) -> str:
    """Installed/suggested site slug for ``bridge:<slug>``."""
    if not is_bridge_goal_key(goal_key):
        raise ValueError(f"not a bridge goal key: {goal_key!r}")
    return goal_key[len(BRIDGE_GOAL_PREFIX) :]


def goal_point_for_key(ctx: SiteSuggestionContext, key: str) -> GoalPoint | None:
    """Resolve a preset or ephemeral bridge goal by key."""
    bridge = healing_goals(ctx).get(key)
    if bridge is not None:
        return bridge
    return goals_from_config(grow_goals_config(ctx.cfg)).get(key)


def is_goal_captured(ctx: SiteSuggestionContext, goal_key: str) -> bool:
    """True when satisfied (bridge: satellite in main hop mesh; preset: footprint covers goal)."""
    if is_bridge_goal_key(goal_key):
        return goal_key not in uncaptured_healing_goals(ctx)
    mb = grow_goals_config(ctx.cfg)
    sites = all_backbone_sites(ctx)
    footprints = footprints_for_backbone_sites(ctx.plan, ctx.session_footprints)
    return goal_key in captured_goal_keys(mb, sites, footprints)


def uncaptured_effective_goal_keys(ctx: SiteSuggestionContext) -> set[str]:
    """Uncaptured bridge goals (when disconnected) plus uncaptured preset goals."""
    missing: set[str] = set()
    if not mesh_connectivity_complete(ctx):
        missing |= set(uncaptured_healing_goals(ctx).keys())
    mb = grow_goals_config(ctx.cfg)
    sites = all_backbone_sites(ctx)
    footprints = footprints_for_backbone_sites(ctx.plan, ctx.session_footprints)
    missing |= uncaptured_goal_keys(mb, sites, footprints)
    return missing


def tracked_goal_keys(ctx: SiteSuggestionContext) -> set[str]:
    """Goal keys the active strategy may satisfy during a suggest pass."""
    if ctx.cfg.strategy in (
        SiteSuggestionStrategy.MESH_BACKBONE,
        SiteSuggestionStrategy.MESH_GROW_AI,
    ):
        keys = set(grow_goals_config(ctx.cfg).goals.keys())
        if not mesh_connectivity_complete(ctx):
            keys |= set(healing_goals(ctx).keys())
        return keys
    if ctx.cfg.strategy == SiteSuggestionStrategy.LAND_GRAB:
        return {LAND_GRAB_COVERAGE_GOAL_KEY}
    return set()


def captured_tracked_goals(ctx: SiteSuggestionContext, *, planning_complete: bool) -> set[str]:
    """Subset of ``tracked_goal_keys`` currently satisfied."""
    keys = tracked_goal_keys(ctx)
    if not keys:
        return set()
    if LAND_GRAB_COVERAGE_GOAL_KEY in keys:
        return {LAND_GRAB_COVERAGE_GOAL_KEY} if planning_complete else set()
    return {key for key in keys if is_goal_captured(ctx, key)}


def goals_satisfied_since(
    ctx: SiteSuggestionContext,
    *,
    planning_complete: bool,
    baseline: set[str],
    tracked_keys: set[str] | None = None,
) -> set[str]:
    """Goals newly satisfied relative to a pass-start snapshot.

    ``tracked_keys`` should be ``tracked_goal_keys`` at pass start so bridge goals
    still count after the mesh connects and they leave the live tracked set.
    """
    keys = tracked_keys if tracked_keys is not None else tracked_goal_keys(ctx)
    if not keys:
        return set()
    if LAND_GRAB_COVERAGE_GOAL_KEY in keys:
        done = {LAND_GRAB_COVERAGE_GOAL_KEY} if planning_complete else set()
        return done - baseline
    return {key for key in keys if is_goal_captured(ctx, key)} - baseline


def ordered_uncaptured_goal_keys(ctx: SiteSuggestionContext) -> list[str]:
    """Pending goals: bridge goals first when disconnected, else preset ``goal_order``."""
    blocked = ctx.corridor_state.blocked_goal_keys if ctx.corridor_state else set()

    if not mesh_connectivity_complete(ctx):
        missing = set(uncaptured_healing_goals(ctx).keys()) - blocked
        return sorted(missing)

    mb = grow_goals_config(ctx.cfg)
    sites = all_backbone_sites(ctx)
    footprints = footprints_for_backbone_sites(ctx.plan, ctx.session_footprints)
    missing = uncaptured_goal_keys(mb, sites, footprints) - blocked
    if not missing:
        return []

    order: list[str] = []
    for key in mb.goal_order:
        if key in missing:
            order.append(key)
    for key in sorted(missing):
        if key not in order:
            order.append(key)
    return order
