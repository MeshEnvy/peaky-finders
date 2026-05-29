"""Mutual site–site link GeoJSON for the web map."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from peaky_finders import kml_bundle
from peaky_finders.preset_mapping import preset_to_request
from peaky_finders.sites_job import (
    Preset,
    load_preset,
    peaky_projects_dir,
    resolved_viewshed_dir,
)
from peaky_finders.splat_polygonize import SPLAT_GPKG_NAME
from peaky_finders.viewshed_links import site_links_geojson
from peaky_finders.viewshed_workspace import resolved_viewshed_workdir, viewshed_workspace_digest


def _site_overlays(preset: Preset) -> list[kml_bundle.AggregateSiteOverlay]:
    tx_antenna_agl_m = max(1.0, float(preset.simulation.transmitter["height_m"]))
    overlays: list[kml_bundle.AggregateSiteOverlay] = []
    for site_slug, site in sorted(preset.sites.items()):
        pin_lat, pin_lon = float(site.lat), float(site.lon)
        overlays.append(
            kml_bundle.AggregateSiteOverlay(
                slug=site_slug,
                folder_name=site.name.strip() or site_slug,
                overlay_href=f"sites/viewsheds/raster/{site_slug}/splat.png",
                north=pin_lat,
                south=pin_lat,
                east=pin_lon,
                west=pin_lon,
                rotation=0.0,
                center_lat=pin_lat,
                center_lon=pin_lon,
                antenna_height_agl_m=tx_antenna_agl_m,
                pin_description="",
            ),
        )
    return overlays


def _coverage_gpkg_by_slug(
    *,
    preset: Preset,
    bundle_cache_root: Path,
) -> dict[str, Path]:
    viewshed_root = resolved_viewshed_dir(bundle_cache_root)
    out: dict[str, Path] = {}
    for site_slug, site in preset.sites.items():
        vd = viewshed_workspace_digest(request=preset_to_request(preset, float(site.lat), float(site.lon)))
        data_dir = resolved_viewshed_workdir(digest=vd, viewshed_root=viewshed_root)
        gp = data_dir / SPLAT_GPKG_NAME
        if gp.is_file():
            out[site_slug] = gp
    return out


def project_mesh_links_geojson(slug: str) -> dict[str, Any]:
    """Return mutual site link LineStrings as GeoJSON for ``projects/<slug>``."""
    cfg = peaky_projects_dir() / slug / "config.yaml"
    if not cfg.is_file():
        raise FileNotFoundError(f"project not found: {slug!r}")

    preset = load_preset(cfg)
    from peaky_finders.preset_overlays import standard_bundle_roots

    job_path_r, bundle_cache_root, _ = standard_bundle_roots(cfg, preset)
    _ = job_path_r

    overlays = _site_overlays(preset)
    coverage = _coverage_gpkg_by_slug(preset=preset, bundle_cache_root=bundle_cache_root)
    sees_by_slug = {site_slug: entry.sees for site_slug, entry in preset.sites.items()}
    return site_links_geojson(
        coverage_gpkg_by_slug=coverage,
        sites=overlays,
        sees_by_slug=sees_by_slug,
    )
