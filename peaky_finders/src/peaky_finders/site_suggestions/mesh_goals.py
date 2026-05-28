"""Unified preset + bridge goals for mesh-backbone grow."""

from __future__ import annotations

from peaky_finders.site_suggestions.context import SiteSuggestionContext
from peaky_finders.site_suggestions.mesh_backbone_completion import (
    all_backbone_sites,
    captured_goal_keys,
    footprints_for_backbone_sites,
    mesh_connectivity_complete,
    uncaptured_goal_keys,
)
from peaky_finders.site_suggestions.mesh_backbone_geom import GoalPoint, goals_from_config
from peaky_finders.site_suggestions.mesh_connectivity import (
    healing_goals,
    uncaptured_healing_goals,
)

BRIDGE_GOAL_PREFIX = "bridge:"


def is_bridge_goal_key(key: str) -> bool:
    return key.startswith(BRIDGE_GOAL_PREFIX)


def goal_point_for_key(ctx: SiteSuggestionContext, key: str) -> GoalPoint | None:
    """Resolve a preset or ephemeral bridge goal by key."""
    bridge = healing_goals(ctx).get(key)
    if bridge is not None:
        return bridge
    return goals_from_config(ctx.cfg.mesh_backbone).get(key)


def is_goal_captured(ctx: SiteSuggestionContext, goal_key: str) -> bool:
    """True when ``goal_key`` is satisfied (bridge: main mesh; preset: any site)."""
    if is_bridge_goal_key(goal_key):
        return goal_key not in uncaptured_healing_goals(ctx)
    mb = ctx.cfg.mesh_backbone
    sites = all_backbone_sites(ctx)
    footprints = footprints_for_backbone_sites(ctx.plan, ctx.session_footprints)
    return goal_key in captured_goal_keys(mb, sites, footprints)


def uncaptured_effective_goal_keys(ctx: SiteSuggestionContext) -> set[str]:
    """Uncaptured bridge goals (when disconnected) plus uncaptured preset goals."""
    missing: set[str] = set()
    if not mesh_connectivity_complete(ctx):
        missing |= set(uncaptured_healing_goals(ctx).keys())
    mb = ctx.cfg.mesh_backbone
    sites = all_backbone_sites(ctx)
    footprints = footprints_for_backbone_sites(ctx.plan, ctx.session_footprints)
    missing |= uncaptured_goal_keys(mb, sites, footprints)
    return missing


def ordered_uncaptured_goal_keys(ctx: SiteSuggestionContext) -> list[str]:
    """Pending goals: bridge goals first when disconnected, else preset ``goal_order``."""
    blocked = ctx.corridor_state.blocked_goal_keys if ctx.corridor_state else set()

    if not mesh_connectivity_complete(ctx):
        missing = set(uncaptured_healing_goals(ctx).keys()) - blocked
        return sorted(missing)

    mb = ctx.cfg.mesh_backbone
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
