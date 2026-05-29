"""Mutual site–site link GeoJSON for the web map."""

from __future__ import annotations

from typing import Any

from peaky_finders import kml_bundle
from peaky_finders.sites_job import (
    Preset,
    load_preset,
    peaky_projects_dir,
)
from peaky_finders.viewshed_links import site_links_geojson


def _site_overlays(preset: Preset) -> list[kml_bundle.AggregateSiteOverlay]:
    tx_antenna_agl_m = max(1.0, float(preset.simulation.transmitter["height_m"]))
    overlays: list[kml_bundle.AggregateSiteOverlay] = []
    for site_slug, site in sorted(preset.sites.items()):
        if not site.participates_in_rf:
            continue
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


def project_mesh_links_geojson(slug: str) -> dict[str, Any]:
    """Return mutual site link LineStrings as GeoJSON for ``projects/<slug>``."""
    cfg = peaky_projects_dir() / slug / "config.yaml"
    if not cfg.is_file():
        raise FileNotFoundError(f"project not found: {slug!r}")

    preset = load_preset(cfg)
    overlays = _site_overlays(preset)
    sees_by_slug = {
        site_slug: entry.sees
        for site_slug, entry in preset.sites.items()
        if entry.participates_in_rf
    }
    return site_links_geojson(
        preset=preset,
        sites=overlays,
        sees_by_slug=sees_by_slug,
    )
