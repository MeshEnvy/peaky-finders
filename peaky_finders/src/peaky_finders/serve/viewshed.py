"""On-demand RF viewshed PNG generation for ``peaky serve``."""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from pydantic import ValidationError

from peaky_finders.core.preset import Preset, SiteEntry
from peaky_finders.core.preset.paths import resolved_viewshed_root
from peaky_finders.core.rf.mapping import preset_to_request
from peaky_finders.core.viewshed.kml import load_bounds_from_manifest, parse_lat_lon_box
from peaky_finders.core.viewshed.pipeline import ensure_splat_raster_png, run_viewshed_coverage
from peaky_finders.core.viewshed.polygonize import SPLAT_OUTPUT_PPM_BASENAME
from peaky_finders.core.viewshed.raster import (
    bbox_rotation_normalized,
    fraction_to_lat_lon,
    splat_png_is_valid,
    splat_png_is_valid_fast,
)
from peaky_finders.core.viewshed.workspace import (
    resolved_viewshed_workdir,
    viewshed_request_digest_matches,
    viewshed_workspace_digest,
)
from peaky_finders.serve.preset_cache import load_serve_project_context
from peaky_finders.serve.viewshed_sim import ViewshedSimOverrides, viewshed_sim_query_string

_workdir_locks: dict[str, threading.Lock] = {}
_workdir_locks_guard = threading.Lock()

DEFAULT_SERVE_COVERAGE_MAX_CONCURRENT = 4

_coverage_sem: threading.BoundedSemaphore | None = None
_coverage_sem_guard = threading.Lock()
_coverage_sem_slots: int | None = None


def resolve_serve_coverage_max_concurrent() -> int:
    """Max concurrent splatter coverage runs for ``peaky serve`` (default 1)."""
    raw = os.environ.get("PEAKY_SERVE_COVERAGE_CONCURRENT", "").strip()
    if raw:
        return max(1, int(raw))
    return DEFAULT_SERVE_COVERAGE_MAX_CONCURRENT


def _get_coverage_semaphore() -> threading.BoundedSemaphore:
    global _coverage_sem, _coverage_sem_slots
    slots = resolve_serve_coverage_max_concurrent()
    with _coverage_sem_guard:
        if _coverage_sem is None or _coverage_sem_slots != slots:
            _coverage_sem = threading.BoundedSemaphore(slots)
            _coverage_sem_slots = slots
        return _coverage_sem


def _reset_coverage_semaphore_for_tests() -> None:
    """Drop the lazy coverage semaphore (tests only)."""
    global _coverage_sem, _coverage_sem_slots
    with _coverage_sem_guard:
        _coverage_sem = None
        _coverage_sem_slots = None


@contextmanager
def _coverage_slot(*, verbose: bool, site_slug: str) -> Iterator[None]:
    """Serialize splatter coverage; single jobs already fan out across CPU cores."""
    sem = _get_coverage_semaphore()
    if verbose:
        print(f"serve viewshed: wait coverage slot ({site_slug})", flush=True)
    sem.acquire()
    try:
        if verbose:
            print(f"serve viewshed: coverage slot ({site_slug})", flush=True)
        yield
    finally:
        sem.release()


class ServeViewshedError(Exception):
    """Viewshed could not be loaded or generated for serve."""


DRAFT_VIEWSHED_SLUG = "_draft"


def viewshed_png_api_path(
    project_slug: str,
    site_slug: str,
    *,
    sim_overrides: ViewshedSimOverrides | None = None,
) -> str:
    """URL path for a site's ``splat.png`` raster."""
    base = f"/api/p/{project_slug}/viewsheds/{site_slug}/splat.png"
    qs = viewshed_sim_query_string(sim_overrides)
    return f"{base}?{qs}" if qs else base


def viewshed_meta_api_path(project_slug: str, site_slug: str) -> str:
    """URL path for MapLibre overlay metadata (bounds + PNG URL)."""
    return f"/api/p/{project_slug}/viewsheds/{site_slug}"


def viewshed_index_api_path(project_slug: str) -> str:
    """URL path for bulk cached viewshed overlay metadata."""
    return f"/api/p/{project_slug}/viewsheds/index"


