"""Mesh-backbone link completion (depth at sites, hop chains)."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Mapping, Sequence

from pyproj import Transformer
from shapely.geometry import LineString, Point
from shapely.geometry.base import BaseGeometry

from peaky_finders.site_suggestions.depth_grid import CoverageDepthGrid
from peaky_finders.site_suggestions.mesh_backbone_geom import (
    AnchorPoint,
    anchors_from_config,
    link_search_zone,
    resolve_link_leg,
)
from peaky_finders.sites_job import MeshBackboneLinkEntry, MeshBackboneStrategyConfig

_TO_M = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)


@dataclass(frozen=True)
class BackboneSite:
    slug: str
    lat: float
    lon: float


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
    """Sites sorted by projection distance along ``leg`` (start anchor → end anchor)."""
    return sorted(sites, key=lambda s: (position_along_leg_m(leg, s.lon, s.lat), s.slug))


def sites_in_zone(sites: Sequence[BackboneSite], zone_ll: BaseGeometry) -> list[BackboneSite]:
    if zone_ll is None or zone_ll.is_empty:
        return []
    return [s for s in sites if zone_ll.intersects(Point(float(s.lon), float(s.lat)))]


def _distance_m(lon_a: float, lat_a: float, lon_b: float, lat_b: float) -> float:
    xa, ya = _TO_M.transform(float(lon_a), float(lat_a))
    xb, yb = _TO_M.transform(float(lon_b), float(lat_b))
    return float(Point(xa, ya).distance(Point(xb, yb)))


def anchor_capture_slugs(
    sites: Sequence[BackboneSite],
    anchor: AnchorPoint,
    *,
    capture_m: float,
) -> set[str]:
    cap = max(1.0, float(capture_m))
    return {
        s.slug
        for s in sites
        if _distance_m(s.lon, s.lat, anchor.lon, anchor.lat) <= cap
    }


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
    anchors: Mapping[str, AnchorPoint],
    sites: Sequence[BackboneSite],
    grid: CoverageDepthGrid,
    site_goal_depth: int,
    footprints: Mapping[str, BaseGeometry | None],
    endpoint_capture_m: float,
) -> LinkCompletionResult:
    """True when a depth-valid mutual-hop chain spans anchor A → anchor B inside the link zone."""
    a_key, b_key = link.endpoints
    start_anchor = anchors[a_key]
    end_anchor = anchors[b_key]
    label = link.name or f"{a_key}-{b_key}"
    need = int(site_goal_depth)

    in_zone = sites_in_zone(sites, zone_ll)
    if not in_zone:
        return LinkCompletionResult(
            link=link,
            complete=False,
            chain_slugs=(),
            detail=f"{label}: no sites in link zone",
        )

    depth_ok = {
        s.slug
        for s in in_zone
        if grid.depth_at_point(s.lon, s.lat) >= need
    }
    if not depth_ok:
        return LinkCompletionResult(
            link=link,
            complete=False,
            chain_slugs=(),
            detail=f"{label}: no sites with depth≥{need} in link zone",
        )

    start_slugs = anchor_capture_slugs(in_zone, start_anchor, capture_m=endpoint_capture_m)
    end_slugs = anchor_capture_slugs(in_zone, end_anchor, capture_m=endpoint_capture_m)
    if not start_slugs:
        return LinkCompletionResult(
            link=link,
            complete=False,
            chain_slugs=(),
            detail=f"{label}: no depth-valid site within {endpoint_capture_m:.0f} m of anchor {a_key!r}",
        )
    if not end_slugs:
        return LinkCompletionResult(
            link=link,
            complete=False,
            chain_slugs=(),
            detail=f"{label}: no depth-valid site within {endpoint_capture_m:.0f} m of anchor {b_key!r}",
        )

    zone_sites = [s for s in in_zone if s.slug in depth_ok]
    adjacency = hop_adjacency(zone_sites, footprints)
    chain = _shortest_hop_chain(
        start_slugs=start_slugs & depth_ok,
        end_slugs=end_slugs & depth_ok,
        valid_slugs=depth_ok,
        adjacency=adjacency,
    )
    if chain is None:
        return LinkCompletionResult(
            link=link,
            complete=False,
            chain_slugs=(),
            detail=f"{label}: no mutual-hop chain between anchors with depth≥{need}",
        )

    return LinkCompletionResult(
        link=link,
        complete=True,
        chain_slugs=tuple(chain),
        detail=f"{label}: chain {' → '.join(chain)} ({len(chain)} site(s))",
    )


def evaluate_mesh_backbone_completion(
    *,
    cfg: MeshBackboneStrategyConfig,
    eligible_ll: BaseGeometry,
    sites: Sequence[BackboneSite],
    grid: CoverageDepthGrid,
    footprints: Mapping[str, BaseGeometry | None],
) -> list[LinkCompletionResult]:
    """Evaluate every configured link; empty when ``cfg.links`` is empty."""
    anchors = anchors_from_config(cfg)
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
                anchors=anchors,
                sites=sites,
                grid=grid,
                site_goal_depth=int(cfg.site_goal_depth),
                footprints=footprints,
                endpoint_capture_m=float(cfg.endpoint_capture_m),
            )
        )
    return results


def all_links_complete(results: Sequence[LinkCompletionResult]) -> bool:
    return bool(results) and all(r.complete for r in results)
