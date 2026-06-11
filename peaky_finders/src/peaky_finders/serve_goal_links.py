"""On-demand goal↔repeater link checks for ``peaky serve``."""

from __future__ import annotations

import threading
from itertools import product
from pathlib import Path
from typing import Mapping

from pydantic import ValidationError
from shapely.geometry import Point

from peaky_finders.coverage_footprint import read_coverage_footprint
from peaky_finders.serve_links import ServeLinksError, _haversine_m
from peaky_finders.site_suggestions.rf_link import (
    ensure_dem_for_points,
    max_hop_range_m,
    mutual_hop_batch,
    rf_json_for_preset,
    splatter_session,
)
from peaky_finders.sites_job import (
    Preset,
    SiteEntry,
    load_preset_for_coverage,
    preset_repeater_sites,
    resolved_bundle_dir,
    resolved_viewshed_dir,
)
from peaky_finders.splat_polygonize import SPLAT_GPKG_NAME
from peaky_finders.viewshed_workspace import resolved_viewshed_workdir_for_coords

_links_eval_lock = threading.Lock()


def _load_links_preset(project_dir: Path) -> Preset:
    try:
        return load_preset_for_coverage(project_dir / "config.yaml")
    except (ValueError, ValidationError) as e:
        raise ServeLinksError(f"invalid preset: {e}") from e


def _site_footprint(project_dir: Path, slug: str, *, preset: Preset) -> object | None:
    site = preset.sites.get(slug)
    if site is None:
        return None
    preset_path = project_dir / "config.yaml"
    viewshed_root = resolved_viewshed_dir(resolved_bundle_dir(preset_path=preset_path))
    workdir = resolved_viewshed_workdir_for_coords(
        preset=preset,
        viewshed_root=viewshed_root,
        lat=float(site.lat),
        lon=float(site.lon),
    )
    return read_coverage_footprint(workdir / SPLAT_GPKG_NAME)


def _footprint_covers_goal(footprint, *, lat: float, lon: float) -> bool:
    if footprint is None or getattr(footprint, "is_empty", True):
        return False
    return bool(footprint.covers(Point(float(lon), float(lat))))


def _goal_link_record(
    *,
    goal_slug: str,
    site_slug: str,
    linked: bool,
    captured: bool,
) -> dict[str, object]:
    return {
        "goal": goal_slug,
        "site": site_slug,
        "linked": linked,
        "captured": captured,
    }


def _goal_line_feature(
    *,
    goal_slug: str,
    site_slug: str,
    goal: SiteEntry,
    site: SiteEntry,
    captured: bool,
) -> dict[str, object]:
    return {
        "type": "Feature",
        "geometry": {
            "type": "LineString",
            "coordinates": [
                [float(goal.lon), float(goal.lat)],
                [float(site.lon), float(site.lat)],
            ],
        },
        "properties": {
            "goal": goal_slug,
            "site": site_slug,
            "captured": captured,
        },
    }


