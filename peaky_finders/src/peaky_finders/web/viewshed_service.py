"""On-demand viewshed ensure + metadata for the web API."""

from __future__ import annotations

from typing import Any

from peaky_finders.sites_job import load_preset
from peaky_finders.splat_pipeline import run_viewshed_workspace
from peaky_finders.viewshed_workspace import resolved_viewshed_workdir_for_coords
from peaky_finders.web.viewshed_manager import (
    VIEWSHED_MANAGER,
    point_job_key,
    site_job_key,
)
from peaky_finders.web.viewshed_rasters import (
    _bounds_for_workdir,
    _raster_opacity,
    _viewsheds_root_for_preset,
    latlonbox_image_coordinates,
    normalize_point_coords,
    preset_path_for_project,
    resolve_point_workdir,
    resolve_splat_png_path,
)
from peaky_finders.web.viewshed_tiles import point_tile_layer_metadata, tile_layer_metadata


def wait_for_site_viewshed(*, project_slug: str, site_slug: str) -> None:
    """Block until a site viewshed exists."""
    if viewshed_is_cached(project_slug=project_slug, site_slug=site_slug):
        return
    ensure_site_viewshed(project_slug=project_slug, site_slug=site_slug)


def viewshed_is_cached(*, project_slug: str, site_slug: str) -> bool:
    try:
        resolve_splat_png_path(project_slug=project_slug, site_slug=site_slug)
    except FileNotFoundError:
        return False
    return True


def viewshed_raster_record(
    *,
    project_slug: str,
    site_slug: str,
    computed: bool = False,
) -> dict[str, Any]:
    cfg = preset_path_for_project(project_slug)
    preset = load_preset(cfg)
    if site_slug not in preset.sites:
        raise FileNotFoundError(f"unknown site {site_slug!r} in project {project_slug!r}")
    if not preset.sites[site_slug].participates_in_rf:
        raise FileNotFoundError(f"site {site_slug!r} is a goal marker (no viewshed)")

    resolve_splat_png_path(project_slug=project_slug, site_slug=site_slug)
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
    bounds = _bounds_for_workdir(workdir)
    if bounds is None:
        raise FileNotFoundError(f"missing viewshed bounds for site {site_slug!r}")

    tiles = tile_layer_metadata(
        project_slug=project_slug,
        site_slug=site_slug,
        workdir=workdir,
        bounds=bounds,
    )
    return {
        "slug": site_slug,
        "digest": workdir.name,
        "url": f"/api/projects/{project_slug}/viewsheds/{site_slug}/splat.png",
        "opacity": _raster_opacity(preset),
        "cached": not computed,
        "computed": computed,
        **tiles,
    }


def _run_site_viewshed_build(
    *,
    project_slug: str,
    site_slug: str,
    verbose: bool = False,
) -> None:
    cfg = preset_path_for_project(project_slug)
    preset = load_preset(cfg)
    site = preset.sites[site_slug]
    viewsheds_root = _viewsheds_root_for_preset(cfg)
    if viewsheds_root is None:
        raise FileNotFoundError("viewshed root unavailable")

    workdir = resolved_viewshed_workdir_for_coords(
        preset=preset,
        viewshed_root=viewsheds_root,
        lat=float(site.lat),
        lon=float(site.lon),
    )
    run_viewshed_workspace(
        preset=preset,
        lat=float(site.lat),
        lon=float(site.lon),
        workdir=workdir,
        site_label=(site.name.strip() or site_slug),
        coverage_verbose=verbose,
    )
    if not (workdir / "splat.png").is_file():
        raise RuntimeError(f"viewshed raster missing after compute for {site_slug!r}")


