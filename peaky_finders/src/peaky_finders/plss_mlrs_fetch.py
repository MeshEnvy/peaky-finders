"""Fetch PLSS / MLRS strings from BLM CadNSDI for preset site coordinates."""

from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from peaky_finders.content_keys import content_key_hex, read_content_key_hex, write_content_key
from peaky_finders.http_pool import HttpPool
from peaky_finders.sites_job import Preset, _slugify_files_segment, dump_preset_yaml_document, read_preset_yaml_tree

CADNSDI_BASE = "https://gis.blm.gov/arcgis/rest/services/Cadastral/BLM_Natl_PLSS_CadNSDI/MapServer"
CADNSDI_HTTP_POOL = HttpPool(max_concurrent=10, min_interval_s=0.12)

PLSS_MLRS_LOC_CACHE_FORMAT = "plss_mlrs_loc_cache/v1"
PLSS_MLRS_LOC_CACHE_SUBDIR = "plss_mlrs"
PLSS_MLRS_LOC_CACHE_BASENAME = "by_loc.json"
PLSS_BUNDLE_KEY_BASENAME = "input.sha"
PLSS_SITES_DIGEST_FORMAT = "plss_bundle_sites/v1"


def loc_stamp(lat: float, lon: float) -> str:
    """Stable string for a site ``loc``; cache key for CadNSDI PLSS/MLRS."""
    return f"{lat:.6f},{lon:.6f}"


def plss_mlrs_loc_cache_path(cache_base: Path) -> Path:
    return Path(cache_base).expanduser().resolve() / PLSS_MLRS_LOC_CACHE_SUBDIR / PLSS_MLRS_LOC_CACHE_BASENAME


def plss_bundle_key_path(cache_base: Path) -> Path:
    return plss_mlrs_loc_cache_path(cache_base).parent / PLSS_BUNDLE_KEY_BASENAME


def read_plss_mlrs_loc_cache(cache_base: Path) -> dict[str, dict[str, str]]:
    path = plss_mlrs_loc_cache_path(cache_base)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if raw.get("format") != PLSS_MLRS_LOC_CACHE_FORMAT:
        return {}
    by_loc = raw.get("by_loc")
    if not isinstance(by_loc, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for loc_key, entry in by_loc.items():
        if not isinstance(loc_key, str) or not isinstance(entry, dict):
            continue
        plss = entry.get("plss")
        mlrs = entry.get("mlrs")
        if isinstance(plss, str) and isinstance(mlrs, str):
            out[loc_key] = {"plss": plss, "mlrs": mlrs}
    return out


def write_plss_mlrs_loc_cache(cache_base: Path, by_loc: dict[str, dict[str, str]]) -> None:
    path = plss_mlrs_loc_cache_path(cache_base)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "format": PLSS_MLRS_LOC_CACHE_FORMAT,
                "by_loc": {k: by_loc[k] for k in sorted(by_loc)},
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def loc_plss_resolved(entry: dict[str, str]) -> bool:
    """True when a loc cache row has a prior CadNSDI lookup result."""
    return bool((entry.get("mlrs") or "").strip() or (entry.get("plss") or "").strip())


def plss_sites_loc_digest(preset: Preset) -> str:
    """Stable hash of site slugs and coordinates that drive PLSS/MLRS lookup."""
    rows = [(slug, loc_stamp(ent.lat, ent.lon)) for slug, ent in sorted(preset.sites.items())]
    body = json.dumps({"format": PLSS_SITES_DIGEST_FORMAT, "sites": rows}, sort_keys=True, ensure_ascii=False)
    return content_key_hex(body)


def plss_bundle_cache_stale(*, cache_base: Path, preset: Preset) -> bool:
    """True when site coords changed, cache misses, or preset PLSS fields are out of sync."""

    cache_base = Path(cache_base).expanduser().resolve()
    want = plss_sites_loc_digest(preset)
    if read_content_key_hex(plss_bundle_key_path(cache_base)) != want:
        return True
    if not plss_mlrs_loc_cache_path(cache_base).is_file():
        return True

    loc_cache = read_plss_mlrs_loc_cache(cache_base)
    _seed_loc_cache_from_preset(preset, loc_cache)
    for ent in preset.sites.values():
        stamp = loc_stamp(ent.lat, ent.lon)
        cached = loc_cache.get(stamp, {})
        if not loc_plss_resolved(cached):
            return True
        if (ent.plss or "") != (cached.get("plss") or "") or (ent.mlrs or "") != (cached.get("mlrs") or ""):
            return True
    return False


def _query(layer: int, lon: float, lat: float) -> dict:
    params = urllib.parse.urlencode(
        {
            "geometry": f"{lon},{lat}",
            "geometryType": "esriGeometryPoint",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "*",
            "returnGeometry": "false",
            "f": "json",
        }
    )
    url = f"{CADNSDI_BASE}/{layer}/query?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "peaky-finders-plss-populate/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode())


