"""Mesh-backbone link geometry and preset config."""

from peaky_finders.site_suggestions.mesh_backbone_completion import (
    BackboneSite,
    LinkCompletionResult,
    all_links_complete,
    anchor_capture_slugs,
    backbone_sites_from_preset,
    evaluate_link_completion,
    evaluate_mesh_backbone_completion,
    hop_adjacency,
    order_sites_along_leg,
    position_along_leg_m,
    sites_in_zone,
)
from peaky_finders.site_suggestions.mesh_backbone_geom import (
    AnchorPoint,
    anchor_from_loc,
    anchors_from_config,
    build_link_leg,
    distance_to_link_m,
    link_search_zone,
    resolve_link_leg,
    resolved_link_legs,
    sample_along_link,
)

__all__ = [
    "AnchorPoint",
    "BackboneSite",
    "LinkCompletionResult",
    "all_links_complete",
    "anchor_capture_slugs",
    "anchor_from_loc",
    "anchors_from_config",
    "backbone_sites_from_preset",
    "build_link_leg",
    "distance_to_link_m",
    "evaluate_link_completion",
    "evaluate_mesh_backbone_completion",
    "hop_adjacency",
    "link_search_zone",
    "order_sites_along_leg",
    "position_along_leg_m",
    "resolve_link_leg",
    "resolved_link_legs",
    "sample_along_link",
    "sites_in_zone",
]