def viewshed_cache_png_api_path(project_slug: str, digest: str) -> str:
    """Immutable digest-keyed PNG URL (browser-cacheable)."""
    return f"/api/p/{project_slug}/cache/viewsheds/{digest}/splat.png"


def viewshed_prefetch_png_api_path(
    project_slug: str,
    lat: float,
    lon: float,
    *,
    sim_overrides: ViewshedSimOverrides | None = None,
) -> str:
    """URL path for a draft-site ``splat.png`` at ``lat``/``lon``."""
    params = f"lat={lat}&lon={lon}"
    sim_qs = viewshed_sim_query_string(sim_overrides)
    if sim_qs:
        params = f"{params}&{sim_qs}"
    return f"/api/p/{project_slug}/viewsheds/prefetch/splat.png?{params}"


def _viewshed_overlay_png_url(
    project_slug: str,
    site_slug: str,
    site: SiteEntry,
    *,
    sim_overrides: ViewshedSimOverrides | None = None,
) -> str:
    """MapLibre raster URL for a site or coordinate draft viewshed."""
    if site_slug == DRAFT_VIEWSHED_SLUG:
        return viewshed_prefetch_png_api_path(
            project_slug,
            float(site.lat),
            float(site.lon),
            sim_overrides=sim_overrides,
        )
    return viewshed_png_api_path(project_slug, site_slug, sim_overrides=sim_overrides)


def image_coordinates_from_bbox(bbox: dict[str, float]) -> list[list[float]]:
    """MapLibre image corners: top-left, top-right, bottom-right, bottom-left as ``[lon, lat]``."""
    north, south, east, west, rotation = bbox_rotation_normalized(bbox)
    coords: list[list[float]] = []
    for u_frac, v_frac in ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)):
        lat, lon = fraction_to_lat_lon(
            u_frac,
            v_frac,
            north=north,
            south=south,
            east=east,
            west=west,
            rotation_deg=rotation,
        )
        coords.append([lon, lat])
    return coords


def load_viewshed_bounds(workdir: Path) -> dict[str, float] | None:
    """Read KML GroundOverlay bounds from workspace ``manifest.json`` or ``output.kml``."""
    wd = Path(workdir).expanduser().resolve()
    bounds = load_bounds_from_manifest(wd / "manifest.json")
    if bounds is not None:
        return bounds
    kml = wd / "output.kml"
    if not kml.is_file():
        return None
    try:
        return parse_lat_lon_box(kml.read_bytes())
    except (OSError, ValueError):
        return None


def _workdir_lock(workdir: Path) -> threading.Lock:
    key = str(workdir.resolve())
    with _workdir_locks_guard:
        lock = _workdir_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _workdir_locks[key] = lock
        return lock


def _load_viewshed_preset(project_dir: Path) -> Preset:
    try:
        return load_serve_project_context(project_dir).preset
    except (ValueError, ValidationError) as e:
        raise ServeViewshedError(f"invalid preset: {e}") from e


def _preview_site_at(lat: float, lon: float) -> SiteEntry:
    if not (-90.0 <= lat <= 90.0):
        raise ServeViewshedError(f"lat out of bounds: {lat}")
    if not (-180.0 <= lon <= 180.0):
        raise ServeViewshedError(f"lon out of bounds: {lon}")
    return SiteEntry(name="Preview", loc=(lat, lon))


def ensure_coords_viewshed_png(
    project_dir: Path,
    lat: float,
    lon: float,
    *,
    sim_overrides: ViewshedSimOverrides | None = None,
    verbose: bool = False,
) -> Path:
    """Warm viewshed workspace cache for a coordinate (no preset site entry required)."""
    site = _preview_site_at(lat, lon)
    return ensure_site_viewshed_png(
        project_dir,
        DRAFT_VIEWSHED_SLUG,
        site,
        sim_overrides=sim_overrides,
        verbose=verbose,
    )