def _attrs(feat: dict) -> dict:
    return (feat or {}).get("attributes") or {}


def plss_mlrs_for_point(
    lon: float,
    lat: float,
    *,
    http_pool: HttpPool | None = None,
) -> tuple[str, str]:
    pool = http_pool or CADNSDI_HTTP_POOL
    return pool.run(lambda: _plss_mlrs_for_point_impl(lon, lat))


def _plss_mlrs_for_point_impl(lon: float, lat: float) -> tuple[str, str]:
    sec = _query(2, lon, lat)
    twp = _query(1, lon, lat)

    sa = _attrs(sec.get("features", [{}])[0] if sec.get("features") else {})
    ta = _attrs(twp.get("features", [{}])[0] if twp.get("features") else {})

    frstdivid = (sa.get("FRSTDIVID") or "").strip() or None
    plssid_sec = (sa.get("PLSSID") or "").strip()
    plssid_twp = (ta.get("PLSSID") or "").strip()
    plssid = plssid_sec or plssid_twp

    twplab = (ta.get("TWNSHPLAB") or "").strip()
    state = (ta.get("STATEABBR") or "").strip()
    mer = (ta.get("PRINMER") or "").strip()
    sec_no = (sa.get("FRSTDIVLAB") or sa.get("FRSTDIVNO") or "").strip()

    parts: list[str] = []
    if state:
        parts.append(state)
    if mer:
        parts.append(mer)
    if twplab:
        bits = twplab.split()
        if len(bits) >= 2:
            parts.append(f"T.{bits[0]} R.{bits[1]}")
        else:
            parts.append(f"T.{twplab}")
    elif ta.get("TWNSHPNO"):
        tno = str(ta.get("TWNSHPNO", "")).strip()
        tdir = str(ta.get("TWNSHPDIR", "")).strip()
        rno = str(ta.get("RANGENO", "")).strip()
        rdir = str(ta.get("RANGEDIR", "")).strip()
        parts.append(f"T{tno}{tdir} R{rno}{rdir}")
    if sec_no:
        parts.append(f"Sec. {sec_no}")
    plss_body = "; ".join(parts) if parts else ""

    meta: list[str] = []
    if plssid:
        meta.append(f"CadNSDI PLSSID={plssid}")
    if frstdivid:
        meta.append(f"FRSTDIVID={frstdivid}")

    plss = (" — ".join([plss_body, " | ".join(meta)])).strip(" —") if (plss_body or meta) else ""

    mlrs = frstdivid or (f"PLSSID:{plssid}" if plssid else "")

    return plss, mlrs


def _list_site_slug_assignments(entries: list[Any]) -> list[tuple[str, dict[str, Any]]]:
    """Match ``sites_job.load_preset`` list slug collision rules."""
    assigned: dict[str, dict[str, Any]] = {}
    out: list[tuple[str, dict[str, Any]]] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        slug = _slugify_files_segment(name)
        if slug in assigned:
            n = 2
            while f"{slug}-{n}" in assigned:
                n += 1
            slug = f"{slug}-{n}"
        assigned[slug] = item
        out.append((slug, item))
    return out


def _yaml_plss_mlrs(ent: dict[str, Any]) -> tuple[str | None, str | None]:
    plss = ent.get("plss")
    mlrs = ent.get("mlrs")
    plss_s = plss if isinstance(plss, str) else None
    mlrs_s = mlrs if isinstance(mlrs, str) else None
    return plss_s, mlrs_s


