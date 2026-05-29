"""Fill site PLSS, MLRS, and Skadi elevation from coordinates."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from peaky_finders.http_pool import HttpPool
from peaky_finders.pairwise_dem_peak import skadi_elevation_at_point
from peaky_finders.plss_mlrs_fetch import (
    CADNSDI_HTTP_POOL,
    loc_plss_resolved,
    loc_stamp,
    plss_mlrs_for_point,
    read_plss_mlrs_loc_cache,
    write_plss_mlrs_loc_cache,
)
from peaky_finders.sites_job import (
    load_preset,
    read_preset_yaml_tree,
    resolved_preset_build_dir,
    resolved_skadi_mirror_dir,
    update_preset_yaml_tree,
)


def _text_missing(val: Any) -> bool:
    if val is None:
        return True
    return isinstance(val, str) and not val.strip()


def _elevation_missing(val: Any) -> bool:
    return val is None


def resolved_dem_mirror_for_preset(preset_path: Path) -> Path | None:
    """Project ``build/dem`` mirror, else global Skadi cache when populated."""
    preset_path_r = Path(preset_path).expanduser().resolve()
    for root in (resolved_preset_build_dir(preset_path_r) / "dem", resolved_skadi_mirror_dir()):
        if root.is_dir() and any(root.glob("*.hgt.gz")):
            return root.resolve()
    return None


def lookup_plss_mlrs_for_loc(
    lat: float,
    lon: float,
    *,
    loc_cache: dict[str, dict[str, str]],
    allow_network: bool,
    http_pool: HttpPool | None = None,
    loc_cache_lock: threading.Lock | None = None,
) -> tuple[str | None, str | None, bool]:
    """Resolve PLSS/MLRS for a coordinate. Returns ``(plss, mlrs, from_network)``."""
    stamp = loc_stamp(lat, lon)

    def _cached_hit() -> tuple[str | None, str | None] | None:
        cached = loc_cache.get(stamp)
        if cached is not None and loc_plss_resolved(cached):
            plss = (cached.get("plss") or "").strip() or None
            mlrs = (cached.get("mlrs") or "").strip() or None
            return plss, mlrs
        return None

    if loc_cache_lock is not None:
        with loc_cache_lock:
            hit = _cached_hit()
    else:
        hit = _cached_hit()
    if hit is not None:
        return hit[0], hit[1], False

    if not allow_network:
        return None, None, False

    plss_s, mlrs_s = plss_mlrs_for_point(lon, lat, http_pool=http_pool)
    plss = plss_s.strip() or None
    mlrs = mlrs_s.strip() or None

    if loc_cache_lock is not None:
        with loc_cache_lock:
            hit = _cached_hit()
            if hit is not None:
                return hit[0], hit[1], False
            loc_cache[stamp] = {"plss": plss or "", "mlrs": mlrs or ""}
    else:
        loc_cache[stamp] = {"plss": plss or "", "mlrs": mlrs or ""}
    return plss, mlrs, True


def _site_loc(ent: dict[str, Any]) -> tuple[float, float] | None:
    loc_v = ent.get("loc")
    if not isinstance(loc_v, (list, tuple)) or len(loc_v) != 2:
        return None
    try:
        return float(loc_v[0]), float(loc_v[1])
    except (TypeError, ValueError):
        return None


def _apply_text_field(ent: dict[str, Any], key: str, value: str | None, *, force: bool) -> bool:
    if not force and not _text_missing(ent.get(key)):
        return False
    old = ent.get(key)
    if value:
        if old != value:
            ent[key] = value
            return True
        return False
    if key in ent:
        ent.pop(key)
        return True
    return False


def _apply_elevation_field(ent: dict[str, Any], value: float | None, *, force: bool) -> bool:
    if not force and not _elevation_missing(ent.get("elevation_m")):
        return False
    if value is None:
        if "elevation_m" in ent:
            ent.pop("elevation_m")
            return True
        return False
    rounded = round(float(value), 1)
    if ent.get("elevation_m") != rounded:
        ent["elevation_m"] = rounded
        return True
    return False


def enrich_site_entry_metadata(
    ent: dict[str, Any],
    *,
    loc_cache: dict[str, dict[str, str]],
    dem_dir: Path | None,
    allow_network_plss: bool,
    http_pool: HttpPool | None = None,
    loc_cache_lock: threading.Lock | None = None,
    force: bool = False,
) -> tuple[bool, bool]:
    """Resolve ``plss`` / ``mlrs`` / ``elevation_m`` from site coordinates.

  ``force=True`` overwrites existing values (e.g. after a coordinate edit).
  Returns ``(changed, plss_from_network)``.
    """
    loc = _site_loc(ent)
    if loc is None:
        return False, False

    lat, lon = loc
    changed = False
    plss_from_network = False

    need_plss = force or _text_missing(ent.get("plss"))
    need_mlrs = force or _text_missing(ent.get("mlrs"))
    if need_plss or need_mlrs:
        plss, mlrs, from_network = lookup_plss_mlrs_for_loc(
            lat,
            lon,
            loc_cache=loc_cache,
            allow_network=allow_network_plss,
            http_pool=http_pool,
            loc_cache_lock=loc_cache_lock,
        )
        if from_network:
            plss_from_network = True
        if need_plss and _apply_text_field(ent, "plss", plss, force=force):
            changed = True
        if need_mlrs and _apply_text_field(ent, "mlrs", mlrs, force=force):
            changed = True

    need_elev = force or _elevation_missing(ent.get("elevation_m"))
    if need_elev and dem_dir is not None:
        elev = skadi_elevation_at_point(lon, lat, dem_dir)
        if _apply_elevation_field(ent, elev, force=force):
            changed = True
    elif need_elev and force:
        if _apply_elevation_field(ent, None, force=True):
            changed = True

    return changed, plss_from_network


def _site_ent_snapshot(preset_path: Path, slug: str) -> dict[str, Any] | None:
    preset = load_preset(preset_path)
    if slug not in preset.sites:
        return None
    entry = preset.sites[slug]
    ent: dict[str, Any] = {"loc": [entry.lat, entry.lon]}
    if entry.plss is not None:
        ent["plss"] = entry.plss
    if entry.mlrs is not None:
        ent["mlrs"] = entry.mlrs
    if entry.elevation_m is not None:
        ent["elevation_m"] = entry.elevation_m
    return ent


def _merge_site_metadata_into_root(root: dict[str, Any], slug: str, enriched: dict[str, Any]) -> None:
    sites_raw = root.get("sites")
    if not isinstance(sites_raw, dict):
        return
    tgt = sites_raw.get(slug)
    if not isinstance(tgt, dict):
        return
    for key in ("plss", "mlrs", "elevation_m"):
        if key in enriched:
            tgt[key] = enriched[key]
        elif key in tgt:
            tgt.pop(key)


def _resolve_site_slugs(
    preset_path: Path,
    site_slug: str | None,
    site_slugs: set[str] | None,
) -> list[str]:
    preset_path = Path(preset_path).expanduser().resolve()
    yaml_rt, root = read_preset_yaml_tree(preset_path)
    del yaml_rt
    sites_raw = root.get("sites")
    if not isinstance(sites_raw, dict):
        return []
    if site_slug is not None:
        return [site_slug] if site_slug in sites_raw else []
    if site_slugs is not None:
        return sorted(s for s in site_slugs if s in sites_raw)
    return sorted(sites_raw.keys())


def resolve_site_metadata(
    preset_path: Path,
    *,
    site_slug: str | None = None,
    site_slugs: set[str] | None = None,
    force: bool = False,
    allow_network_plss: bool = True,
    http_pool: HttpPool | None = None,
    log_fp: Any = None,
) -> tuple[int, int]:
    """Resolve coordinate-derived metadata and persist YAML updates.

    Saves preset YAML after each updated site so a crash mid-batch does not lose progress.

    Returns ``(network_plss_lookups, sites_updated)``.
    """
    preset_path = Path(preset_path).expanduser().resolve()
    slugs = _resolve_site_slugs(preset_path, site_slug, site_slugs)
    if not slugs:
        return 0, 0

    cache_base = resolved_preset_build_dir(preset_path)
    loc_cache = read_plss_mlrs_loc_cache(cache_base)
    dem_dir = resolved_dem_mirror_for_preset(preset_path)
    pool = http_pool or CADNSDI_HTTP_POOL
    cache_lock = threading.Lock()

    network_count = 0
    updated = 0

    def _enrich_slug(slug: str) -> tuple[str, dict[str, Any] | None, bool, bool]:
        ent = _site_ent_snapshot(preset_path, slug)
        if ent is None:
            return slug, None, False, False
        changed, from_network = enrich_site_entry_metadata(
            ent,
            loc_cache=loc_cache,
            dem_dir=dem_dir,
            allow_network_plss=allow_network_plss,
            http_pool=pool,
            loc_cache_lock=cache_lock,
            force=force,
        )
        if not changed:
            return slug, None, False, from_network
        return slug, ent, True, from_network

    def _persist(slug: str, enriched: dict[str, Any] | None, changed: bool, from_network: bool) -> None:
        nonlocal network_count, updated
        if from_network:
            network_count += 1
            write_plss_mlrs_loc_cache(cache_base, loc_cache)
        if changed and enriched is not None:
            updated += 1

            def mutator(_yaml_rt: Any, root: dict[str, Any]) -> None:
                _merge_site_metadata_into_root(root, slug, enriched)

            update_preset_yaml_tree(preset_path, mutator)
            if log_fp is not None:
                print(f"site metadata: {slug} updated", file=log_fp, flush=True)

    workers = min(pool.max_concurrent, len(slugs))
    if workers <= 1:
        for slug in slugs:
            slug, enriched, changed, from_network = _enrich_slug(slug)
            _persist(slug, enriched, changed, from_network)
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(_enrich_slug, slug) for slug in slugs]
            for fut in as_completed(futures):
                slug, enriched, changed, from_network = fut.result()
                _persist(slug, enriched, changed, from_network)

    write_plss_mlrs_loc_cache(cache_base, loc_cache)
    return network_count, updated


def enrich_all_preset_sites(
    preset_path: Path,
    *,
    allow_network_plss: bool = True,
    http_pool: HttpPool | None = None,
    log_fp: Any = None,
) -> tuple[int, int]:
    """Resolve missing PLSS / MLRS / elevation for every preset site; rewrite YAML when needed."""
    return resolve_site_metadata(
        preset_path,
        force=False,
        allow_network_plss=allow_network_plss,
        http_pool=http_pool,
        log_fp=log_fp,
    )


def fill_missing_site_metadata(
    preset_path: Path,
    site_slug: str,
    *,
    allow_network_plss: bool = True,
    http_pool: HttpPool | None = None,
) -> bool:
    """Backfill one site when metadata fields are absent."""
    _, updated = resolve_site_metadata(
        preset_path,
        site_slug=site_slug,
        force=False,
        allow_network_plss=allow_network_plss,
        http_pool=http_pool,
    )
    return updated > 0


def populate_preset_missing_metadata(
    preset_path: Path,
    *,
    site_slugs: set[str] | None = None,
    allow_network_plss: bool = False,
    http_pool: HttpPool | None = None,
    log_fp: Any = None,
) -> tuple[int, int]:
    """Backfill missing metadata for selected sites (default: all)."""
    return resolve_site_metadata(
        preset_path,
        site_slugs=site_slugs,
        force=False,
        allow_network_plss=allow_network_plss,
        http_pool=http_pool,
        log_fp=log_fp,
    )
