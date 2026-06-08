"""Coordinate placement prefetch for ``peaky serve`` (PLSS/MLRS + site links)."""

from __future__ import annotations

from pathlib import Path

from peaky_finders.serve_links import ServeLinksError, load_coords_site_links
from peaky_finders.serve_plss_mlrs import ServePlssMlrsError, ensure_plss_mlrs_for_coords
from peaky_finders.sites_job import load_preset_sites


class ServeSitePrefetchError(Exception):
    """Site placement prefetch failed."""


def load_site_placement_prefetch(
    project_dir: Path,
    lat: float,
    lon: float,
    *,
    verbose: bool = False,
) -> dict[str, object]:
    """Return PLSS, MLRS, and linked peers for draft coordinates."""
    try:
        plss_mlrs = ensure_plss_mlrs_for_coords(project_dir, lat, lon, verbose=verbose)
    except ServePlssMlrsError as e:
        raise ServeSitePrefetchError(str(e)) from e

    try:
        sites = load_preset_sites(project_dir / "config.yaml")
    except ValueError as e:
        raise ServeSitePrefetchError(f"invalid preset: {e}") from e

    try:
        links = load_coords_site_links(project_dir, lat, lon, sites, verbose=verbose)
    except ServeLinksError as e:
        raise ServeSitePrefetchError(str(e)) from e

    link_features: list[dict[str, object]] = []
    for row in links:
        slug = str(row["slug"])
        site = sites.get(slug)
        if site is None:
            continue
        link_features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        [float(lon), float(lat)],
                        [float(site.lon), float(site.lat)],
                    ],
                },
                "properties": {
                    "slug": slug,
                    "manual": bool(row.get("manual")),
                    "distance_km": row.get("distance_km"),
                },
            }
        )

    return {
        "lat": lat,
        "lon": lon,
        "plss": plss_mlrs["plss"],
        "mlrs": plss_mlrs["mlrs"],
        "links": links,
        "links_geojson": {"type": "FeatureCollection", "features": link_features},
    }