def ensure_coords_viewshed_overlay(
    project_slug: str,
    project_dir: Path,
    lat: float,
    lon: float,
    *,
    sim_overrides: ViewshedSimOverrides | None = None,
    verbose: bool = False,
) -> dict[str, object]:
    """Ensure PNG exists for coordinates and return MapLibre overlay metadata."""
    site = _preview_site_at(lat, lon)
    preset = _load_viewshed_preset(project_dir)
    workdir = resolve_site_viewshed_workdir(project_dir, preset, site, sim_overrides=sim_overrides)
    ensure_site_viewshed_png(
        project_dir,
        DRAFT_VIEWSHED_SLUG,
        site,
        sim_overrides=sim_overrides,
        verbose=verbose,
    )
    bounds = load_viewshed_bounds(workdir)
    if bounds is None:
        raise ServeViewshedError("missing GroundOverlay bounds for draft viewshed")
    return {
        "slug": DRAFT_VIEWSHED_SLUG,
        "url": _viewshed_overlay_png_url(
            project_slug, DRAFT_VIEWSHED_SLUG, site, sim_overrides=sim_overrides
        ),
        "coordinates": image_coordinates_from_bbox(bounds),
        "lat": lat,
        "lon": lon,
    }


def resolve_site_viewshed_workdir(
    project_dir: Path,
    preset: Preset,
    site: SiteEntry,
    *,
    sim_overrides: ViewshedSimOverrides | None = None,
) -> Path:
    """Filesystem workspace for one site's propagation fingerprint."""
    viewshed_root = resolved_viewshed_root(project_dir / "config.yaml")
    digest = viewshed_workspace_digest(
        request=_viewshed_request(preset, site, sim_overrides=sim_overrides)
    )
    return resolved_viewshed_workdir(digest=digest, viewshed_root=viewshed_root)


def _viewshed_request(
    preset: Preset,
    site: SiteEntry,
    *,
    sim_overrides: ViewshedSimOverrides | None = None,
):
    ov = sim_overrides or ViewshedSimOverrides()
    return preset_to_request(
        preset,
        float(site.lat),
        float(site.lon),
        site=site,
        radius_km=ov.radius_km,
        raster_dimension=ov.raster_dimension,
    )


def _cached_splat_png(
    workdir: Path,
    *,
    expected_workspace_digest: str,
    quick_png_check: bool = False,
) -> Path | None:
    """Return cached ``splat.png`` when digest matches and the PNG is complete."""
    png = workdir / "splat.png"
    if not png.is_file():
        return None
    if not viewshed_request_digest_matches(
        workdir, expected_workspace_digest=expected_workspace_digest
    ):
        return None
    png_ok = splat_png_is_valid_fast(png) if quick_png_check else splat_png_is_valid(png)
    if not png_ok:
        return None
    return png.resolve()


def _regenerate_splat_png_from_ppm(
    workdir: Path,
    *,
    expected_workspace_digest: str,
    site_label: str,
    verbose: bool = False,
) -> Path | None:
    """Rebuild ``splat.png`` from ``output.ppm`` when coverage is already cached."""
    ppm = workdir / SPLAT_OUTPUT_PPM_BASENAME
    if not ppm.is_file():
        return None
    if not viewshed_request_digest_matches(
        workdir, expected_workspace_digest=expected_workspace_digest
    ):
        return None
    if verbose:
        print(f"serve viewshed: raster {site_label} ({workdir.name})", flush=True)
    ensure_splat_raster_png(site_name=site_label, data_dir=workdir)
    return _cached_splat_png(workdir, expected_workspace_digest=expected_workspace_digest)