def ensure_site_viewshed(
    *,
    project_slug: str,
    site_slug: str,
    force: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    """Ensure ``splat.png`` exists for a preset site; compute on miss via the viewshed queue."""
    cfg = preset_path_for_project(project_slug)
    preset = load_preset(cfg)
    if site_slug not in preset.sites:
        raise FileNotFoundError(f"unknown site {site_slug!r} in project {project_slug!r}")
    if not preset.sites[site_slug].participates_in_rf:
        raise FileNotFoundError(f"site {site_slug!r} is a goal marker (no viewshed)")

    computed = False
    if force or not viewshed_is_cached(project_slug=project_slug, site_slug=site_slug):
        key = site_job_key(project_slug, site_slug)
        VIEWSHED_MANAGER.run(
            key,
            lambda: _run_site_viewshed_build(
                project_slug=project_slug,
                site_slug=site_slug,
                verbose=verbose,
            ),
        )
        computed = True

    return viewshed_raster_record(
        project_slug=project_slug,
        site_slug=site_slug,
        computed=computed,
    )


def get_site_viewshed(
    *,
    project_slug: str,
    site_slug: str,
    force: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    return ensure_site_viewshed(
        project_slug=project_slug,
        site_slug=site_slug,
        force=force,
        verbose=verbose,
    )


def _point_coord_key(lat: float, lon: float) -> tuple[str, str]:
    lat_n, lon_n = normalize_point_coords(lat, lon)
    return (f"{lat_n:.6f}", f"{lon_n:.6f}")


def _point_query(lat: float, lon: float) -> str:
    lat_s, lon_s = _point_coord_key(lat, lon)
    return f"lat={lat_s}&lon={lon_s}"


def point_viewshed_is_cached(*, project_slug: str, lat: float, lon: float) -> bool:
    try:
        workdir = resolve_point_workdir(project_slug=project_slug, lat=lat, lon=lon)
    except FileNotFoundError:
        return False
    return (workdir / "splat.png").is_file()


def point_viewshed_raster_record(
    *,
    project_slug: str,
    lat: float,
    lon: float,
    computed: bool = False,
) -> dict[str, Any]:
    cfg = preset_path_for_project(project_slug)
    preset = load_preset(cfg)
    workdir = resolve_point_workdir(project_slug=project_slug, lat=lat, lon=lon)
    png = workdir / "splat.png"
    if not png.is_file():
        raise FileNotFoundError(f"missing viewshed raster for {lat:.5f},{lon:.5f}")

    bounds = _bounds_for_workdir(workdir)
    if bounds is None:
        raise FileNotFoundError(f"missing viewshed bounds for {lat:.5f},{lon:.5f}")

    q = _point_query(lat, lon)
    tiles = point_tile_layer_metadata(
        project_slug=project_slug,
        lat=lat,
        lon=lon,
        workdir=workdir,
        bounds=bounds,
    )
    slug = f"at-{workdir.name}"
    return {
        "slug": slug,
        "digest": workdir.name,
        "lat": float(lat),
        "lon": float(lon),
        "url": f"/api/projects/{project_slug}/viewsheds/at/splat.png?{q}",
        "coordinates": latlonbox_image_coordinates(bounds),
        "opacity": _raster_opacity(preset),
        "cached": not computed,
        "computed": computed,
        **tiles,
    }


def _run_point_viewshed_build(
    *,
    project_slug: str,
    lat: float,
    lon: float,
    verbose: bool = False,
) -> None:
    lat_n, lon_n = normalize_point_coords(lat, lon)
    cfg = preset_path_for_project(project_slug)
    preset = load_preset(cfg)
    viewsheds_root = _viewsheds_root_for_preset(cfg)
    if viewsheds_root is None:
        raise FileNotFoundError("viewshed root unavailable")

    workdir = resolved_viewshed_workdir_for_coords(
        preset=preset,
        viewshed_root=viewsheds_root,
        lat=lat_n,
        lon=lon_n,
    )
    if verbose:
        print(
            f"viewshed at: {lat_n:.6f},{lon_n:.6f} workdir={workdir.name}",
            flush=True,
        )
    run_viewshed_workspace(
        preset=preset,
        lat=lat_n,
        lon=lon_n,
        workdir=workdir,
        site_label=f"web {lat_n:.6f},{lon_n:.6f}",
        coverage_verbose=verbose,
    )
    if not (workdir / "splat.png").is_file():
        raise RuntimeError(f"viewshed raster missing after compute at {lat_n:.6f},{lon_n:.6f}")


def ensure_point_viewshed(
    *,
    project_slug: str,
    lat: float,
    lon: float,
    force: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    """Ensure ``splat.png`` exists for arbitrary WGS-84 coordinates."""
    lat_n, lon_n = normalize_point_coords(lat, lon)

    computed = False
    if force or not point_viewshed_is_cached(project_slug=project_slug, lat=lat_n, lon=lon_n):
        key = point_job_key(project_slug, lat_n, lon_n)
        VIEWSHED_MANAGER.run(
            key,
            lambda: _run_point_viewshed_build(
                project_slug=project_slug,
                lat=lat_n,
                lon=lon_n,
                verbose=verbose,
            ),
        )
        computed = True

    return point_viewshed_raster_record(
        project_slug=project_slug,
        lat=lat_n,
        lon=lon_n,
        computed=computed,
    )


def get_point_viewshed(
    *,
    project_slug: str,
    lat: float,
    lon: float,
    force: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    lat_n, lon_n = normalize_point_coords(lat, lon)
    return ensure_point_viewshed(
        project_slug=project_slug,
        lat=lat_n,
        lon=lon_n,
        force=force,
        verbose=verbose,
    )
