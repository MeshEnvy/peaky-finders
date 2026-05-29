"""Viewshed splat.png raster metadata for the web map."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from peaky_finders.coverage_png import bbox_rotation_normalized, fraction_to_lat_lon
from peaky_finders import kml_bundle
from peaky_finders.kml_bundle import load_bounds_from_manifest, parse_lat_lon_box
from peaky_finders.sites_job import Preset, load_preset, peaky_projects_dir, resolved_bundle_dir, resolved_viewshed_dir
from peaky_finders.viewshed_workspace import resolved_viewshed_workdir_for_coords

POINT_COORD_DECIMALS = 6


def normalize_point_coords(lat: float, lon: float) -> tuple[float, float]:
    """Round WGS-84 coords for point viewshed cache keys and tile URLs."""
    return (round(float(lat), POINT_COORD_DECIMALS), round(float(lon), POINT_COORD_DECIMALS))


def preset_path_for_project(project_slug: str) -> Path:
    cfg = peaky_projects_dir() / project_slug / "config.yaml"
    if not cfg.is_file():
        raise FileNotFoundError(f"project not found: {project_slug!r}")
    return cfg


def resolve_point_workdir(*, project_slug: str, lat: float, lon: float) -> Path:
    lat_n, lon_n = normalize_point_coords(lat, lon)
    cfg = preset_path_for_project(project_slug)
    preset = load_preset(cfg)
    viewsheds_root = _viewsheds_root_for_preset(cfg)
    if viewsheds_root is None:
        raise FileNotFoundError("viewshed root unavailable")
    return resolved_viewshed_workdir_for_coords(
        preset=preset,
        viewshed_root=viewsheds_root,
        lat=lat_n,
        lon=lon_n,
    )


def resolve_site_workdir(*, project_slug: str, site_slug: str) -> Path:
    cfg = preset_path_for_project(project_slug)
    preset = load_preset(cfg)
    if site_slug not in preset.sites:
        raise FileNotFoundError(f"unknown site {site_slug!r} in project {project_slug!r}")
    viewsheds_root = _viewsheds_root_for_preset(cfg)
    if viewsheds_root is None:
        raise FileNotFoundError("viewshed root unavailable")
    site = preset.sites[site_slug]
    return resolved_viewshed_workdir_for_coords(
        preset=preset,
        viewshed_root=viewsheds_root,
        lat=float(site.lat),
        lon=float(site.lon),
    )


def latlonbox_image_coordinates(box: dict[str, float]) -> list[list[float]]:
    """MapLibre ``image`` source corners: top-left, top-right, bottom-right, bottom-left ``[lon, lat]``."""
    north, south, east, west, rot = bbox_rotation_normalized(box)
    corners: list[list[float]] = []
    for u, v in ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)):
        lat, lon = fraction_to_lat_lon(
            u,
            v,
            north=north,
            south=south,
            east=east,
            west=west,
            rotation_deg=rot,
        )
        corners.append([float(lon), float(lat)])
    return corners


def _viewsheds_root_for_preset(preset_path: Path) -> Path | None:
    try:
        bundles_root = resolved_bundle_dir(preset_path=preset_path.expanduser().resolve())
        return resolved_viewshed_dir(bundles_root)
    except Exception:
        return None


def _bounds_for_workdir(workdir: Path) -> dict[str, float] | None:
    manifest = workdir / "manifest.json"
    bounds = load_bounds_from_manifest(manifest)
    if bounds is not None:
        return bounds
    kml = workdir / "output.kml"
    if kml.is_file():
        return parse_lat_lon_box(kml.read_bytes())
    return None


def _raster_opacity(preset: Preset) -> float:
    display = preset.display if isinstance(preset.display, dict) else {}
    pct = kml_bundle.overlay_opacity_pct_from_display_transparency(display)
    return max(0.05, min(1.0, float(pct) / 100.0))


def resolve_splat_png_path(*, project_slug: str, site_slug: str) -> Path:
    """``build/viewsheds/<digest>/splat.png`` for a preset site (raises if missing)."""
    from peaky_finders.sites_job import peaky_projects_dir

    cfg = peaky_projects_dir() / project_slug / "config.yaml"
    if not cfg.is_file():
        raise FileNotFoundError(f"project not found: {project_slug!r}")
    preset = load_preset(cfg)
    if site_slug not in preset.sites:
        raise FileNotFoundError(f"unknown site {site_slug!r} in project {project_slug!r}")

    viewsheds_root = _viewsheds_root_for_preset(cfg)
    if viewsheds_root is None:
        raise FileNotFoundError("viewshed root unavailable")

    site = preset.sites[site_slug]
    workdir = resolved_viewshed_workdir_for_coords(
        preset=preset,
        viewshed_root=viewsheds_root,
        lat=float(site.lat),
        lon=float(site.lon),
    )
    png = (workdir / "splat.png").resolve()
    root = viewsheds_root.resolve()
    if root not in png.parents and png.parent != root:
        raise ValueError(f"viewshed path outside project viewshed root: {png}")
    if not png.is_file():
        raise FileNotFoundError(f"missing splat.png for site {site_slug!r}")
    return png


def load_site_viewshed_rasters(*, preset_path: Path, preset: Preset, project_slug: str) -> list[dict[str, Any]]:
    """Georeferenced ``splat.png`` overlays for sites that have built viewsheds."""
    preset_path_r = Path(preset_path).expanduser().resolve()
    viewsheds_root = _viewsheds_root_for_preset(preset_path_r)
    if viewsheds_root is None:
        return []

    opacity = _raster_opacity(preset)
    seen_png: set[str] = set()
    out: list[dict[str, Any]] = []

    for slug, site in sorted(preset.sites.items()):
        workdir = resolved_viewshed_workdir_for_coords(
            preset=preset,
            viewshed_root=viewsheds_root,
            lat=float(site.lat),
            lon=float(site.lon),
        )
        png = workdir / "splat.png"
        if not png.is_file():
            continue
        png_key = str(png.resolve())
        if png_key in seen_png:
            continue
        seen_png.add(png_key)

        bounds = _bounds_for_workdir(workdir)
        if bounds is None:
            continue

        out.append(
            {
                "slug": str(slug),
                "url": f"/api/projects/{project_slug}/viewsheds/{slug}/splat.png",
                "coordinates": latlonbox_image_coordinates(bounds),
                "opacity": opacity,
            }
        )
    return out
