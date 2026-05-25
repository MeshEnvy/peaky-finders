"""Mesh-backbone link completion (mutual-hop connectivity)."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from pyproj import Transformer
from shapely import make_valid
from shapely.geometry import LineString, Point
from shapely.geometry.base import BaseGeometry

from peaky_finders.coverage_footprint import read_coverage_footprint
from peaky_finders.site_suggestions.context import BackboneSite, SiteSuggestionContext
from peaky_finders.site_suggestions.mesh_backbone_geom import (
    AnchorPoint,
    anchors_from_preset,
    link_search_zone,
    resolve_link_leg,
)
from peaky_finders.sites_job import MeshBackboneLinkEntry, MeshBackboneStrategyConfig, SiteSuggestionStrategy

_TO_M = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)


@dataclass(frozen=True)
class LinkCompletionResult:
    link: MeshBackboneLinkEntry
    complete: bool
    chain_slugs: tuple[str, ...]
    detail: str


def backbone_sites_from_preset(sites: Mapping[str, object]) -> list[BackboneSite]:
    """Build backbone nodes from ``Preset.sites`` entries (``lat``/``lon`` properties)."""
    out: list[BackboneSite] = []
    for slug, ent in sites.items():
        out.append(BackboneSite(slug=str(slug), lat=float(ent.lat), lon=float(ent.lon)))
    return out


def position_along_leg_m(leg: LineString, lon: float, lat: float) -> float:
    """Distance in meters from the leg start to the projected point."""
    if leg.is_empty:
        return 0.0
    x, y = _TO_M.transform(float(lon), float(lat))
    return float(leg.project(Point(x, y)))


def order_sites_along_leg(leg: LineString, sites: Sequence[BackboneSite]) -> list[BackboneSite]:
    """Sites sorted by projection distance along ``leg`` (start endpoint → end endpoint)."""
    return sorted(sites, key=lambda s: (position_along_leg_m(leg, s.lon, s.lat), s.slug))


def sites_in_zone(sites: Sequence[BackboneSite], zone_ll: BaseGeometry) -> list[BackboneSite]:
    if zone_ll is None or zone_ll.is_empty:
        return []
    return [s for s in sites if zone_ll.intersects(Point(float(s.lon), float(s.lat)))]


def _distance_m(lon_a: float, lat_a: float, lon_b: float, lat_b: float) -> float:
    xa, ya = _TO_M.transform(float(lon_a), float(lat_a))
    xb, yb = _TO_M.transform(float(lon_b), float(lat_b))
    return float(Point(xa, ya).distance(Point(xb, yb)))


def endpoint_slugs_in_zone(
    link: MeshBackboneLinkEntry,
    zone_slugs: set[str],
) -> tuple[set[str], set[str]]:
    """Endpoint site slugs that lie inside the link search zone."""
    a_slug, b_slug = link.endpoints
    return (
        {a_slug} if a_slug in zone_slugs else set(),
        {b_slug} if b_slug in zone_slugs else set(),
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


def _shortest_hop_chain(
    *,
    start_slugs: set[str],
    end_slugs: set[str],
    valid_slugs: set[str],
    adjacency: Mapping[str, set[str]],
) -> list[str] | None:
    if not start_slugs or not end_slugs:
        return None
    best: list[str] | None = None
    for start in sorted(start_slugs):
        if start not in valid_slugs:
            continue
        queue: deque[tuple[str, list[str]]] = deque([(start, [start])])
        visited = {start}
        while queue:
            node, path = queue.popleft()
            if node in end_slugs:
                if best is None or len(path) < len(best):
                    best = path
                break
            for nb in sorted(adjacency.get(node, ())):
                if nb not in valid_slugs or nb in visited:
                    continue
                visited.add(nb)
                queue.append((nb, [*path, nb]))
    return best


def evaluate_link_completion(
    *,
    link: MeshBackboneLinkEntry,
    leg: LineString,
    zone_ll: BaseGeometry,
    sites: Sequence[BackboneSite],
    footprints: Mapping[str, BaseGeometry | None],
) -> LinkCompletionResult:
    """True when a mutual-hop path connects endpoint site A to endpoint site B (direct link counts)."""
    a_slug, b_slug = link.endpoints
    label = link.name or f"{a_slug}-{b_slug}"

    in_zone = sites_in_zone(sites, zone_ll)
    if not in_zone:
        return LinkCompletionResult(
            link=link,
            complete=False,
            chain_slugs=(),
            detail=f"{label}: no sites in link zone",
        )

    zone_slugs = {s.slug for s in in_zone}
    start_slugs, end_slugs = endpoint_slugs_in_zone(link, zone_slugs)
    if not start_slugs:
        return LinkCompletionResult(
            link=link,
            complete=False,
            chain_slugs=(),
            detail=f"{label}: endpoint site {a_slug!r} not in link zone",
        )
    if not end_slugs:
        return LinkCompletionResult(
            link=link,
            complete=False,
            chain_slugs=(),
            detail=f"{label}: endpoint site {b_slug!r} not in link zone",
        )

    adjacency = hop_adjacency(in_zone, footprints)
    chain = _shortest_hop_chain(
        start_slugs=start_slugs,
        end_slugs=end_slugs,
        valid_slugs=zone_slugs,
        adjacency=adjacency,
    )
    if chain is None:
        return LinkCompletionResult(
            link=link,
            complete=False,
            chain_slugs=(),
            detail=f"{label}: no mutual-hop path between endpoint sites",
        )

    hop_label = "direct" if len(chain) == 2 else f"{len(chain)} hops"
    return LinkCompletionResult(
        link=link,
        complete=True,
        chain_slugs=tuple(chain),
        detail=f"{label}: connected {' → '.join(chain)} ({hop_label})",
    )


def evaluate_mesh_backbone_completion(
    *,
    cfg: MeshBackboneStrategyConfig,
    preset_sites: Mapping[str, object],
    eligible_ll: BaseGeometry,
    sites: Sequence[BackboneSite],
    footprints: Mapping[str, BaseGeometry | None],
) -> list[LinkCompletionResult]:
    """Evaluate every configured link; empty when ``cfg.links`` is empty."""
    anchors = anchors_from_preset(preset_sites, cfg)
    results: list[LinkCompletionResult] = []
    for link in cfg.links:
        leg = resolve_link_leg(anchors, link)
        zone = link_search_zone(leg, buffer_m=cfg.link_buffer_m, eligible_ll=eligible_ll)
        if zone is None:
            results.append(
                LinkCompletionResult(
                    link=link,
                    complete=False,
                    chain_slugs=(),
                    detail=f"{link.name or link.endpoints}: empty link zone",
                )
            )
            continue
        results.append(
            evaluate_link_completion(
                link=link,
                leg=leg,
                zone_ll=zone,
                sites=sites,
                footprints=footprints,
            )
        )
    return results


def all_links_complete(results: Sequence[LinkCompletionResult]) -> bool:
    return bool(results) and all(r.complete for r in results)


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
            fp = read_coverage_footprint(Path(ws.coverage_gpkg))
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


def mesh_backbone_planning_complete(ctx: SiteSuggestionContext) -> bool:
    """True when every configured mesh-backbone link has a hop path between endpoint sites."""
    if ctx.cfg.strategy != SiteSuggestionStrategy.MESH_BACKBONE:
        return False
    mb = ctx.cfg.mesh_backbone
    if not mb.links:
        return True
    results = evaluate_mesh_backbone_completion(
        cfg=mb,
        preset_sites=ctx.preset.sites,
        eligible_ll=ctx.eligible_ll,
        sites=all_backbone_sites(ctx),
        footprints=footprints_for_backbone_sites(ctx.plan, ctx.session_footprints),
    )
    return all_links_complete(results)
