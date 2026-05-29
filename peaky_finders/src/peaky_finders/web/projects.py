"""Discover Peaky projects from ``projects/*/config.yaml``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from shapely.geometry import box
from shapely.geometry.base import BaseGeometry

from peaky_finders.bundle_build import load_composite_aoi_polygon
from peaky_finders.http_pool import HttpPool
from peaky_finders.site_metadata_enrich import enrich_all_preset_sites, resolve_site_metadata
from peaky_finders.site_suggestions.rf_link import rf_mutual_link_slug_pairs_from_site
from peaky_finders.sites_job import SiteType, load_preset, peaky_projects_dir, resolved_preset_bundle_data_dir
from peaky_finders.web.site_preset_io import (
    delete_site_from_preset,
    get_site_from_preset,
    update_site_in_preset,
)

_GEOCODE_AOI_CACHE: dict[str, tuple[float, list[float] | None, BaseGeometry | None]] = {}


def _iter_project_config_paths() -> list[tuple[str, Path]]:
    """Return ``(slug, config_path)`` for each valid project preset."""
    root = peaky_projects_dir()
    out: list[tuple[str, Path]] = []
    if not root.is_dir():
        return out
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        cfg = entry / "config.yaml"
        if not cfg.is_file():
            yml = entry / "config.yml"
            cfg = yml if yml.is_file() else cfg
        if not cfg.is_file():
            continue
        out.append((entry.name, cfg))
    return out


def enrich_all_project_sites(
    *,
    allow_network_plss: bool = True,
    http_pool: HttpPool | None = None,
    log_fp: Any = None,
) -> tuple[int, int, int]:
    """Backfill missing PLSS / MLRS / elevation for every project preset.

    Returns ``(projects_processed, network_plss_lookups, sites_updated)``.
    """
    configs = _iter_project_config_paths()
    prefix = "boot site enrich:"
    print(f"{prefix} start: {len(configs)} project(s)", flush=True)

    projects_processed = 0
    total_network = 0
    total_updated = 0

    for index, (slug, cfg) in enumerate(configs, start=1):
        print(f"{prefix} [{index}/{len(configs)}] {slug}", flush=True)
        try:
            network_count, updated = enrich_all_preset_sites(
                cfg,
                allow_network_plss=allow_network_plss,
                http_pool=http_pool,
                log_fp=log_fp,
            )
        except Exception as exc:
            print(f"{prefix} [{index}/{len(configs)}] {slug}: skipped ({exc})", flush=True)
            continue
        projects_processed += 1
        total_network += network_count
        total_updated += updated
        if updated or network_count:
            print(
                f"{prefix} [{index}/{len(configs)}] {slug}: "
                f"{updated} site(s) updated, {network_count} PLSS lookup(s)",
                flush=True,
            )

    print(
        f"{prefix} done: {projects_processed}/{len(configs)} project(s), "
        f"{total_updated} site(s) updated, {total_network} PLSS lookup(s)",
        flush=True,
    )
    return projects_processed, total_network, total_updated


def list_projects() -> list[dict[str, Any]]:
    root = peaky_projects_dir()
    out: list[dict[str, Any]] = []
    if not root.is_dir():
        return out
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        cfg = entry / "config.yaml"
        if not cfg.is_file():
            yml = entry / "config.yml"
            cfg = yml if yml.is_file() else cfg
        if not cfg.is_file():
            continue
        try:
            preset = load_preset(cfg)
        except Exception:
            continue
        strategy = "land-grab"
        goal_count = sum(1 for e in preset.sites.values() if e.type == SiteType.GOAL)
        if preset.bundle and preset.bundle.site_suggestions:
            ss = preset.bundle.site_suggestions
            strategy = ss.strategy.value
            if strategy == "mesh-grow-ai":
                goal_count += len(ss.mesh_grow_ai.goals)
            elif strategy == "mesh-backbone":
                goal_count += len(ss.mesh_backbone.goals)
        out.append(
            {
                "slug": entry.name,
                "path": str(cfg.resolve()),
                "strategy": strategy,
                "goals": goal_count,
                "site_count": len(preset.sites),
            }
        )
    return out


def project_context(slug: str) -> dict[str, Any]:
    root = peaky_projects_dir()
    cfg = root / slug / "config.yaml"
    if not cfg.is_file():
        raise FileNotFoundError(f"project not found: {slug!r}")
    enrich_all_preset_sites(cfg, allow_network_plss=True)
    preset = load_preset(cfg)
    sites = [
        {
            "slug": s,
            "name": e.name.strip() or s,
            "lat": e.lat,
            "lon": e.lon,
            "type": e.type.value,
        }
        for s, e in sorted(preset.sites.items())
    ]
    goals: list[dict[str, Any]] = [
        {
            "key": slug,
            "label": e.name.strip() or slug,
            "lat": e.lat,
            "lon": e.lon,
        }
        for slug, e in sorted(preset.sites.items())
        if e.type == SiteType.GOAL
    ]
    if preset.bundle and preset.bundle.site_suggestions:
        ss = preset.bundle.site_suggestions
        if ss.strategy.value == "mesh-grow-ai":
            gcfg = ss.mesh_grow_ai.goals
        elif ss.strategy.value == "mesh-backbone":
            gcfg = ss.mesh_backbone.goals
        else:
            gcfg = {}
        for key, ent in gcfg.items():
            goals.append({"key": key, "label": key, "lat": ent.lat, "lon": ent.lon})
    bbox = None
    if sites:
        lats = [s["lat"] for s in sites]
        lons = [s["lon"] for s in sites]
        pad = 0.25
        bbox = [min(lons) - pad, min(lats) - pad, max(lons) + pad, max(lats) + pad]
    return {"slug": slug, "sites": sites, "goals": goals, "bbox": bbox}


def _viewbox_from_bounds(west: float, south: float, east: float, north: float, *, pad: float) -> list[float]:
    return [west - pad, south - pad, east + pad, north + pad]


def project_geocode_aoi(slug: str) -> tuple[list[float] | None, BaseGeometry | None]:
    """WGS-84 viewbox and AOI polygon for geocode bias (bundle AOI, else site extent)."""
    root = peaky_projects_dir()
    cfg = root / slug / "config.yaml"
    if not cfg.is_file():
        return None, None

    mtime = cfg.stat().st_mtime_ns
    cached = _GEOCODE_AOI_CACHE.get(slug)
    if cached and cached[0] == mtime:
        return cached[1], cached[2]

    viewbox: list[float] | None = None
    aoi: BaseGeometry | None = None

    try:
        preset = load_preset(cfg)
    except Exception:
        _GEOCODE_AOI_CACHE[slug] = (mtime, None, None)
        return None, None

    if preset.bundle and preset.bundle.aoi:
        try:
            data_dir = resolved_preset_bundle_data_dir(preset_path=cfg, preset=preset)
            poly = load_composite_aoi_polygon(preset.bundle, data_dir)
            if not poly.is_empty:
                aoi = poly
                west, south, east, north = poly.bounds
                viewbox = _viewbox_from_bounds(west, south, east, north, pad=0.05)
        except Exception:
            pass

    if aoi is None:
        ctx = project_context(slug)
        raw_bbox = ctx.get("bbox")
        if isinstance(raw_bbox, list) and len(raw_bbox) == 4:
            west, south, east, north = (float(raw_bbox[i]) for i in range(4))
            if east > west and north > south:
                viewbox = [west, south, east, north]
                aoi = box(west, south, east, north)

    _GEOCODE_AOI_CACHE[slug] = (mtime, viewbox, aoi)
    return viewbox, aoi


def clear_project_geocode_aoi_cache() -> None:
    _GEOCODE_AOI_CACHE.clear()


def _project_config_path(slug: str) -> Path:
    cfg = peaky_projects_dir() / slug / "config.yaml"
    if not cfg.is_file():
        raise FileNotFoundError(f"project not found: {slug!r}")
    return cfg


def _sees_mutual_and_pending(
    site_slug: str,
    sees: list[str],
    sees_by_slug: dict[str, list[str]],
) -> tuple[list[str], list[str]]:
    mutual: list[str] = []
    pending: list[str] = []
    for target in sees:
        if target == site_slug:
            continue
        reciprocal = {str(t).strip() for t in sees_by_slug.get(target, ())}
        if site_slug in reciprocal:
            mutual.append(target)
        else:
            pending.append(target)
    return sorted(mutual), sorted(pending)


def _rf_peers_for_site(preset, site_slug: str) -> list[str]:
    site = preset.sites.get(site_slug)
    if site is None or not site.participates_in_rf:
        return []
    peers: set[str] = set()
    for slug_a, slug_b in rf_mutual_link_slug_pairs_from_site(
        preset,
        from_slug=site_slug,
        from_lat=float(site.lat),
        from_lon=float(site.lon),
    ):
        peers.add(slug_b if slug_a == site_slug else slug_a)
    return sorted(peers)


def project_site_detail(slug: str, site_slug: str) -> dict[str, Any]:
    """Full site record plus RF / ``sees`` helper lists."""
    cfg = _project_config_path(slug)
    base = get_site_from_preset(cfg, site_slug)
    preset = load_preset(cfg)
    sees_by_slug = {s: list(e.sees) for s, e in preset.sites.items()}
    mutual, pending = _sees_mutual_and_pending(site_slug, base["sees"], sees_by_slug)
    peer_slugs = sorted(
        s for s, e in preset.sites.items() if s != site_slug and e.participates_in_rf
    )
    return {
        **base,
        "rf_peers": _rf_peers_for_site(preset, site_slug),
        "sees_mutual": mutual,
        "sees_pending": pending,
        "peer_slugs": peer_slugs,
    }


def patch_project_site(slug: str, site_slug: str, body: dict[str, Any]) -> dict[str, Any]:
    """Update preset site; return refreshed detail."""
    cfg = _project_config_path(slug)
    patch = dict(body)
    new_slug_raw = patch.pop("new_slug", None)
    new_slug: str | None = None
    if new_slug_raw is not None:
        new_slug = str(new_slug_raw).strip() or None
    loc_changed = "lat" in patch or "lon" in patch
    effective = update_site_in_preset(cfg, site_slug, patch, new_slug=new_slug)
    if loc_changed:
        resolve_site_metadata(cfg, site_slug=effective, force=True, allow_network_plss=True)
    return project_site_detail(slug, effective)


def remove_project_site(slug: str, site_slug: str) -> None:
    """Delete preset site."""
    cfg = _project_config_path(slug)
    delete_site_from_preset(cfg, site_slug)
