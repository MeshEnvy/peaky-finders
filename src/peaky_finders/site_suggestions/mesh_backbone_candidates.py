"""Mesh-grow candidate generation (frontier extension toward goals)."""

from __future__ import annotations

from pyproj import Transformer
from shapely.geometry import LineString, Point

from peaky_finders.site_suggestions.candidates import SiteCandidate, _dedupe_candidates
from peaky_finders.site_suggestions.context import SiteSuggestionContext
from peaky_finders.site_suggestions.mesh_backbone_completion import all_backbone_sites
from peaky_finders.site_suggestions.mesh_backbone_geom import GoalPoint
from peaky_finders.site_suggestions.mesh_grow import active_attractor_goal
from peaky_finders.sites_job import MeshBackboneStrategyConfig

_TO_M = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
_FROM_M = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)


def _cold_start_samples(
    ctx: SiteSuggestionContext,
    *,
    goal: GoalPoint,
    cfg: MeshBackboneStrategyConfig,
    cap: int,
) -> list[SiteCandidate]:
    """Sample along seed→goal line on cells already in composite coverage."""
    sites = all_backbone_sites(ctx)
    if not sites:
        return []

    best = min(
        sites,
        key=lambda s: ctx.grid.min_distance_coverage_to_point_m(
            float(s.lon), float(s.lat), min_depth=1
        ),
    )
    gx, gy = _TO_M.transform(float(goal.lon), float(goal.lat))
    sx, sy = _TO_M.transform(float(best.lon), float(best.lat))
    leg = LineString([(sx, sy), (gx, gy)])
    if leg.is_empty or leg.length <= 0:
        return []

    spacing = max(50.0, float(cfg.frontier_sample_spacing_m))
    n = max(1, int(leg.length / spacing))
    label = f"goal:{goal.key}"
    out: list[SiteCandidate] = []
    for i in range(1, n + 1):
        dist = min(float(leg.length), i * spacing)
        pt = leg.interpolate(dist)
        lon, lat = _FROM_M.transform(pt.x, pt.y)
        if not ctx.eligible_ll.intersects(Point(float(lon), float(lat))):
            continue
        if ctx.grid.depth_at_point(float(lon), float(lat)) < 1:
            continue
        out.append(
            SiteCandidate(
                lat=float(lat),
                lon=float(lon),
                elev_m=None,
                strategy=label,
            )
        )
    return out[:cap]


def generate_mesh_grow_candidates(ctx: SiteSuggestionContext) -> list[SiteCandidate]:
    """Frontier samples on composite coverage, biased toward the active attractor goal."""
    mb = ctx.cfg.mesh_backbone
    if not mb.goals:
        return []

    attractor = active_attractor_goal(ctx)
    if attractor is None:
        return []

    cap = max(1, int(mb.max_candidates_per_round))
    samples = ctx.grid.frontier_sample_points_toward_goal(
        goal_lon=float(attractor.lon),
        goal_lat=float(attractor.lat),
        eligible_ll=ctx.eligible_ll,
        min_depth=1,
        spacing_m=float(mb.frontier_sample_spacing_m),
        max_points=cap,
    )

    label = f"goal:{attractor.key}"
    cands = [
        SiteCandidate(lat=lat, lon=lon, elev_m=None, strategy=label) for lat, lon in samples
    ]
    if not cands:
        cands = _cold_start_samples(ctx, goal=attractor, cfg=mb, cap=cap)

    return _dedupe_candidates(cands)[:cap]


generate_mesh_backbone_candidates = generate_mesh_grow_candidates