def load_project_goal_links(
    project_dir: Path,
    goals: Mapping[str, SiteEntry],
    sites: Mapping[str, SiteEntry],
    *,
    verbose: bool = False,
) -> dict[str, object]:
    """Evaluate goal↔repeater pairs and return linked pairs plus GeoJSON line features."""
    preset = _load_links_preset(project_dir)

    slug_list_goals = sorted(goals.keys())
    slug_list_sites = sorted(sites.keys())
    records: list[dict[str, object]] = []
    features: list[dict[str, object]] = []
    rf_pairs: list[tuple[str, str, float, float, float, float]] = []

    for goal_slug, site_slug in product(slug_list_goals, slug_list_sites):
        goal = goals[goal_slug]
        site = sites[site_slug]
        lat_g, lon_g = float(goal.lat), float(goal.lon)
        lat_s, lon_s = float(site.lat), float(site.lon)
        if _haversine_m(lat_g, lon_g, lat_s, lon_s) > max_hop_range_m(preset):
            continue
        rf_pairs.append((goal_slug, site_slug, lat_g, lon_g, lat_s, lon_s))

    if not rf_pairs:
        return {
            "links": records,
            "geojson": {"type": "FeatureCollection", "features": features},
        }

    rf_json = rf_json_for_preset(preset)
    max_hop_m = max_hop_range_m(preset)
    points = [(lat_g, lon_g) for _, _, lat_g, lon_g, _, _ in rf_pairs]
    points.extend((lat_s, lon_s) for _, _, _, _, lat_s, lon_s in rf_pairs)
    coord_pairs = [(lat_g, lon_g, lat_s, lon_s) for _, _, lat_g, lon_g, lat_s, lon_s in rf_pairs]

    footprints: dict[str, object | None] = {}
    with _links_eval_lock:
        session = splatter_session(verbose=verbose)
        if verbose:
            print(
                f"serve goal links: {len(rf_pairs)} RF pair(s), "
                f"{len(slug_list_goals)} goal(s), {len(slug_list_sites)} site(s)",
                flush=True,
            )
        ensure_dem_for_points(
            session,
            points,
            buffer_m=max_hop_m * 0.05 + 5000.0,
        )
        viable = mutual_hop_batch(session, coord_pairs, rf_json=rf_json)

    if len(viable) != len(rf_pairs):
        raise ServeLinksError(
            f"RF batch length mismatch: {len(viable)} results for {len(rf_pairs)} pair(s)"
        )

    for (goal_slug, site_slug, lat_g, lon_g, lat_s, lon_s), ok in zip(rf_pairs, viable):
        if not ok:
            continue
        if site_slug not in footprints:
            footprints[site_slug] = _site_footprint(project_dir, site_slug, preset=preset)
        fp = footprints[site_slug]
        captured = _footprint_covers_goal(fp, lat=lat_g, lon=lon_g)
        records.append(
            _goal_link_record(
                goal_slug=goal_slug,
                site_slug=site_slug,
                linked=True,
                captured=captured,
            )
        )
        features.append(
            _goal_line_feature(
                goal_slug=goal_slug,
                site_slug=site_slug,
                goal=goals[goal_slug],
                site=sites[site_slug],
                captured=captured,
            )
        )

    records.sort(key=lambda row: (str(row["goal"]), str(row["site"])))
    return {
        "links": records,
        "geojson": {"type": "FeatureCollection", "features": features},
    }


def evaluate_goal_site_prefetch_links(
    project_dir: Path,
    *,
    lat: float,
    lon: float,
    sites: Mapping[str, SiteEntry],
    verbose: bool = False,
) -> list[dict[str, object]]:
    """RF-viable repeater links for a draft goal placement."""
    preset = _load_links_preset(project_dir)

    rf_json = rf_json_for_preset(preset)
    max_hop_m = max_hop_range_m(preset)
    pairs: list[tuple[float, float, float, float]] = []
    site_slugs: list[str] = []
    for slug, site in sorted(preset_repeater_sites(sites).items()):
        lat_s, lon_s = float(site.lat), float(site.lon)
        if _haversine_m(lat, lon, lat_s, lon_s) > max_hop_m:
            continue
        pairs.append((lat, lon, lat_s, lon_s))
        site_slugs.append(slug)

    if not pairs:
        return []

    with _links_eval_lock:
        session = splatter_session(verbose=verbose)
        ensure_dem_for_points(
            session,
            [(lat, lon)] + [(float(s.lat), float(s.lon)) for s in sites.values()],
            buffer_m=max_hop_m * 0.05 + 5000.0,
        )
        viable = mutual_hop_batch(session, pairs, rf_json=rf_json)

    out: list[dict[str, object]] = []
    for slug, ok in zip(site_slugs, viable):
        if ok:
            out.append({"site": slug, "linked": True})
    return out
