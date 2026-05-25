"""Mesh-backbone candidate generation on incomplete link strips."""

from __future__ import annotations

from dataclasses import dataclass

from shapely.geometry import LineString
from shapely.geometry.base import BaseGeometry

from peaky_finders.site_suggestions.candidates import SiteCandidate, _dedupe_candidates
from peaky_finders.site_suggestions.context import SiteSuggestionContext
from peaky_finders.site_suggestions.depth_grid import CoverageDepthGrid
from peaky_finders.site_suggestions.mesh_backbone_completion import (
    LinkCompletionResult,
    anchor_capture_slugs,
    all_backbone_sites,
    evaluate_mesh_backbone_completion,
    footprints_for_backbone_sites,
    hop_adjacency,
    position_along_leg_m,
    sites_in_zone,
)
from peaky_finders.site_suggestions.mesh_backbone_geom import (
    AnchorPoint,
    anchors_from_config,
    link_search_zone,
    resolve_link_leg,
    sample_along_link,
)
from peaky_finders.sites_job import MeshBackboneLinkEntry, MeshBackboneStrategyConfig


@dataclass(frozen=True)
class _OpenAnchorBias:
    anchor_key: str
    toward_leg_start: bool


def _distance_m(lon_a: float, lat_a: float, lon_b: float, lat_b: float) -> float:
    from peaky_finders.site_suggestions.mesh_backbone_completion import _distance_m as dist

    return dist(lon_a, lat_a, lon_b, lat_b)


def _reachable_slugs(
    start_slugs: set[str],
    adjacency: dict[str, set[str]],
    valid_slugs: set[str],
) -> set[str]:
    if not start_slugs:
        return set()
    seen: set[str] = set()
    queue = sorted(s for s in start_slugs if s in valid_slugs)
    while queue:
        node = queue.pop(0)
        if node in seen:
            continue
        seen.add(node)
        for nb in sorted(adjacency.get(node, ())):
            if nb in valid_slugs and nb not in seen:
                queue.append(nb)
    return seen


def open_anchor_bias_for_link(
    *,
    link: MeshBackboneLinkEntry,
    leg: LineString,
    zone_ll: BaseGeometry,
    anchors: dict[str, AnchorPoint],
    sites: list,
    footprints: dict[str, BaseGeometry | None],
    endpoint_capture_m: float,
) -> _OpenAnchorBias:
    """Pick the anchor side to extend toward for an incomplete link."""
    a_key, b_key = link.endpoints
    start_anchor = anchors[a_key]
    end_anchor = anchors[b_key]
    in_zone = sites_in_zone(sites, zone_ll)
    zone_slugs = {s.slug for s in in_zone}
    start_slugs = anchor_capture_slugs(in_zone, start_anchor, capture_m=endpoint_capture_m)
    end_slugs = anchor_capture_slugs(in_zone, end_anchor, capture_m=endpoint_capture_m)
    if not start_slugs:
        return _OpenAnchorBias(anchor_key=a_key, toward_leg_start=True)
    if not end_slugs:
        return _OpenAnchorBias(anchor_key=b_key, toward_leg_start=False)

    adjacency = hop_adjacency(in_zone, footprints)
    from_start = _reachable_slugs(start_slugs, adjacency, zone_slugs)
    from_end = _reachable_slugs(end_slugs, adjacency, zone_slugs)
    if from_start & end_slugs or from_end & start_slugs or (from_start & from_end):
        return _OpenAnchorBias(anchor_key=b_key, toward_leg_start=False)

    near_m = max(float(endpoint_capture_m) * 4.0, 20_000.0)
    start_near = sum(
        1 for s in in_zone if _distance_m(s.lon, s.lat, start_anchor.lon, start_anchor.lat) <= near_m
    )
    end_near = sum(
        1 for s in in_zone if _distance_m(s.lon, s.lat, end_anchor.lon, end_anchor.lat) <= near_m
    )
    if start_near > end_near:
        return _OpenAnchorBias(anchor_key=b_key, toward_leg_start=False)
    if end_near > start_near:
        return _OpenAnchorBias(anchor_key=a_key, toward_leg_start=True)
    return _OpenAnchorBias(anchor_key=b_key, toward_leg_start=False)


def _candidate_passes_edge_filter(
    *,
    grid: CoverageDepthGrid,
    lon: float,
    lat: float,
    goal_depth: int,
    open_anchor: AnchorPoint,
    endpoint_capture_m: float,
) -> bool:
    depth = grid.depth_at_point(lon, lat)
    edge_depth = max(0, int(goal_depth) - 1)
    gain = grid.point_marginal_gain_cells(lon, lat, goal_depth=goal_depth)
    near_open = _distance_m(lon, lat, open_anchor.lon, open_anchor.lat) <= max(
        float(endpoint_capture_m) * 3.0,
        15_000.0,
    )
    if depth == edge_depth:
        return True
    if depth < edge_depth and gain > 0:
        return True
    if depth == 0 and near_open:
        return True
    return False


