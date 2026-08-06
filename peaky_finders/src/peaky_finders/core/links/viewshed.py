"""Mutual site links from cached viewshed footprints (``splat.gpkg``)."""

from __future__ import annotations

from pathlib import Path

from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

from peaky_finders.core.preset.model import Preset, SiteEntry, resolved_viewshed_polygon_style
from peaky_finders.core.preset.paths import resolved_viewshed_root
from peaky_finders.core.viewshed.footprint import read_coverage_footprint
from peaky_finders.core.viewshed.pipeline import footprint_vectorize_needed, write_coverage_footprints
from peaky_finders.core.viewshed.polygonize import SPLAT_GPKG_NAME, SPLAT_OUTPUT_PPM_BASENAME
from peaky_finders.core.viewshed.workspace import resolved_viewshed_workdir_for_coords


def footprint_covers_point(footprint: BaseGeometry | None, *, lat: float, lon: float) -> bool:
    """True when *footprint* contains the WGS84 point (lon, lat)."""
    if footprint is None or footprint.is_empty:
        return False
    return bool(footprint.covers(Point(float(lon), float(lat))))


def mutual_viewshed_link(
    footprint_a: BaseGeometry | None,
    footprint_b: BaseGeometry | None,
    *,
    lat_a: float,
    lon_a: float,
    lat_b: float,
    lon_b: float,
) -> bool:
    """Bidirectional link: each pin lies inside the other's viewshed footprint."""
    return footprint_covers_point(footprint_a, lat=lat_b, lon=lon_b) and footprint_covers_point(
        footprint_b, lat=lat_a, lon=lon_a
    )


def _discard_footprint_gpkg(gpkg: Path) -> None:
    """Remove footprint artifacts so vectorize can recreate them."""
    wd = gpkg.parent
    for name in (SPLAT_GPKG_NAME, f"{SPLAT_GPKG_NAME}-journal", "splat.wkb"):
        path = wd / name if name != f"{SPLAT_GPKG_NAME}-journal" else Path(f"{gpkg}-journal")
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _footprint_needs_vectorize(wd: Path) -> bool:
    """True when ``output.ppm`` should be (re)vectorized to ``splat.gpkg``."""
    if footprint_vectorize_needed(wd):
        return True
    if _footprint_polygon_stale(wd):
        return True
    gpkg = wd / SPLAT_GPKG_NAME
    if not gpkg.is_file():
        return (wd / SPLAT_OUTPUT_PPM_BASENAME).is_file()
    try:
        return read_coverage_footprint(gpkg) is None
    except Exception:
        return True


def _ensure_footprint_gpkg(workdir: Path, *, preset: Preset, verbose: bool = False) -> Path:
    """Return ``splat.gpkg`` path, vectorizing from ``output.ppm`` when needed."""
    wd = Path(workdir).expanduser().resolve()
    gpkg = wd / SPLAT_GPKG_NAME
    if not _footprint_needs_vectorize(wd):
        return gpkg
    style = resolved_viewshed_polygon_style(preset.display)
    if verbose:
        print(f"links: vectorize footprint {wd.name}", flush=True)
    _discard_footprint_gpkg(gpkg)
    if not write_coverage_footprints(data_dir=wd, polygon_style=style):
        _discard_footprint_gpkg(gpkg)
        if not write_coverage_footprints(data_dir=wd, polygon_style=style):
            raise RuntimeError(f"footprint vectorize failed under {wd}")
    return gpkg


def _footprint_polygon_stale(wd: Path) -> bool:
    """True when raster coverage exists but the GPKG footprint is missing or older."""
    gpkg = wd / SPLAT_GPKG_NAME
    png = wd / "splat.png"
    if footprint_vectorize_needed(wd):
        return True
    if not gpkg.is_file():
        return png.is_file()
    if png.is_file() and gpkg.stat().st_mtime < png.stat().st_mtime:
        return True
    return False


def load_viewshed_footprint(
    workdir: Path,
    *,
    preset: Preset,
    verbose: bool = False,
    ensure: bool = True,
) -> BaseGeometry | None:
    """Read viewshed footprint polygon for a workspace.

    When *ensure* is true (warm / engine paths), vectorize from ``output.ppm`` if
    ``splat.gpkg`` is missing or stale. When false (serve GET /links), only read an
    existing fresh GPKG — never block the request on polygonize.
    """
    wd = Path(workdir).expanduser().resolve()
    gpkg = wd / SPLAT_GPKG_NAME
    if gpkg.is_file() and not _footprint_needs_vectorize(wd):
        try:
            return read_coverage_footprint(gpkg)
        except Exception:
            if not ensure:
                return None
    if not ensure:
        return None
    if not (wd / SPLAT_OUTPUT_PPM_BASENAME).is_file():
        if not gpkg.is_file():
            return None
        try:
            return read_coverage_footprint(gpkg)
        except Exception:
            return None
    gpkg = _ensure_footprint_gpkg(wd, preset=preset, verbose=verbose)
    return read_coverage_footprint(gpkg)


def site_viewshed_workdir(
    preset_path: Path,
    preset: Preset,
    site: SiteEntry,
) -> Path:
    """Viewshed workspace directory for a preset site."""
    viewshed_root = resolved_viewshed_root(preset_path)
    return resolved_viewshed_workdir_for_coords(
        preset=preset,
        viewshed_root=viewshed_root,
        lat=float(site.lat),
        lon=float(site.lon),
        site=site,
    )


def load_site_viewshed_footprint(
    preset_path: Path,
    preset: Preset,
    site: SiteEntry,
    *,
    verbose: bool = False,
    ensure: bool = True,
) -> BaseGeometry | None:
    """Footprint for a repeater site's cached viewshed workspace."""
    return load_viewshed_footprint(
        site_viewshed_workdir(preset_path, preset, site),
        preset=preset,
        verbose=verbose,
        ensure=ensure,
    )


def load_coords_viewshed_footprint(
    preset_path: Path,
    preset: Preset,
    *,
    lat: float,
    lon: float,
    verbose: bool = False,
    ensure: bool = True,
) -> BaseGeometry | None:
    """Footprint for a draft coordinate viewshed workspace (same digest as prefetch warm)."""
    viewshed_root = resolved_viewshed_root(preset_path)
    workdir = resolved_viewshed_workdir_for_coords(
        preset=preset,
        viewshed_root=viewshed_root,
        lat=float(lat),
        lon=float(lon),
    )
    return load_viewshed_footprint(workdir, preset=preset, verbose=verbose, ensure=ensure)
