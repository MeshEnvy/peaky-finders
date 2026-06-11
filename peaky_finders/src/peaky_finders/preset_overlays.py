"""Assemble KMZ overlays from persisted viewshed workspaces (no SPLAT re-run)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from peaky_finders import kml_bundle
from peaky_finders.preset_mapping import preset_to_request
from peaky_finders.sites_job import (
    Preset,
    SiteEntry,
    resolved_bundle_dir,
    resolved_preset_bundle_data_dir,
    resolved_viewshed_dir,
)
from peaky_finders.splat_polygonize import SPLAT_GPKG_NAME, SPLAT_KML_NAME
from peaky_finders.viewshed_workspace import (
    resolved_viewshed_workdir,
    viewshed_workspace_digest,
)


def _pretty_pin_m(value: float) -> float | int:
    return int(value) if float(value) == int(value) else value


def _site_pin_parts(*, pin_lat: float, pin_lon: float, elevation_m: float | None) -> str:
    parts = [f"Center {pin_lat:.6f}, {pin_lon:.6f}"]
    if elevation_m is not None:
        parts.append(f"{_pretty_pin_m(float(elevation_m))} m elevation")
    return " · ".join(parts)


@dataclass(frozen=True)
class SiteAssemblyAssets:
    overlays: list[kml_bundle.AggregateSiteOverlay]
    png_paths: list[Path]
    coverage_kml_paths: list[Path | None]
    coverage_gpkg_paths: list[Path]
    coverage_gpkg_by_slug: dict[str, Path]
    slug_to_digest: dict[str, str]


def collect_site_workspace_assets(
    *,
    preset_path: Path,
    preset: Preset,
    bundle_cache_root: Path,
    sites_items: list[tuple[str, SiteEntry]],
) -> SiteAssemblyAssets:
    from peaky_finders.sites_job import viewshed_polygon_coverage_kml_arcname, viewshed_raster_png_arcname

    slug_to_digest: dict[str, str] = {}
    for site_slug, site in preset.repeaters.items():
        vd = viewshed_workspace_digest(request=preset_to_request(preset, float(site.lat), float(site.lon)))
        slug_to_digest[site_slug] = vd

    viewshed_root = resolved_viewshed_dir(bundle_cache_root)
    overlays: list[kml_bundle.AggregateSiteOverlay] = []
    png_paths: list[Path] = []
    coverage_kml_paths: list[Path | None] = []
    coverage_gpkg_paths: list[Path] = []
    coverage_gpkg_by_slug: dict[str, Path] = {}

    tx_antenna_agl_m = max(1.0, float(preset.simulation.transmitter["height_m"]))

    for site_slug, site in sites_items:
        vd = slug_to_digest[site_slug]
        data_dir = resolved_viewshed_workdir(digest=vd, viewshed_root=viewshed_root)
        manifest_path = data_dir / "manifest.json"
        bounds = kml_bundle.load_bounds_from_manifest(manifest_path)
        if bounds is None:
            kml_raw = data_dir / "output.kml"
            if not kml_raw.is_file():
                raise RuntimeError(f"No output.kml for site {site.name!r} under {data_dir}")
            bounds = kml_bundle.parse_lat_lon_box(kml_raw.read_bytes())

        png_disk = data_dir / "splat.png"
        if not png_disk.is_file():
            raise RuntimeError(f"Missing splat.png for site {site.name!r} under {data_dir}")

        pin_lat, pin_lon = float(site.lat), float(site.lon)
        elev_f = float(site.elevation_m) if site.elevation_m is not None else None
        folder_name = site.name.strip()

        polygons_written = (data_dir / SPLAT_GPKG_NAME).is_file()
        cov_href = viewshed_polygon_coverage_kml_arcname(site_slug) if polygons_written else None
        cov_disk = (data_dir / SPLAT_KML_NAME) if polygons_written else None

        overlays.append(
            kml_bundle.AggregateSiteOverlay(
                slug=site_slug,
                folder_name=folder_name,
                overlay_href=viewshed_raster_png_arcname(site_slug),
                north=bounds["north"],
                south=bounds["south"],
                east=bounds["east"],
                west=bounds["west"],
                rotation=float(bounds.get("rotation", 0.0)),
                center_lat=pin_lat,
                center_lon=pin_lon,
                antenna_height_agl_m=tx_antenna_agl_m,
                pin_description=_site_pin_parts(pin_lat=pin_lat, pin_lon=pin_lon, elevation_m=elev_f),
                rationale=site.rationale,
                site_description=site.description,
                plss=site.plss,
                coverage_kml_href=cov_href,
            )
        )
        png_paths.append(png_disk.resolve())
        coverage_kml_paths.append(cov_disk)
        gp = data_dir / SPLAT_GPKG_NAME
        coverage_gpkg_by_slug[site_slug] = gp
        if polygons_written and gp.is_file():
            coverage_gpkg_paths.append(gp.resolve())

    return SiteAssemblyAssets(
        overlays=overlays,
        png_paths=png_paths,
        coverage_kml_paths=coverage_kml_paths,
        coverage_gpkg_paths=coverage_gpkg_paths,
        coverage_gpkg_by_slug=coverage_gpkg_by_slug,
        slug_to_digest=slug_to_digest,
    )


def standard_bundle_roots(preset_path: Path, preset: Preset) -> tuple[Path, Path, Path]:
    p = preset_path.expanduser().resolve()
    cache_root = resolved_bundle_dir(preset_path=p)
    dd = resolved_preset_bundle_data_dir(preset_path=p, preset=preset)
    return p, cache_root, dd