def candidates_for_incomplete_link(
    *,
    result: LinkCompletionResult,
    leg: LineString,
    zone_ll: BaseGeometry,
    anchors: dict[str, AnchorPoint],
    sites: list,
    footprints: dict[str, BaseGeometry | None],
    grid: CoverageDepthGrid,
    cfg: MeshBackboneStrategyConfig,
    goal_depth: int,
    per_link_cap: int,
) -> list[SiteCandidate]:
    """Sample the link strip, keep coverage-edge points biased toward the open anchor."""
    if result.complete or zone_ll.is_empty:
        return []

    in_zone = sites_in_zone(sites, zone_ll)
    bias = open_anchor_bias_for_link(
        link=result.link,
        leg=leg,
        zone_ll=zone_ll,
        anchors=anchors,
        sites=sites,
        footprints=footprints,
        endpoint_capture_m=float(cfg.endpoint_capture_m),
    )
    open_anchor = anchors[bias.anchor_key]
    label = result.link.name or f"{result.link.endpoints[0]}-{result.link.endpoints[1]}"
    leg_len = max(float(leg.length), 1.0)

    samples = sample_along_link(
        leg,
        spacing_m=float(cfg.sample_spacing_m),
        zone_ll=zone_ll,
    )
    scored: list[tuple[tuple[int, float, int], SiteCandidate]] = []
    for lat, lon in samples:
        if not _candidate_passes_edge_filter(
            grid=grid,
            lon=float(lon),
            lat=float(lat),
            goal_depth=goal_depth,
            open_anchor=open_anchor,
            endpoint_capture_m=float(cfg.endpoint_capture_m),
        ):
            continue
        pos = position_along_leg_m(leg, float(lon), float(lat))
        toward_open = pos if bias.toward_leg_start else (leg_len - pos)
        depth = grid.depth_at_point(float(lon), float(lat))
        edge_depth = max(0, int(goal_depth) - 1)
        gain = grid.point_marginal_gain_cells(float(lon), float(lat), goal_depth=goal_depth)
        sort_key = (
            0 if depth == edge_depth else 1,
            toward_open,
            -gain,
        )
        scored.append(
            (
                sort_key,
                SiteCandidate(
                    lat=float(lat),
                    lon=float(lon),
                    elev_m=None,
                    strategy=f"link:{label}",
                ),
            )
        )

    scored.sort(key=lambda item: item[0])
    return [c for _, c in scored[: max(1, int(per_link_cap))]]


def incomplete_link_results(ctx: SiteSuggestionContext) -> list[LinkCompletionResult]:
    mb = ctx.cfg.mesh_backbone
    if not mb.links:
        return []
    return [
        r
        for r in evaluate_mesh_backbone_completion(
            cfg=mb,
            eligible_ll=ctx.eligible_ll,
            sites=all_backbone_sites(ctx),
            footprints=footprints_for_backbone_sites(ctx.plan, ctx.session_footprints),
        )
        if not r.complete
    ]


def generate_mesh_backbone_candidates(
    ctx: SiteSuggestionContext,
    *,
    goal_depth: int,
) -> list[SiteCandidate]:
    """Fan out candidate samples across every incomplete configured link."""
    mb = ctx.cfg.mesh_backbone
    incomplete = incomplete_link_results(ctx)
    if not incomplete:
        return []

    anchors = anchors_from_config(mb)
    sites = all_backbone_sites(ctx)
    footprints = footprints_for_backbone_sites(ctx.plan, ctx.session_footprints)
    cap = max(1, int(mb.max_candidates_per_round))
    per_link = max(1, cap // len(incomplete))

    out: list[SiteCandidate] = []
    for result in incomplete:
        leg = resolve_link_leg(anchors, result.link)
        zone = link_search_zone(leg, buffer_m=float(mb.link_buffer_m), eligible_ll=ctx.eligible_ll)
        if zone is None:
            continue
        out.extend(
            candidates_for_incomplete_link(
                result=result,
                leg=leg,
                zone_ll=zone,
                anchors=anchors,
                sites=sites,
                footprints=footprints,
                grid=ctx.grid,
                cfg=mb,
                goal_depth=goal_depth,
                per_link_cap=per_link,
            )
        )

    return _dedupe_candidates(out)[:cap]
