"""Coordinate placement prefetch for ``peaky serve`` (PLSS + site links)."""

from __future__ import annotations

from pathlib import Path

from peaky_finders.serve.links import ServeLinksError, load_coords_site_links
from peaky_finders.serve.plss import ServePlssError, ensure_plss_for_coords
from peaky_finders.core.preset import load_preset_sites


class ServeSitePrefetchError(Exception):
    """Site placement prefetch failed."""


def load_site_placement_prefetch(
    project_dir: Path,
    lat: float,
    lon: float,
    *,
    exclude_site_slug: str | None = None,
    verbose: bool = False,
) -> dict[str, object]:
    """Return PLSS and linked peers for draft coordinates."""
    try:
        plss_result = ensure_plss_for_coords(project_dir, lat, lon, verbose=verbose)
    except ServePlssError as e:
        raise ServeSitePrefetchError(str(e)) from e

    try:
        sites = load_preset_sites(project_dir / "config.yaml")
    except ValueError as e:
        raise ServeSitePrefetchError(f"invalid preset: {e}") from e

    try:
        links = load_coords_site_links(
            project_dir,
            lat,
            lon,
            sites,
            exclude_site_slug=exclude_site_slug,
            verbose=verbose,
        )
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
        "plss": plss_result["plss"],
        "links": links,
        "links_geojson": {"type": "FeatureCollection", "features": link_features},
    }
