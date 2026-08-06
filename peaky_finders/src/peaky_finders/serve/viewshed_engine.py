"""Coalesced viewshed coverage + footprint ensures for serve."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TypeVar

from shapely.geometry.base import BaseGeometry

from peaky_finders.core.links.viewshed import load_viewshed_footprint
from peaky_finders.core.preset.model import Preset, SiteEntry
from peaky_finders.core.rf.mapping import preset_to_request
from peaky_finders.core.viewshed.workspace import (
    resolved_viewshed_workdir_for_coords,
    viewshed_workspace_digest,
)
from peaky_finders.core.preset.paths import resolved_viewshed_root
from peaky_finders.serve.viewshed import (
    ServeViewshedError,
    _preview_site_at,
    ensure_site_viewshed_png,
    resolve_site_viewshed_workdir,
)
from peaky_finders.serve.viewshed_sim import ViewshedSimOverrides

_T = TypeVar("_T")


def footprint_cache_key(project_dir: Path, digest: str) -> str:
    root = Path(project_dir).expanduser().resolve()
    return f"{root}:{str(digest).strip().lower()}"


def site_footprint_digest(
    preset: Preset,
    site: SiteEntry,
    *,
    sim: ViewshedSimOverrides | None = None,
) -> str:
    ov = sim or ViewshedSimOverrides()
    req = preset_to_request(
        preset,
        float(site.lat),
        float(site.lon),
        site=site,
        radius_km=ov.radius_km,
        raster_dimension=ov.raster_dimension,
    )
    return viewshed_workspace_digest(request=req)


def coords_footprint_digest(
    preset: Preset,
    *,
    lat: float,
    lon: float,
    sim: ViewshedSimOverrides | None = None,
) -> str:
    return site_footprint_digest(preset, _preview_site_at(lat, lon), sim=sim)


@dataclass
class _InflightFootprint:
    done: threading.Event
    result: BaseGeometry | None = None
    error: BaseException | None = None


class ViewshedEngine:
    """Merge identical footprint requests; run coverage once per workspace digest."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._inflight: dict[str, _InflightFootprint] = {}
        # Persist successful footprint reads across warm passes (Nevada: ~100 GPKGs).
        self._results: dict[str, BaseGeometry] = {}

    def reset_for_tests(self) -> None:
        with self._lock:
            self._inflight.clear()
            self._results.clear()

    def _cached_result(self, key: str) -> BaseGeometry | None:
        with self._lock:
            return self._results.get(key)

    def _store_result(self, key: str, result: BaseGeometry | None) -> None:
        if result is None:
            return
        with self._lock:
            self._results[key] = result

    def _ensure(
        self,
        key: str,
        runner: Callable[[], BaseGeometry | None],
        *,
        verbose: bool = False,
    ) -> BaseGeometry | None:
        cached = self._cached_result(key)
        if cached is not None:
            return cached

        with self._lock:
            existing = self._inflight.get(key)
            if existing is not None:
                job = existing
                is_owner = False
            else:
                job = _InflightFootprint(done=threading.Event())
                self._inflight[key] = job
                is_owner = True

        if not is_owner:
            job.done.wait()
            if job.error is not None:
                raise ServeViewshedError(str(job.error)) from job.error
            return job.result

        try:
            if verbose:
                print(f"viewshed engine: ensure footprint {key.rsplit(':', 1)[-1]}", flush=True)
            job.result = runner()
            self._store_result(key, job.result)
        except BaseException as exc:
            job.error = exc
            raise
        finally:
            job.done.set()
            with self._lock:
                if self._inflight.get(key) is job:
                    del self._inflight[key]
        return job.result

    def read_site_footprint(
        self,
        project_dir: Path,
        preset: Preset,
        site: SiteEntry,
        *,
        sim: ViewshedSimOverrides | None = None,
        verbose: bool = False,
    ) -> BaseGeometry | None:
        """Return an existing footprint from memory or disk — never generate coverage."""
        sim = sim or ViewshedSimOverrides()
        digest = site_footprint_digest(preset, site, sim=sim)
        key = footprint_cache_key(project_dir, digest)
        cached = self._cached_result(key)
        if cached is not None:
            return cached
        preset_path = Path(project_dir).expanduser().resolve() / "config.yaml"
        viewshed_root = resolved_viewshed_root(preset_path)
        workdir = resolved_viewshed_workdir_for_coords(
            preset=preset,
            viewshed_root=viewshed_root,
            lat=float(site.lat),
            lon=float(site.lon),
            site=site,
        )
        fp = load_viewshed_footprint(workdir, preset=preset, verbose=verbose, ensure=False)
        self._store_result(key, fp)
        return fp

    def vectorize_site_footprint(
        self,
        project_dir: Path,
        preset: Preset,
        site: SiteEntry,
        *,
        sim: ViewshedSimOverrides | None = None,
        verbose: bool = False,
    ) -> BaseGeometry | None:
        """Build ``splat.gpkg`` from cached ``output.ppm`` — never run splatter coverage."""
        sim = sim or ViewshedSimOverrides()
        digest = site_footprint_digest(preset, site, sim=sim)
        key = footprint_cache_key(project_dir, digest)
        cached = self._cached_result(key)
        if cached is not None:
            return cached
        preset_path = Path(project_dir).expanduser().resolve() / "config.yaml"
        viewshed_root = resolved_viewshed_root(preset_path)
        workdir = resolved_viewshed_workdir_for_coords(
            preset=preset,
            viewshed_root=viewshed_root,
            lat=float(site.lat),
            lon=float(site.lon),
            site=site,
        )
        fp = load_viewshed_footprint(workdir, preset=preset, verbose=verbose, ensure=True)
        self._store_result(key, fp)
        return fp

    def read_coords_footprint(
        self,
        project_dir: Path,
        preset: Preset,
        *,
        lat: float,
        lon: float,
        sim: ViewshedSimOverrides | None = None,
        verbose: bool = False,
    ) -> BaseGeometry | None:
        """Return an existing draft-coordinate footprint — never generate coverage."""
        site = _preview_site_at(lat, lon)
        return self.read_site_footprint(
            project_dir,
            preset,
            site,
            sim=sim,
            verbose=verbose,
        )

    def ensure_site_footprint(
        self,
        project_dir: Path,
        preset: Preset,
        site_slug: str,
        site: SiteEntry,
        *,
        sim: ViewshedSimOverrides | None = None,
        verbose: bool = False,
    ) -> BaseGeometry | None:
        sim = sim or ViewshedSimOverrides()
        digest = site_footprint_digest(preset, site, sim=sim)
        key = footprint_cache_key(project_dir, digest)
        preset_path = Path(project_dir).expanduser().resolve() / "config.yaml"

        def _runner() -> BaseGeometry | None:
            ensure_site_viewshed_png(
                project_dir,
                site_slug,
                site,
                sim_overrides=sim,
                verbose=verbose,
            )
            workdir = resolve_site_viewshed_workdir(
                project_dir,
                preset,
                site,
                sim_overrides=sim,
            )
            return load_viewshed_footprint(workdir, preset=preset, verbose=verbose)

        return self._ensure(key, _runner, verbose=verbose)

    def ensure_coords_footprint(
        self,
        project_dir: Path,
        preset: Preset,
        *,
        lat: float,
        lon: float,
        sim: ViewshedSimOverrides | None = None,
        verbose: bool = False,
    ) -> BaseGeometry | None:
        sim = sim or ViewshedSimOverrides()
        site = _preview_site_at(lat, lon)
        digest = coords_footprint_digest(preset, lat=lat, lon=lon, sim=sim)
        key = footprint_cache_key(project_dir, digest)
        preset_path = Path(project_dir).expanduser().resolve() / "config.yaml"

        def _runner() -> BaseGeometry | None:
            ensure_site_viewshed_png(
                project_dir,
                "_draft",
                site,
                sim_overrides=sim,
                verbose=verbose,
            )
            workdir = resolve_site_viewshed_workdir(
                project_dir,
                preset,
                site,
                sim_overrides=sim,
            )
            return load_viewshed_footprint(workdir, preset=preset, verbose=verbose)

        return self._ensure(key, _runner, verbose=verbose)


_engine: ViewshedEngine | None = None
_engine_guard = threading.Lock()


def get_viewshed_engine() -> ViewshedEngine:
    global _engine
    with _engine_guard:
        if _engine is None:
            _engine = ViewshedEngine()
        return _engine


def reset_viewshed_engine_for_tests() -> ViewshedEngine:
    global _engine
    with _engine_guard:
        _engine = ViewshedEngine()
        return _engine