def populate_preset_plss_mlrs_file(
    preset_path: Path,
    *,
    site_slugs: set[str],
    log_fp: Any = sys.stdout,
    loc_cache: dict[str, dict[str, str]] | None = None,
    force_network: bool = False,
    progress: Callable[[str], None] | None = None,
    http_pool: HttpPool | None = None,
) -> int:
    """Update ``plss`` / ``mlrs`` in ``preset_path`` for ``site_slugs`` from coordinates (CadNSDI).

    Saves the preset after each site when values change so a crash mid-batch does not lose progress.

    When ``loc_cache`` is set, resolved hits are served from cache; only unknown locs hit CadNSDI.

    Returns the number of CadNSDI network lookups performed.
    """
    preset_path = preset_path.expanduser()
    yaml_rt, root = read_preset_yaml_tree(preset_path)
    sites_raw = root.get("sites")
    if sites_raw is None:
        return 0

    slug_entries: list[tuple[str, dict[str, Any]]] = []

    if isinstance(sites_raw, dict):
        for slug, ent in sites_raw.items():
            if slug not in site_slugs:
                continue
            if isinstance(ent, dict):
                slug_entries.append((slug, ent))
    elif isinstance(sites_raw, list):
        for slug, ent in _list_site_slug_assignments(sites_raw):
            if slug not in site_slugs:
                continue
            slug_entries.append((slug, ent))

    def _emit(msg: str) -> None:
        if progress is not None:
            progress(msg)
        else:
            print(msg, file=log_fp, flush=True)

    network_count = 0
    total = len(slug_entries)
    for i, (slug, ent) in enumerate(slug_entries, start=1):
        loc_v = ent.get("loc")
        if not isinstance(loc_v, (list, tuple)) or len(loc_v) != 2:
            continue
        try:
            lat, lon = float(loc_v[0]), float(loc_v[1])
        except (TypeError, ValueError):
            continue
        stamp = loc_stamp(lat, lon)
        try:
            cached = (loc_cache or {}).get(stamp)
            from_network = False
            if cached is not None and loc_plss_resolved(cached) and not force_network:
                plss, mlrs = cached.get("plss", ""), cached.get("mlrs", "")
            else:
                _emit(f"PLSS/MLRS: [{i}/{total}] {slug} …")
                plss, mlrs = plss_mlrs_for_point(lon, lat, http_pool=http_pool)
                from_network = True
                network_count += 1
                if loc_cache is not None:
                    loc_cache[stamp] = {"plss": plss or "", "mlrs": mlrs or ""}
            new_plss = plss or None
            new_mlrs = mlrs or None
            old_plss, old_mlrs = _yaml_plss_mlrs(ent)
            if old_plss != new_plss or old_mlrs != new_mlrs:
                ent["plss"] = new_plss
                ent["mlrs"] = new_mlrs
                dump_preset_yaml_document(yaml_rt, root, preset_path)
        except Exception as e:
            _emit(f"  ERROR {slug}: {e}")
            old_plss, old_mlrs = _yaml_plss_mlrs(ent)
            if old_plss is not None or old_mlrs is not None:
                ent["plss"] = None
                ent["mlrs"] = None
                dump_preset_yaml_document(yaml_rt, root, preset_path)
            from_network = False
    return network_count


def _seed_loc_cache_from_preset(preset: Preset, loc_cache: dict[str, dict[str, str]]) -> None:
    for ent in preset.sites.values():
        if not ent.plss and not ent.mlrs:
            continue
        stamp = loc_stamp(ent.lat, ent.lon)
        if stamp not in loc_cache:
            loc_cache[stamp] = {"plss": ent.plss or "", "mlrs": ent.mlrs or ""}


def refresh_plss_mlrs_for_bundle(
    *,
    preset_path: Path,
    cache_base: Path,
    preset: Preset,
) -> None:
    """Fill missing PLSS/mlrs from CadNSDI, sync preset YAML, and record the input key."""

    cache_base = Path(cache_base).expanduser().resolve()
    preset_path = Path(preset_path).expanduser().resolve()
    loc_cache = read_plss_mlrs_loc_cache(cache_base)
    _seed_loc_cache_from_preset(preset, loc_cache)
    site_slugs = set(preset.sites.keys())

    unresolved = 0
    for ent in preset.sites.values():
        stamp = loc_stamp(ent.lat, ent.lon)
        if not loc_plss_resolved(loc_cache.get(stamp, {})):
            unresolved += 1

    if unresolved:
        print(f"plss/mlrs: CadNSDI lookup for {unresolved} unresolved site(s)", flush=True)
    else:
        print(f"plss/mlrs: sync {len(site_slugs)} site(s) from cache", flush=True)

    network_count = populate_preset_plss_mlrs_file(
        preset_path,
        site_slugs=site_slugs,
        loc_cache=loc_cache,
        force_network=False,
        progress=None,
    )
    write_plss_mlrs_loc_cache(cache_base, loc_cache)
    write_content_key(plss_bundle_key_path(cache_base), plss_sites_loc_digest(preset))
    if network_count:
        print(f"plss/mlrs: {network_count} CadNSDI quer{'y' if network_count == 1 else 'ies'}", flush=True)

    from peaky_finders.site_metadata_enrich import populate_preset_missing_metadata

    _, elev_updated = populate_preset_missing_metadata(
        preset_path,
        site_slugs=site_slugs,
        allow_network_plss=False,
    )
    if elev_updated:
        print(f"site metadata: elevation filled for {elev_updated} site(s)", flush=True)