def ensure_site_viewshed_png(
    project_dir: Path,
    site_slug: str,
    site: SiteEntry,
    *,
    sim_overrides: ViewshedSimOverrides | None = None,
    verbose: bool = False,
) -> Path:
    """Return ``splat.png``, running splatter coverage + raster when not cached."""
    preset = _load_viewshed_preset(project_dir)

    workdir = resolve_site_viewshed_workdir(project_dir, preset, site, sim_overrides=sim_overrides)
    req = _viewshed_request(preset, site, sim_overrides=sim_overrides)
    digest = viewshed_workspace_digest(request=req)
    site_label = site.name.strip() or site_slug

    with _workdir_lock(workdir):
        workdir.mkdir(parents=True, exist_ok=True)
        (workdir / "request.json").write_text(
            req.model_dump_json(indent=2, exclude_none=True),
            encoding="utf-8",
        )

        png = workdir / "splat.png"
        cached = _cached_splat_png(workdir, expected_workspace_digest=digest)
        if cached is not None:
            return cached

        regenerated = _regenerate_splat_png_from_ppm(
            workdir,
            expected_workspace_digest=digest,
            site_label=site_label,
            verbose=verbose,
        )
        if regenerated is not None:
            return regenerated

    with _workdir_lock(workdir):
        cached = _cached_splat_png(workdir, expected_workspace_digest=digest)
        if cached is not None:
            return cached

        regenerated = _regenerate_splat_png_from_ppm(
            workdir,
            expected_workspace_digest=digest,
            site_label=site_label,
            verbose=verbose,
        )
        if regenerated is not None:
            return regenerated

        if verbose:
            print(
                f"serve viewshed: coverage {site_slug} ({workdir.name})",
                flush=True,
            )
        rc = run_viewshed_coverage(
            site_name=site_label,
            provider=preset.simulation.provider,
            data_dir=workdir,
            coverage_verbose=verbose,
        )
        if rc != 0:
            raise ServeViewshedError(f"coverage failed for site {site_slug!r}")

        ensure_splat_raster_png(site_name=site_label, data_dir=workdir)
        if not png.is_file():
            raise ServeViewshedError(
                f"missing splat.png after generation for {site_slug!r}"
            )
        if verbose:
            print(f"serve viewshed: done {site_slug} ({workdir.name})", flush=True)
        return png.resolve()


def site_viewshed_overlay_if_ready(
    project_slug: str,
    project_dir: Path,
    site_slug: str,
    site: SiteEntry,
    *,
    sim_overrides: ViewshedSimOverrides | None = None,
) -> dict[str, object] | None:
    """Return MapLibre overlay metadata when ``splat.png`` is cached; else ``None``."""
    try:
        preset = _load_viewshed_preset(project_dir)
    except ServeViewshedError:
        return None

    workdir = resolve_site_viewshed_workdir(project_dir, preset, site, sim_overrides=sim_overrides)
    digest = viewshed_workspace_digest(
        request=_viewshed_request(preset, site, sim_overrides=sim_overrides)
    )
    png = _cached_splat_png(
        workdir,
        expected_workspace_digest=digest,
        quick_png_check=True,
    )
    if png is None:
        return None

    bounds = load_viewshed_bounds(workdir)
    if bounds is None:
        return None
    return {
        "slug": site_slug,
        "digest": digest,
        "url": viewshed_cache_png_api_path(project_slug, digest),
        "coordinates": image_coordinates_from_bbox(bounds),
    }


def read_site_viewshed_png_if_ready(
    project_dir: Path,
    site: SiteEntry,
    *,
    site_slug: str,
    sim_overrides: ViewshedSimOverrides | None = None,
) -> Path | None:
    """Return cached ``splat.png`` when digest matches; else ``None``."""
    try:
        preset = _load_viewshed_preset(project_dir)
    except ServeViewshedError:
        return None

    workdir = resolve_site_viewshed_workdir(project_dir, preset, site, sim_overrides=sim_overrides)
    digest = viewshed_workspace_digest(
        request=_viewshed_request(preset, site, sim_overrides=sim_overrides)
    )
    return _cached_splat_png(
        workdir,
        expected_workspace_digest=digest,
        quick_png_check=True,
    )


def ensure_site_viewshed_overlay(
    project_slug: str,
    project_dir: Path,
    site_slug: str,
    site: SiteEntry,
    *,
    sim_overrides: ViewshedSimOverrides | None = None,
    verbose: bool = False,
) -> dict[str, object]:
    """Ensure PNG exists and return MapLibre overlay metadata."""
    preset = _load_viewshed_preset(project_dir)
    workdir = resolve_site_viewshed_workdir(project_dir, preset, site, sim_overrides=sim_overrides)
    ensure_site_viewshed_png(
        project_dir,
        site_slug,
        site,
        sim_overrides=sim_overrides,
        verbose=verbose,
    )
    bounds = load_viewshed_bounds(workdir)
    if bounds is None:
        raise ServeViewshedError(f"missing GroundOverlay bounds for site {site_slug!r}")
    return {
        "slug": site_slug,
        "url": _viewshed_overlay_png_url(
            project_slug, site_slug, site, sim_overrides=sim_overrides
        ),
        "coordinates": image_coordinates_from_bbox(bounds),
    }
