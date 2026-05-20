"""Fetch PLSS / MLRS strings from BLM CadNSDI for preset site coordinates."""

from __future__ import annotations

import json
import sys
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from peaky_finders.sites_job import Preset, _slugify_files_segment, dump_preset_yaml_document, read_preset_yaml_tree

CADNSDI_BASE = "https://gis.blm.gov/arcgis/rest/services/Cadastral/BLM_Natl_PLSS_CadNSDI/MapServer"

PLSS_MLRS_LOC_CACHE_FORMAT = "plss_mlrs_loc_cache/v1"
PLSS_MLRS_LOC_CACHE_SUBDIR = "plss_mlrs"
PLSS_MLRS_LOC_CACHE_BASENAME = "by_loc.json"


def loc_stamp(lat: float, lon: float) -> str:
    """Stable string for a site ``loc``; cache key for CadNSDI PLSS/MLRS."""
    return f"{lat:.6f},{lon:.6f}"


def plss_mlrs_loc_cache_path(cache_base: Path) -> Path:
    return Path(cache_base).expanduser().resolve() / PLSS_MLRS_LOC_CACHE_SUBDIR / PLSS_MLRS_LOC_CACHE_BASENAME


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


def cadnsdi_slugs_needing_refresh(
    preset: Preset,
    cached_locs: set[str],
    *,
    force_all: bool,
) -> set[str]:
    if force_all:
        return set(preset.sites.keys())
    need: set[str] = set()
    for slug, ent in preset.sites.items():
        st = loc_stamp(ent.lat, ent.lon)
        if st not in cached_locs:
            need.add(slug)
    return need


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


def plss_mlrs_for_point(lon: float, lat: float) -> tuple[str, str]:
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


def populate_preset_plss_mlrs_file(
    preset_path: Path,
    *,
    site_slugs: set[str],
    request_delay_s: float = 0.12,
    log_fp: Any = sys.stdout,
    loc_cache: dict[str, dict[str, str]] | None = None,
    force_network: bool = False,
    progress: Callable[[str], None] | None = None,
) -> None:
    """Update ``plss`` / ``mlrs`` in ``preset_path`` for ``site_slugs`` from coordinates (CadNSDI).

    Saves the preset after each site so a crash mid-batch does not lose progress.

    When ``loc_cache`` is set, hits are served from cache; misses are fetched and stored.
    """
    preset_path = preset_path.expanduser()
    yaml_rt, root = read_preset_yaml_tree(preset_path)
    sites_raw = root.get("sites")
    if sites_raw is None:
        return

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
        _emit(f"PLSS/MLRS: [{i}/{total}] {slug} …")
        try:
            cached = (loc_cache or {}).get(stamp)
            from_network = False
            if cached is not None and not force_network:
                plss, mlrs = cached.get("plss", ""), cached.get("mlrs", "")
            else:
                plss, mlrs = plss_mlrs_for_point(lon, lat)
                from_network = True
                if loc_cache is not None:
                    loc_cache[stamp] = {"plss": plss or "", "mlrs": mlrs or ""}
            ent["plss"] = plss or None
            ent["mlrs"] = mlrs or None
            dump_preset_yaml_document(yaml_rt, root, preset_path)
        except Exception as e:
            _emit(f"  ERROR {slug}: {e}")
            ent["plss"] = None
            ent["mlrs"] = None
            dump_preset_yaml_document(yaml_rt, root, preset_path)
            from_network = False
        if request_delay_s > 0 and from_network:
            time.sleep(request_delay_s)


def _seed_loc_cache_from_preset(preset: Preset, loc_cache: dict[str, dict[str, str]]) -> None:
    for ent in preset.sites.values():
        if not ent.plss and not ent.mlrs:
            continue
        stamp = loc_stamp(ent.lat, ent.lon)
        if stamp not in loc_cache:
            loc_cache[stamp] = {"plss": ent.plss or "", "mlrs": ent.mlrs or ""}


def maybe_refresh_plss_mlrs_for_bundle(
    *,
    preset_path: Path,
    cache_base: Path,
    preset: Preset,
    force_all: bool,
    skip_network: bool,
    verbose_log: Callable[[str], None] | None,
) -> None:
    """Update preset ``plss`` / ``mlrs`` via CadNSDI when ``loc`` is not yet cached (or ``force_all``).

    Cached by lat/lon under ``{cache_base}/plss_mlrs/by_loc.json`` — independent of bundle AOI digest.
    """
    if skip_network:
        if verbose_log:
            verbose_log("plss/mlrs: skip (--no-plss-fetch)")
        return

    cache_base = Path(cache_base).expanduser().resolve()
    preset_path = Path(preset_path).expanduser().resolve()
    loc_cache = read_plss_mlrs_loc_cache(cache_base)
    before_len = len(loc_cache)
    _seed_loc_cache_from_preset(preset, loc_cache)
    need = cadnsdi_slugs_needing_refresh(preset, set(loc_cache), force_all=force_all)

    if not need:
        if len(loc_cache) > before_len:
            write_plss_mlrs_loc_cache(cache_base, loc_cache)
        if verbose_log:
            verbose_log("plss/mlrs: skip (all site locs cached)")
        return

    print(f"plss/mlrs: CadNSDI refresh for {len(need)} site(s)", flush=True)

    populate_preset_plss_mlrs_file(
        preset_path,
        site_slugs=need,
        loc_cache=loc_cache,
        force_network=force_all,
        progress=verbose_log,
    )

    write_plss_mlrs_loc_cache(cache_base, loc_cache)
